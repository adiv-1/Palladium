"""Runtime measurements: rates, latencies and process resource use. Measured, never estimated."""
from __future__ import annotations

import os
import threading
import time
from collections import deque

import numpy as np
import psutil


class Metrics:
    def __init__(self, window: int = 600) -> None:
        self._lock = threading.Lock()
        self._proc = psutil.Process(os.getpid())
        self._proc.cpu_percent(None)                      # prime the counter
        self._inference_ms: deque[float] = deque(maxlen=window)
        self._total_ms: deque[float] = deque(maxlen=window)
        self._lag_ms: deque[float] = deque(maxlen=window)
        self._frame_walls: deque[float] = deque(maxlen=window)
        self._cpu: deque[float] = deque(maxlen=window)
        self._pose_ms: deque[float] = deque(maxlen=window)
        self.pose_frames = 0
        self.pose_people = 0
        self._last_cpu_sample = time.monotonic()
        self.frames_analysed = 0
        self.events_emitted = 0
        self.peak_rss_mb = 0.0
        self.started = time.time()

    def record_frame(self, inference_ms: float, total_ms: float, lag_ms: float) -> None:
        now = time.monotonic()
        with self._lock:
            self.frames_analysed += 1
            self._inference_ms.append(inference_ms)
            self._total_ms.append(total_ms)
            self._lag_ms.append(lag_ms)
            self._frame_walls.append(now)
            if now - self._last_cpu_sample >= 1.0:
                self._last_cpu_sample = now
                self._cpu.append(self._proc.cpu_percent(None))   # percent of ONE core
                self.peak_rss_mb = max(self.peak_rss_mb, self._rss_mb())

    def record_pose(self, ms: float, people: int) -> None:
        with self._lock:
            self._pose_ms.append(ms)
            self.pose_frames += 1
            self.pose_people += people

    def _rss_mb(self) -> float:
        return self._proc.memory_info().rss / (1024 * 1024)

    def _peak_os_mb(self) -> float:
        info = self._proc.memory_info()
        peak = getattr(info, "peak_wset", None)           # Windows reports a true peak
        return peak / (1024 * 1024) if peak else max(self.peak_rss_mb, self._rss_mb())

    @staticmethod
    def _stats(values) -> dict[str, float | None]:
        if not values:
            return {"mean": None, "p50": None, "p95": None, "max": None}
        a = np.asarray(values, dtype=float)
        return {"mean": round(float(a.mean()), 2), "p50": round(float(np.percentile(a, 50)), 2),
                "p95": round(float(np.percentile(a, 95)), 2), "max": round(float(a.max()), 2)}

    def snapshot(self) -> dict:
        with self._lock:
            walls = list(self._frame_walls)
            recent = [w for w in walls if walls and walls[-1] - w <= 5.0]
            fps = (len(recent) - 1) / (recent[-1] - recent[0]) if len(recent) > 1 and recent[-1] > recent[0] else 0.0
            cpu = list(self._cpu)
            cores = psutil.cpu_count(logical=True) or 1
            return {
                "frames_analysed": self.frames_analysed,
                "events_emitted": self.events_emitted,
                "analysed_fps": round(fps, 2),
                "inference_ms": self._stats(self._inference_ms),
                "frame_total_ms": self._stats(self._total_ms),
                "pose_ms_when_run": self._stats(self._pose_ms),
                "pose_frames": self.pose_frames, "pose_people": self.pose_people,
                "capture_to_result_ms": self._stats(self._lag_ms),
                "cpu_percent_of_one_core": {"mean": round(float(np.mean(cpu)), 1) if cpu else None,
                                            "max": round(float(np.max(cpu)), 1) if cpu else None},
                "cpu_percent_of_machine": round(float(np.mean(cpu)) / cores, 1) if cpu else None,
                "logical_cores": cores,
                "rss_mb": round(self._rss_mb(), 1),
                "peak_rss_mb": round(self._peak_os_mb(), 1),
                "uptime_s": round(time.time() - self.started, 1),
            }
