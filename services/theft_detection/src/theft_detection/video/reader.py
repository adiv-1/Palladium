"""Background frame reader with sampling and a bounded, drop-oldest queue.

The capture thread never waits for the analysis thread in ``realtime`` mode: if analysis is
slower than the sampled rate, the *oldest* queued frame is discarded, so latency stays bounded
instead of accumulating. In ``fast`` mode (evaluation/benchmarks) the reader blocks instead,
so every sampled frame is analysed and results are deterministic.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass

import cv2

from ..config import SourceConfig
from ..types import Frame
from .sources import VideoSource


@dataclass
class ReaderStats:
    frames_read: int = 0          # grabbed from the source
    frames_sampled: int = 0       # decoded and queued for analysis
    frames_skipped: int = 0       # not needed at the configured analysis rate (never decoded)
    frames_dropped: int = 0       # queued but discarded because analysis fell behind
    frames_invalid: int = 0
    ended: bool = False
    error: str | None = None


class FrameSampler:
    """Chooses which source frames to analyse so the average rate matches ``analysis_fps``."""

    def __init__(self, analysis_fps: float) -> None:
        self.period = 1.0 / analysis_fps
        self._due = 0.0

    def accept(self, ts: float) -> bool:
        if ts + 1e-6 < self._due:
            return False
        self._due += self.period
        if self._due <= ts:               # fell behind (slow source or dropped frames): resync
            self._due = ts + self.period
        return True


class FrameReader:
    def __init__(self, source: VideoSource, cfg: SourceConfig) -> None:
        self.source = source
        self.cfg = cfg
        self.stats = ReaderStats()
        self._queue: deque[Frame] = deque()
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "FrameReader":
        self._thread = threading.Thread(target=self._run, name="frame-reader", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self.source.close()

    def get(self, timeout: float = 1.0) -> Frame | None:
        """Next frame to analyse, or ``None`` on timeout / end of stream (check ``stats.ended``)."""
        with self._cond:
            if not self._queue and not self.stats.ended:
                self._cond.wait(timeout)
            if not self._queue:
                return None
            frame = self._queue.popleft()
            self._cond.notify_all()
            return frame

    @property
    def finished(self) -> bool:
        with self._cond:
            return self.stats.ended and not self._queue

    # ------------------------------------------------------------------------------------
    def _run(self) -> None:
        cfg, src, stats = self.cfg, self.source, self.stats
        sampler = FrameSampler(cfg.analysis_fps)
        realtime = cfg.pace == "realtime"
        t0_wall: float | None = None
        t0_ts = 0.0
        try:
            while not self._stop.is_set():
                grabbed = src.grab()
                if grabbed is None:
                    break
                index, ts = grabbed
                stats.frames_read += 1
                if realtime and not src.is_live:
                    # replay a file at its native speed, like a camera would deliver it
                    if t0_wall is None:
                        t0_wall, t0_ts = time.monotonic(), ts
                    delay = (ts - t0_ts) - (time.monotonic() - t0_wall)
                    if delay > 0 and self._stop.wait(delay):
                        break
                if not sampler.accept(ts):
                    stats.frames_skipped += 1
                    continue
                img = src.retrieve()
                if img is None:
                    stats.frames_invalid += 1
                    continue
                h, w = img.shape[:2]
                if w > cfg.max_width:
                    img = cv2.resize(img, (cfg.max_width, int(round(h * cfg.max_width / w))),
                                     interpolation=cv2.INTER_AREA)
                frame = Frame(image=img, index=index, ts=ts, wall_ts=time.time())
                with self._cond:
                    if realtime:
                        while len(self._queue) >= cfg.queue_size:
                            self._queue.popleft()        # drop the stalest frame
                            stats.frames_dropped += 1
                    else:
                        while len(self._queue) >= cfg.queue_size and not self._stop.is_set():
                            self._cond.wait(0.1)
                    self._queue.append(frame)
                    stats.frames_sampled += 1
                    self._cond.notify_all()
        except Exception as exc:  # surfaced through stats; the analysis loop ends cleanly
            stats.error = f"{type(exc).__name__}: {exc}"
        finally:
            with self._cond:
                stats.ended = True
                self._cond.notify_all()
