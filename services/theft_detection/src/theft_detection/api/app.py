"""Local HTTP API and minimal review UI.

Binds to 127.0.0.1 by default. It exposes events for a consumer to poll, lets an authorised
reviewer record a decision, and never contacts anyone on its own.
"""
from __future__ import annotations

import asyncio
import re
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .. import __version__
from ..config import Config
from ..detection.base import Detector, build_detector
from ..events.schema import SCHEMA_VERSION, Event, ReviewRequest, event_json_schema
from ..events.store import EventStore
from ..monitoring.trials import SCENARIOS, TrialRecorder
from ..pipeline import TheftDetector
from ..sim import scenarios

_STATIC = Path(__file__).parent / "static"
_EVIDENCE_NAME = re.compile(r"^evt_[0-9a-f]{16}\.(jpg|webm)$")
_VIDEO_EXT = {".mp4", ".avi", ".mov", ".mkv", ".webm"}


class SourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    uri: str = Field(min_length=1, max_length=500)
    source_id: str | None = Field(default=None, min_length=1, max_length=80)
    analysis_fps: float | None = Field(default=None, gt=0, le=60)
    pace: str | None = Field(default=None, pattern="^(realtime|fast)$")
    loop: bool | None = None
    model: str | None = Field(default=None, max_length=200)


class TrialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario: str = Field(min_length=1, max_length=60)
    expected: str | None = Field(default=None, pattern="^(alert|no_alert|any)$")
    note: str = Field(default="", max_length=500)


