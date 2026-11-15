"""Per-track time series and the interpretable motion features derived from them."""
from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from statistics import median

from ..types import Box


class TrackHistory:
    """Short rolling history of one track.

    Centres are stored *camera-motion compensated* (cumulative global shift subtracted), so
    speeds describe motion in the scene rather than motion of the camera. Raw boxes are kept
    for overlap tests, which compare two tracks at the same instant and need no compensation.
    """

    __slots__ = ("ts", "x", "y", "h", "obs", "boxes")

    def __init__(self) -> None:
        self.ts: list[float] = []
        self.x: list[float] = []
        self.y: list[float] = []
        self.h: list[float] = []
        self.obs: list[bool] = []
        self.boxes: list[Box] = []

    def __len__(self) -> int:
        return len(self.ts)

    def add(self, ts: float, box: Box, observed: bool, offset: tuple[float, float]) -> None:
        self.ts.append(ts)
        self.x.append((box[0] + box[2]) * 0.5 - offset[0])
        self.y.append((box[1] + box[3]) * 0.5 - offset[1])
        self.h.append(box[3] - box[1])
        self.obs.append(observed)
        self.boxes.append(box)

    def prune(self, before_ts: float) -> None:
        k = bisect_left(self.ts, before_ts)
        if k > 0:
            for name in self.__slots__:
                del getattr(self, name)[:k]

    def prepend(self, other: "TrackHistory") -> None:
        """Adopt an earlier history (used when an object track is re-linked to a new ID)."""
        if not other.ts:
            return
        cut = bisect_left(other.ts, self.ts[0]) if self.ts else len(other.ts)
        for name in self.__slots__:
            setattr(self, name, getattr(other, name)[:cut] + getattr(self, name))

    @property
    def first_ts(self) -> float:
        return self.ts[0] if self.ts else math.inf

    @property
    def last_ts(self) -> float:
        return self.ts[-1] if self.ts else -math.inf

    def _idx(self, t: float) -> int:
        """Index of the last sample at or before ``t``, clamped to the first sample."""
        return max(bisect_right(self.ts, t + 1e-9) - 1, 0)

    def pos(self, t: float) -> tuple[float, float]:
        i = self._idx(t)
        return self.x[i], self.y[i]

    def box_at(self, t: float) -> Box:
        return self.boxes[self._idx(t)]

    def net_speed(self, t0: float, t1: float) -> float:
        """Straight-line displacement / elapsed time between two instants (pixels per second).

        Net displacement is used instead of path length so that box jitter does not look
        like movement.
        """
        if not self.ts:
            return 0.0
        i, j = self._idx(t0), self._idx(t1)
        span = self.ts[j] - self.ts[i]
        if j <= i or span <= 1e-6:
            return 0.0
        return math.hypot(self.x[j] - self.x[i], self.y[j] - self.y[i]) / span

    def velocity(self, t0: float, t1: float) -> tuple[float, float]:
        if not self.ts:
            return 0.0, 0.0
        i, j = self._idx(t0), self._idx(t1)
        span = self.ts[j] - self.ts[i]
        if j <= i or span <= 1e-6:
            return 0.0, 0.0
        return (self.x[j] - self.x[i]) / span, (self.y[j] - self.y[i]) / span

    def max_window_speed(self, t0: float, t1: float, window: float) -> float:
        """Largest net speed over any ~``window``-long stretch inside [t0, t1]."""
        if not self.ts:
            return 0.0
        lo, hi = self._idx(t0), self._idx(t1)
        best = 0.0
        for i in range(lo, hi):
            for j in range(i + 1, hi + 1):
                span = self.ts[j] - self.ts[i]
                if span >= window * 0.8:
                    best = max(best, math.hypot(self.x[j] - self.x[i], self.y[j] - self.y[i]) / span)
                    break
        return best

    def median_h(self, t0: float, t1: float) -> float | None:
        vals = [h for t, h, o in zip(self.ts, self.h, self.obs) if t0 <= t <= t1 and o]
        return median(vals) if vals else None

    def observed_frac(self, t0: float, t1: float) -> float:
        flags = [o for t, o in zip(self.ts, self.obs) if t0 <= t <= t1]
        return sum(flags) / len(flags) if flags else 0.0

    def samples_between(self, t0: float, t1: float) -> range:
        return range(bisect_left(self.ts, t0), bisect_right(self.ts, t1))
