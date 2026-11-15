"""``TheftDetector``: the Python-callable interface to the whole pipeline.

Three ways to use it, from most to least managed:

    det = TheftDetector(cfg, on_event=handle)      # handle(Event) is called for every event
    det.run()                                      # blocking: read cfg.source until it ends
    det.start(); ...; det.stop()                   # same, on a background thread

    result = det.process_frame(bgr_image, ts)      # you own the capture loop
    result = det.process_detections(dets, ts)      # you own detection too (traces, tests)

Nothing in this module imports from the SafeSC application.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
from pydantic import ValidationError

from . import __version__
from .config import Config, ZoneConfig
from .detection.base import Detector, NullDetector, build_detector
from .events.engine import EventEngine
from .events.evidence import EvidenceRecorder
from .events.schema import Event, Evidence, TrackRef
from .events.store import EventStore, JsonlSink, WebhookSink
from .interactions.motion import GlobalMotionEstimator
from .monitoring.metrics import Metrics
from .monitoring.trials import TrialRecorder
from .tracking.bytetrack import ByteTracker
from .types import BAG, PERSON, Detection, RawEvent, Track
from .video.reader import FrameReader
from .video.sources import VideoSource, open_source


@dataclass
class FrameResult:
    ts: float
    tracks: list[Track]
    events: list[Event]
    inference_ms: float = 0.0
    total_ms: float = 0.0
    detections: int = 0


@dataclass
class RunState:
    status: str = "idle"          # idle | starting | running | ended | error | stopped
    detail: str = ""
    source_uri: str = ""
    source_fps: float = 0.0
    stream_time_s: float = 0.0
    reader: dict = field(default_factory=dict)


class TheftDetector:
    def __init__(self, cfg: Config | None = None, detector: Detector | None = None,
                 on_event: Callable[[Event], None] | None = None,
                 store: EventStore | None = None, trials: TrialRecorder | None = None) -> None:
        self.cfg = cfg or Config()
        self._detector = detector
        self._owns_detector = detector is None
        self.tracker = ByteTracker(self.cfg.tracker)
        self.engine = EventEngine(self.cfg.engine, self.cfg.zones)
        self.motion = GlobalMotionEstimator()
        self.metrics = Metrics()
        # `store or ...` would be wrong: an empty EventStore is falsy because it defines __len__.
        self.store = store if store is not None else EventStore(self.cfg.events.max_stored)
        self.evidence = EvidenceRecorder(self.cfg.evidence)
        self.trials = trials if trials is not None else TrialRecorder(self.cfg.events.trials_path)
        self.store.subscribe(self.trials.on_event)
        self.state = RunState()
        self.events_rejected = 0
        self.simulated = False
        self.session_key = "manual"
        self._sinks: list = []
        if on_event is not None:
            self.store.subscribe(on_event)
        if self.cfg.events.jsonl_path:
            self.store.subscribe(JsonlSink(self.cfg.events.jsonl_path))
        if self.cfg.events.webhook.url:
            sink = WebhookSink(self.cfg.events.webhook)
            self._sinks.append(sink)
            self.store.subscribe(sink)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._latest_jpeg: bytes | None = None
        self._latest_lock = threading.Lock()
        self._frame_size: tuple[int, int] | None = None
        self.render_preview = False     # set by the API so annotated frames are encoded
        self._base_zones = list(self.cfg.zones)
        self._reader: FrameReader | None = None
        self._pose = None
        self.last_poses: dict = {}

    # ------------------------------------------------------------------------------------
    @property
    def detector(self) -> Detector:
        if self._detector is None:
            self._detector = build_detector(self.cfg.detector)
        return self._detector

    @property
    def model_version(self) -> str:
        return f"theft_detection/{__version__} detector={self.detector.name} tracker=bytetrack-np"

    def set_zones(self, zones: list) -> None:
        """Replace the configured zones (used when a synthetic scenario brings its own)."""
        self.cfg = self.cfg.model_copy(update={"zones": list(zones)})
        self.engine = EventEngine(self.cfg.engine, self.cfg.zones)

    def reset(self) -> None:
        """Forget all tracks and candidates (call between unrelated streams)."""
        self.tracker.reset()
        self.engine.reset()
        self.motion.reset()

    # ------------------------------------------------------------------------------------
    # frame-level API
    # ------------------------------------------------------------------------------------
    def process_frame(self, image: np.ndarray, ts: float, wall_ts: float | None = None) -> FrameResult:
        t0 = time.perf_counter()
        dets = self.detector.detect(image)
        inference_ms = (time.perf_counter() - t0) * 1000.0
        gm = self.motion.update(image)
        h, w = image.shape[:2]
        result = self._advance(dets, ts, gm, (w, h), wall_ts, image=image)
        result.inference_ms = inference_ms
        result.total_ms = (time.perf_counter() - t0) * 1000.0
        lag_ms = (time.time() - wall_ts) * 1000.0 if wall_ts else result.total_ms
        self.metrics.record_frame(inference_ms, result.total_ms, lag_ms)

        if self.evidence.enabled or self.render_preview:
            annotated = annotate(image, result.tracks, self.cfg, result.events, self.last_poses)
            if self.render_preview:
                ok, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 75])
                if ok:
                    with self._latest_lock:
                        self._latest_jpeg = buf.tobytes()
            for ev in result.events:
                self.evidence.on_event(ev.event_id, ts)
            for event_id, clip, frame in self.evidence.add_frame(ts, annotated):
                self.store.set_evidence(event_id, clip, frame)
        return result

    def process_detections(self, detections: list[Detection], ts: float,
                           global_motion: tuple[float, float] = (0.0, 0.0),
                           frame_size: tuple[int, int] | None = None, poses: dict | None = None) -> FrameResult:
        """Skip the detector: feed detections (and optionally body keypoints per track id) from
        another model, a trace, or a test."""
        return self._advance(detections, ts, global_motion, frame_size, None, poses=poses)

    def _poses_for(self, image: np.ndarray, tracks: list[Track]) -> dict:
        """Keypoints for people who are close to someone else (bounded; none in a sparse scene)."""
        cfg = self.cfg.pose
        people = [t for t in tracks if t.category == PERSON and t.lost_s == 0.0]
        near: list[Track] = []
        for p in people:
            ph = p.box[3] - p.box[1]
            cx, cy = (p.box[0] + p.box[2]) * 0.5, (p.box[1] + p.box[3]) * 0.5
            if any(q is not p and ((q.box[0] + q.box[2]) * 0.5 - cx) ** 2 + ((q.box[1] + q.box[3]) * 0.5 - cy) ** 2
                   <= (cfg.near * max(ph, q.box[3] - q.box[1])) ** 2 for q in people):
                near.append(p)
        near = sorted(near, key=lambda t: -(t.box[3] - t.box[1]))[:cfg.max_people]
        if not near:
            return {}
        if self._pose is None:
            from .detection.pose import RtmPoseEstimator
            self._pose = RtmPoseEstimator(cfg.model, self.cfg.detector.weights_dir, cfg.threads)
        t0 = time.perf_counter()
        kps = self._pose.estimate(image, [t.box for t in near])
        self.metrics.record_pose((time.perf_counter() - t0) * 1000.0, len(near))
        self.last_poses = {t.track_id: kp for t, kp in zip(near, kps)}
        return self.last_poses

    def _advance(self, dets: list[Detection], ts: float, gm: tuple[float, float],
                 frame_size: tuple[int, int] | None, wall_ts: float | None,
                 image: np.ndarray | None = None, poses: dict | None = None) -> FrameResult:
        self._frame_size = frame_size or self._frame_size
        tracks = self.tracker.update(dets, ts)
        self.trials.on_frame(ts, tracks)
        self.last_poses = {}
        if poses is None and image is not None and self.cfg.pose.enabled and self.cfg.detector.backend == "yolox_onnx":
            poses = self._poses_for(image, tracks)
        raw_events = self.engine.update(ts, tracks, gm, self._frame_size, detections=dets, poses=poses)
        events: list[Event] = []
        for raw in raw_events:
            ev = self._to_event(raw, wall_ts)
            if ev is not None and self.store.add(ev):
                events.append(ev)
                self.metrics.events_emitted += 1
        self.state.stream_time_s = ts
        return FrameResult(ts=ts, tracks=tracks, events=events, detections=len(dets))

    def _to_event(self, raw: RawEvent, wall_ts: float | None) -> Event | None:
        # Stable ID: the same stream replayed produces the same IDs, so consumers can de-duplicate.
        digest = hashlib.sha1(
            f"{self.cfg.source.source_id}|{self.session_key}|{raw.dedup_key}".encode()).hexdigest()[:16]
        try:
            return Event(
                event_id=f"evt_{digest}",
                source_id=self.cfg.source.source_id,
                event_type=raw.event_type,
                timestamp_utc=datetime.now(timezone.utc),
                evidence_score=raw.score,
                severity=raw.severity,
                observation=raw.observation,
                uncertainties=raw.uncertainties,
                evidence=Evidence(
                    onset_stream_s=round(max(raw.onset_ts, 0.0), 3),
                    detected_stream_s=round(max(raw.detected_ts, 0.0), 3),
                    detection_delay_s=round(max(raw.detected_ts - raw.onset_ts, 0.0), 3),
                    processing_lag_s=round(time.time() - wall_ts, 3) if wall_ts else None,
                    features=raw.features,
                    tracks=[TrackRef(**t) for t in raw.tracks],
                    object_label=raw.object_label, object_ref=raw.object_ref, zone_id=raw.zone_id,
                ),
                model_version=self.model_version,
                simulated=self.simulated,
            )
        except ValidationError:
            self.events_rejected += 1      # never let a malformed event crash detection
            return None

    # ------------------------------------------------------------------------------------
    # stream-level API
    # ------------------------------------------------------------------------------------
    def run(self, source: VideoSource | None = None, max_seconds: float | None = None,
            on_frame: Callable[[FrameResult], None] | None = None) -> RunState:
        """Blocking loop over ``cfg.source`` (or a provided source) until it ends or stop()."""
        state = self.state
        state.status, state.detail, state.source_uri = "starting", "", self.cfg.source.uri
        reader: FrameReader | None = None
        try:
            src = source or open_source(self.cfg.source)
            self.session_key = src.session_key or "manual"
            self.simulated = bool(src.simulated) or self.cfg.detector.backend != "yolox_onnx"
            state.source_fps = src.fps
            extra_zones = list(getattr(getattr(src, "scenario", None), "zones", []) or [])
            extra_zones += load_sidecar_zones(self.cfg.source.uri)
            if extra_zones or self.cfg.zones != self._base_zones:
                self.set_zones(list(self._base_zones) + extra_zones)
            self.reset()
            self.detector.warmup()
            reader = FrameReader(src, self.cfg.source).start()
            self._reader = reader
            state.status = "running"
            started = time.monotonic()
            while not self._stop.is_set():
                frame = reader.get(timeout=0.5)
                state.reader = vars(reader.stats).copy()
                if frame is None:
                    if reader.finished:
                        break
                    continue
                result = self.process_frame(frame.image, frame.ts, frame.wall_ts)
                if on_frame is not None:
                    on_frame(result)
                if max_seconds is not None and time.monotonic() - started >= max_seconds:
                    break
            for event_id, clip, frame_ref in self.evidence.flush():
                self.store.set_evidence(event_id, clip, frame_ref)
            state.reader = vars(reader.stats).copy()
            if reader.stats.error:
                state.status, state.detail = "error", reader.stats.error
            elif reader.stats.frames_read == 0 and not self._stop.is_set():
                # Opened but never delivered a frame: report it, never pass it off as an empty clip.
                state.status, state.detail = "error", "the source opened but delivered no frames"
            else:
                state.status = "stopped" if self._stop.is_set() else "ended"
        except Exception as exc:
            state.status, state.detail = "error", f"{type(exc).__name__}: {exc}"
        finally:
            if reader is not None:
                reader.stop()
        return state

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("detector is already running")
        self._stop.clear()
        self._thread = threading.Thread(target=self.run, name="theft-detector", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def close(self) -> None:
        self.stop()
        for sink in self._sinks:
            sink.close()
        if self._owns_detector and self._detector is not None:
            self._detector.close()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def latest_jpeg(self) -> bytes | None:
        with self._latest_lock:
            return self._latest_jpeg

    def status(self) -> dict:
        return {
            "status": self.state.status, "detail": self.state.detail,
            "source_id": self.cfg.source.source_id, "source_uri": self.state.source_uri,
            "source_fps": round(self.state.source_fps, 2),
            "target_analysis_fps": self.cfg.source.analysis_fps,
            "stream_time_s": round(self.state.stream_time_s, 2),
            "reader": self.state.reader,
            "queue_depth": len(self._reader._queue) if self._reader is not None else 0,
            "detector": getattr(self._detector, "name", self.cfg.detector.backend),
            "pose_model": getattr(self._pose, "name", None),
            "providers": getattr(self._detector, "providers", []),
            "simulated": self.simulated,
            "evidence_capture": self.evidence.enabled,
            "events_rejected": self.events_rejected,
            "duplicates_suppressed": self.store.duplicates_suppressed,
            "metrics": self.metrics.snapshot(),
        }


def load_sidecar_zones(uri: str) -> list[ZoneConfig]:
    """Zones for one staged clip: ``clip.mp4`` may be accompanied by ``clip.zones.json``."""
    if "://" in uri or uri.isdigit() or uri.startswith("synthetic:"):
        return []
    side = Path(uri).with_suffix(".zones.json")
    if not side.is_file():
        return []
    return [ZoneConfig.model_validate(z) for z in json.loads(side.read_text(encoding="utf-8"))]


# ----------------------------------------------------------------------------------------
_PERSON_COLOR = (80, 200, 120)
_BAG_COLOR = (40, 170, 255)
_ALERT_COLOR = (60, 60, 235)


_ARMS = ((5, 7), (7, 9), (6, 8), (8, 10), (5, 6))


def annotate(image: np.ndarray, tracks: list[Track], cfg: Config, events: list[Event] | None = None,
             poses: dict | None = None) -> np.ndarray:
    """Draw boxes, ephemeral track IDs and configured zones. No faces are located or cropped."""
    out = image.copy()
    h, w = out.shape[:2]
    for z in cfg.zones:
        pts = np.array([(int(x * w), int(y * h)) for x, y in z.polygon], dtype=np.int32)
        color = (60, 60, 235) if z.kind == "restricted" else (200, 160, 40)
        cv2.polylines(out, [pts], True, color, 2, cv2.LINE_AA)
        cv2.putText(out, f"{z.kind}: {z.zone_id}", (int(pts[:, 0].min()) + 4, int(pts[:, 1].min()) + 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)
    for t in tracks:
        if t.lost_s > 0.3:
            continue
        x1, y1, x2, y2 = (int(v) for v in t.box)
        color = _BAG_COLOR if t.category == BAG else _PERSON_COLOR
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 2 if t.lost_s == 0 else 1)
        tag = f"{t.label} #{t.track_id}" if t.category == BAG else f"person #{t.track_id}"
        cv2.putText(out, tag, (x1, max(y1 - 5, 12)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)
    for kp in (poses or {}).values():       # arms and wrists only
        for a, b in _ARMS:
            if kp[a, 2] >= 0.3 and kp[b, 2] >= 0.3:
                cv2.line(out, (int(kp[a, 0]), int(kp[a, 1])), (int(kp[b, 0]), int(kp[b, 1])), (0, 220, 255), 2, cv2.LINE_AA)
        for j in (9, 10):
            if kp[j, 2] >= 0.3:
                cv2.circle(out, (int(kp[j, 0]), int(kp[j, 1])), 4, (0, 0, 255), -1, cv2.LINE_AA)
    if events:
        cv2.rectangle(out, (0, 0), (w - 1, h - 1), _ALERT_COLOR, 4)
        cv2.putText(out, events[0].event_type.replace("_", " "), (10, 26),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, _ALERT_COLOR, 2, cv2.LINE_AA)
    return out
