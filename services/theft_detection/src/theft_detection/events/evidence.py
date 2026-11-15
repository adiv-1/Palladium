"""Optional, short, event-centred evidence capture.

Disabled by default. When enabled, a few seconds of already-analysed frames are kept in memory
as JPEG bytes (a small ring buffer); only when an event is emitted are the frames around it
written to disk, as one still image and one short clip. Nothing continuous is ever recorded,
and files older than the retention period are deleted.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from ..config import EvidenceConfig


@dataclass
class _Pending:
    event_id: str
    detected_ts: float
    frames: list[tuple[float, bytes]]


class EvidenceRecorder:
    def __init__(self, cfg: EvidenceConfig) -> None:
        self.cfg = cfg
        self.dir = Path(cfg.directory)
        self._ring: deque[tuple[float, bytes]] = deque()
        self._pending: list[_Pending] = []
        self._last_purge = 0.0
        if cfg.enabled:
            self.dir.mkdir(parents=True, exist_ok=True)
            self.purge()

    @property
    def enabled(self) -> bool:
        return self.cfg.enabled

    def add_frame(self, ts: float, image: np.ndarray) -> list[tuple[str, str | None, str | None]]:
        """Buffer one (annotated) frame. Returns (event_id, clip, frame) for any clip finished."""
        if not self.cfg.enabled:
            return []
        h, w = image.shape[:2]
        if w > self.cfg.max_width:
            image = cv2.resize(image, (self.cfg.max_width, int(h * self.cfg.max_width / w)),
                               interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, self.cfg.jpeg_quality])
        if not ok:
            return []
        item = (ts, buf.tobytes())
        self._ring.append(item)
        while self._ring and self._ring[0][0] < ts - self.cfg.pre_s:
            self._ring.popleft()
        done = []
        for p in list(self._pending):
            p.frames.append(item)
            if ts >= p.detected_ts + self.cfg.post_s:
                self._pending.remove(p)
                done.append(self._write(p))
        if time.time() - self._last_purge > 600:
            self.purge()
        return done

    def on_event(self, event_id: str, detected_ts: float) -> None:
        if self.cfg.enabled:
            self._pending.append(_Pending(event_id, detected_ts, list(self._ring)))

    def flush(self) -> list[tuple[str, str | None, str | None]]:
        """Write any clip still waiting for its post-event frames (called at end of stream)."""
        done = [self._write(p) for p in self._pending]
        self._pending.clear()
        return done

    def _write(self, p: _Pending) -> tuple[str, str | None, str | None]:
        if not p.frames:
            return p.event_id, None, None
        key = min(p.frames, key=lambda f: abs(f[0] - p.detected_ts))
        frame_path = self.dir / f"{p.event_id}.jpg"
        frame_path.write_bytes(key[1])
        clip_path: Path | None = self.dir / f"{p.event_id}.webm"
        first = cv2.imdecode(np.frombuffer(p.frames[0][1], np.uint8), cv2.IMREAD_COLOR)
        span = p.frames[-1][0] - p.frames[0][0]
        fps = max((len(p.frames) - 1) / span, 1.0) if span > 0 else 5.0
        writer = cv2.VideoWriter(str(clip_path), cv2.VideoWriter_fourcc(*"VP80"), fps,
                                 (first.shape[1], first.shape[0]))
        if writer.isOpened():
            for _, data in p.frames:
                img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                if img is not None and img.shape == first.shape:
                    writer.write(img)
            writer.release()
        else:
            writer.release()
            clip_path = None
        return p.event_id, (clip_path.name if clip_path else None), frame_path.name

    def purge(self, everything: bool = False) -> int:
        """Delete evidence files older than the retention period (or all of them)."""
        self._last_purge = time.time()
        if not self.dir.is_dir():
            return 0
        cutoff = time.time() - self.cfg.retention_hours * 3600
        removed = 0
        for f in self.dir.iterdir():
            if f.is_file() and f.suffix in (".jpg", ".webm") and f.name.startswith("evt_"):
                if everything or f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
                    removed += 1
        return removed