class Service:
    """Owns the running detector; the event store outlives source switches."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.store = EventStore(cfg.events.max_stored)
        self.trials = TrialRecorder(cfg.events.trials_path)
        self.detector: TheftDetector | None = None
        self._models: dict[tuple[str, str], Detector] = {}
        self.startup_error: str | None = None
        self._lock = threading.Lock()

    def _is_synthetic(self, uri: str) -> bool:
        return uri.startswith("synthetic:") or Path(uri).name.startswith("synthetic_")

    def resolve_uri(self, uri: str) -> str:
        """Only sources the operator has made available may be opened through the API."""
        uri = uri.strip()
        if uri.startswith("synthetic:") or uri.isdigit():
            return uri
        if "://" in uri:
            if not self.cfg.api.allow_network_sources:
                raise HTTPException(403, "network video sources are disabled (api.allow_network_sources)")
            return uri
        root = Path(self.cfg.api.demo_dir).resolve()
        path = (root / uri).resolve() if not Path(uri).is_absolute() else Path(uri).resolve()
        if root not in path.parents or path.suffix.lower() not in _VIDEO_EXT or not path.is_file():
            raise HTTPException(404, f"video not found under the demo directory: {uri}")
        return str(path)

    def start(self, req: SourceRequest) -> dict:
        uri = self.resolve_uri(req.uri)
        with self._lock:
            self._stop_locked()
            source = self.cfg.source.model_copy(update={
                "uri": uri,
                **({"source_id": req.source_id} if req.source_id else {}),
                **({"analysis_fps": req.analysis_fps} if req.analysis_fps else {}),
                **({"pace": req.pace} if req.pace else {}),
                **({"loop": req.loop} if req.loop is not None else {}),
            })
            det_cfg = self.cfg.detector
            if self._is_synthetic(uri):
                det_cfg = det_cfg.model_copy(update={"backend": "color_blob"})
            elif req.model:
                det_cfg = det_cfg.model_copy(update={"model": req.model})
            cfg = self.cfg.model_copy(update={"source": source, "detector": det_cfg})
            key = (det_cfg.backend, det_cfg.model)
            if key not in self._models:
                try:
                    self._models[key] = build_detector(det_cfg)   # loaded once, reused across sources
                except (FileNotFoundError, ValueError, KeyError) as exc:
                    raise HTTPException(400, f"detector unavailable: {exc}") from exc
            self.detector = TheftDetector(cfg, detector=self._models[key], store=self.store, trials=self.trials)
            self.detector.render_preview = True
            self.detector.start()
            return {"started": True, "uri": req.uri, "detector": self._models[key].name}

    def _stop_locked(self) -> None:
        if self.detector is not None:
            self.detector.stop()

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def sources(self) -> dict:
        root = Path(self.cfg.api.demo_dir)
        files = sorted(str(p.relative_to(root)).replace("\\", "/")
                       for p in root.rglob("*") if p.suffix.lower() in _VIDEO_EXT
                       and "evidence" not in p.parts)[:100] if root.is_dir() else []
        return {"videos": files, "synthetic": [f"synthetic:{n}" for n in scenarios.BY_NAME],
                "webcams": ["0"], "network_sources_allowed": self.cfg.api.allow_network_sources}


def _placeholder(text: str) -> bytes:
    img = np.full((360, 640, 3), 32, np.uint8)
    cv2.putText(img, text, (24, 186), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (190, 190, 190), 1, cv2.LINE_AA)
    return cv2.imencode(".jpg", img)[1].tobytes()


def create_app(cfg: Config | None = None, autostart: SourceRequest | None = None) -> FastAPI:
    """``autostart`` starts that source as soon as the server is up (used by ``theft-detect live``).
    A source that fails to open is reported through /api/v1/status, never replaced."""
    cfg = cfg or Config()
    svc = Service(cfg)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if autostart is not None:
            try:
                svc.start(autostart)
            except HTTPException as exc:
                svc.startup_error = str(exc.detail)
        yield
        svc.stop()

    app = FastAPI(title="SafeSC theft-detection module", version=__version__, lifespan=lifespan)
    app.state.service = svc

    def auth(request: Request) -> None:
        token = cfg.api.token
        if not token:
            return
        header = request.headers.get("authorization", "")
        supplied = header[7:] if header.lower().startswith("bearer ") else request.query_params.get("token", "")
        if supplied != token:
            raise HTTPException(401, "missing or invalid API token")

    guard = [Depends(auth)]

    @app.get("/health")
    def health() -> dict:
        d = svc.detector
        return {"status": "ok", "version": __version__, "schema_version": SCHEMA_VERSION,
                "pipeline": d.state.status if d else "idle"}

    @app.get("/api/v1/status", dependencies=guard)
    def status() -> dict:
        d = svc.detector
        if d is None:
            return {"status": "error" if svc.startup_error else "idle", "detail": svc.startup_error or "",
                    "metrics": None, "events_stored": len(svc.store), "trial": svc.trials.active}
        return {**d.status(), "events_stored": len(svc.store), "trial": svc.trials.active}

    @app.get("/api/v1/sources", dependencies=guard)
    def sources() -> dict:
        return svc.sources()

    @app.post("/api/v1/source", dependencies=guard)
    def start_source(req: SourceRequest) -> dict:
        return svc.start(req)

    @app.post("/api/v1/stop", dependencies=guard)
    def stop() -> dict:
        svc.stop()
        return {"stopped": True}

    @app.get("/api/v1/events", dependencies=guard, response_model=list[Event])
    def list_events(status: str | None = Query(None, pattern="^(pending|confirmed|dismissed|uncertain)$"),
                    severity: str | None = Query(None, pattern="^(info|review)$"),
                    since: datetime | None = None, limit: int = Query(100, ge=1, le=500)) -> list[Event]:
        return svc.store.list(status=status, severity=severity, since=since, limit=limit)

    @app.get("/api/v1/events/{event_id}", dependencies=guard, response_model=Event)
    def get_event(event_id: str) -> Event:
        ev = svc.store.get(event_id)
        if ev is None:
            raise HTTPException(404, f"unknown event: {event_id}")
        return ev

    @app.post("/api/v1/events/{event_id}/review", dependencies=guard, response_model=Event)
    def review_event(event_id: str, req: ReviewRequest) -> Event:
        try:
            return svc.store.review(event_id, req.status, req.note)
        except KeyError:
            raise HTTPException(404, f"unknown event: {event_id}") from None

    @app.get("/api/v1/decisions", dependencies=guard)
    def decisions(limit: int = Query(30, ge=1, le=200)) -> list[dict]:
        d = svc.detector
        if d is None:
            return []
        items = list(d.engine.decisions)[-limit:][::-1]
        return [{"stream_time_s": round(x.ts, 2), "kind": x.kind, "outcome": x.outcome,
                 "reason": x.reason, "features": x.features} for x in items]

    @app.get("/api/v1/schema", dependencies=guard)
    def schema() -> dict:
        return event_json_schema()

    @app.get("/api/v1/evidence/{name}", dependencies=guard)
    def evidence(name: str) -> FileResponse:
        if not _EVIDENCE_NAME.match(name):
            raise HTTPException(400, "invalid evidence reference")
        path = Path(cfg.evidence.directory) / name
        if not path.is_file():
            raise HTTPException(404, "evidence not found (capture disabled, expired, or not written yet)")
        return FileResponse(path)

    @app.get("/api/v1/frame.jpg", dependencies=guard)
    def latest_frame() -> Response:
        d = svc.detector
        jpeg = d.latest_jpeg() if d is not None else None
        if jpeg is None:
            raise HTTPException(404, "no frame yet")
        return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/v1/state", dependencies=guard)
    def live_state() -> dict:
        d = svc.detector
        return {"engine": d.engine.snapshot() if d is not None else None, "trial": svc.trials.active}

    @app.get("/api/v1/scenarios", dependencies=guard)
    def list_scenarios() -> dict:
        return SCENARIOS

    @app.get("/api/v1/trials", dependencies=guard)
    def list_trials(limit: int = Query(20, ge=1, le=50)) -> list[dict]:
        return svc.trials.completed[-limit:][::-1]

    @app.post("/api/v1/trials/start", dependencies=guard)
    def start_trial(req: TrialRequest) -> dict:
        d = svc.detector
        if d is None or d.state.status != "running":
            raise HTTPException(409, "start a video source before starting a trial")
        try:
            return svc.trials.start(req.scenario, d.state.stream_time_s, req.expected, req.note)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/v1/trials/stop", dependencies=guard)
    def stop_trial() -> dict:
        d = svc.detector
        ts = d.state.stream_time_s if d is not None else 0.0
        decisions = list(d.engine.decisions) if d is not None else []
        snapshot = d.engine.snapshot() if d is not None else {"open_candidates": []}
        metrics = d.metrics.snapshot() if d is not None else {}
        try:
            return svc.trials.stop(ts, decisions, snapshot["open_candidates"], metrics)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/v1/stream.mjpg", dependencies=guard)
    async def stream() -> StreamingResponse:
        idle = _placeholder("No source running - choose one and press Start")

        async def frames():
            last: bytes | None = None
            while True:
                d = svc.detector
                jpeg = d.latest_jpeg() if d is not None else None
                jpeg = jpeg or idle
                if jpeg is not last:
                    last = jpeg
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
                await asyncio.sleep(0.05)

        return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (_STATIC / "index.html").read_text(encoding="utf-8")

    return app
