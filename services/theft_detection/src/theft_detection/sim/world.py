"""A tiny 2-D scene model used to script controlled scenarios.

People follow key-framed paths; bags are attached to a person, rest at a point, or move
between such anchors. The same scenario object drives three things:

* mock detections with configurable noise (``observe``) for trace-level evaluation,
* rendered synthetic video (``sim.render``) for the end-to-end demo,
* ground-truth event labels for scoring.

This is a *controlled test fixture*. It is not a model of real camera footage, and results
obtained on it are not real-world accuracy.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..config import ZoneConfig
from ..types import BAG, PERSON, Box, Detection

FRAME_W, FRAME_H = 1280, 720


def _smooth(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


class PathBuilder:
    """Key-framed foot position; speeds are in body heights per second."""

    def __init__(self, x: float, y: float, height: float, t: float = 0.0) -> None:
        self.h = height
        self.kf: list[tuple[float, float, float]] = [(t, x, y)]

    @property
    def t(self) -> float:
        return self.kf[-1][0]

    @property
    def xy(self) -> tuple[float, float]:
        return self.kf[-1][1], self.kf[-1][2]

    def stay(self, dur: float) -> "PathBuilder":
        t, x, y = self.kf[-1]
        self.kf.append((t + max(dur, 0.0), x, y))
        return self

    def until(self, t_abs: float) -> "PathBuilder":
        return self.stay(t_abs - self.t)

    def to(self, x: float, y: float, speed: float) -> "PathBuilder":
        t, x0, y0 = self.kf[-1]
        d = math.hypot(x - x0, y - y0)
        self.kf.append((t + d / max(speed * self.h, 1e-6), x, y))
        return self

    def by(self, dx: float, dy: float, speed: float) -> "PathBuilder":
        _, x0, y0 = self.kf[-1]
        return self.to(x0 + dx, y0 + dy, speed)


@dataclass
class Person:
    pid: str
    height: float
    path: list[tuple[float, float, float]]
    width_ratio: float = 0.36
    color: int = 0  # palette index for the renderer

    def feet(self, t: float) -> tuple[float, float]:
        kf = self.path
        if t <= kf[0][0]:
            return kf[0][1], kf[0][2]
        for (t0, x0, y0), (t1, x1, y1) in zip(kf, kf[1:]):
            if t <= t1:
                u = (t - t0) / (t1 - t0) if t1 > t0 else 1.0
                return x0 + (x1 - x0) * u, y0 + (y1 - y0) * u
        return kf[-1][1], kf[-1][2]

    def box(self, t: float) -> Box:
        x, y = self.feet(t)
        w = self.height * self.width_ratio
        return x - w / 2, y - self.height, x + w / 2, y


@dataclass
class Anchor:
    """Where a bag is: carried by a person (``side`` -1/+1 or ``back``) or resting at a point."""

    person: str | None = None
    style: str = "side"
    side: int = 1
    pos: tuple[float, float] | None = None


@dataclass
class Seg:
    t0: float
    t1: float
    anchor: Anchor


@dataclass
class Bag:
    bid: str
    label: str
    segs: list[Seg]
    size: tuple[float, float] = (0.22, 0.26)  # relative to ref_height
    ref_height: float = 240.0
    color: int = 0
    hidden: list[tuple[float, float]] = field(default_factory=list)  # intervals with the bag out of sight

    def is_hidden(self, t: float) -> bool:
        return any(a <= t <= b for a, b in self.hidden)


@dataclass
class Label:
    event_type: str
    onset: float


@dataclass
class Scenario:
    name: str
    kind: str                      # "positive" | "negative"
    description: str
    duration: float
    persons: list[Person]
    bags: list[Bag]
    labels: list[Label] = field(default_factory=list)
    zones: list[ZoneConfig] = field(default_factory=list)
    shake: tuple[float, float, float] | None = None  # (amplitude px, t_start, t_end)
    shake_seed: int = 0

    # ---- geometry at time t ---------------------------------------------------------
    def person(self, pid: str) -> Person:
        return next(p for p in self.persons if p.pid == pid)

    def _anchor_xy(self, bag: Bag, a: Anchor, t: float) -> tuple[float, float]:
        if a.person is None:
            assert a.pos is not None
            return a.pos
        p = self.person(a.person)
        x, y = p.feet(t)
        if a.style == "back":
            return x - a.side * 0.15 * p.height, y - 0.68 * p.height
        return x + a.side * 0.21 * p.height, y - 0.42 * p.height

    def bag_center(self, bag: Bag, t: float) -> tuple[float, float]:
        segs = bag.segs
        if t <= segs[0].t0:
            return self._anchor_xy(bag, segs[0].anchor, t)
        for i, s in enumerate(segs):
            if s.t0 <= t <= s.t1:
                return self._anchor_xy(bag, s.anchor, t)
            if i + 1 < len(segs) and s.t1 < t < segs[i + 1].t0:
                n = segs[i + 1]
                u = _smooth((t - s.t1) / (n.t0 - s.t1))
                ax, ay = self._anchor_xy(bag, s.anchor, t)
                bx, by = self._anchor_xy(bag, n.anchor, t)
                return ax + (bx - ax) * u, ay + (by - ay) * u
        return self._anchor_xy(bag, segs[-1].anchor, t)

    def bag_box(self, bag: Bag, t: float) -> Box:
        cx, cy = self.bag_center(bag, t)
        w, h = bag.size[0] * bag.ref_height, bag.size[1] * bag.ref_height
        return cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2

    def bag_carriers(self, bag: Bag, t: float) -> set[str]:
        """People currently holding the bag (both parties while it changes hands)."""
        out: set[str] = set()
        segs = bag.segs
        for i, s in enumerate(segs):
            if s.t0 <= t <= s.t1 and s.anchor.person:
                out.add(s.anchor.person)
            if i + 1 < len(segs) and s.t1 < t < segs[i + 1].t0:
                out.update(p for p in (s.anchor.person, segs[i + 1].anchor.person) if p)
        return out

    def camera_offset(self, t: float) -> tuple[float, float]:
        if not self.shake or not (self.shake[1] <= t <= self.shake[2]):
            return 0.0, 0.0
        amp = self.shake[0]
        k = self.shake_seed
        return (amp * math.sin(2 * math.pi * 5.3 * t + k) * math.cos(2 * math.pi * 1.7 * t + 2 * k),
                amp * 0.6 * math.sin(2 * math.pi * 6.1 * t + 3 * k))


@dataclass
class ObsParams:
    """Detector imperfection model applied to ground-truth boxes."""

    person_miss: float = 0.02
    bag_miss: float = 0.10
    bag_miss_fast_extra: float = 0.15   # extra miss rate while the bag moves fast (motion blur)
    jitter: float = 0.03                # box noise, fraction of box size
    spurious_bag_rate: float = 0.02     # per-frame chance of a one-frame false bag detection
    frame_loss: float = 0.0             # chance that a whole analysed frame is missing
    motion_noise_px: float = 0.5        # error of the global-motion estimate


def observe(sc: Scenario, t: float, rng: np.random.Generator, params: ObsParams,
            dt: float) -> tuple[list[Detection], tuple[float, float]]:
    """Mock detections at time ``t`` plus the camera shift since ``t - dt``."""
    ox, oy = sc.camera_offset(t)
    px, py = sc.camera_offset(t - dt)
    gm = (ox - px + rng.normal(0, params.motion_noise_px) * (1 if sc.shake else 0),
          oy - py + rng.normal(0, params.motion_noise_px) * (1 if sc.shake else 0))

    pboxes = {p.pid: p.box(t) for p in sc.persons}
    depth = {p.pid: p.feet(t)[1] for p in sc.persons}
    dets: list[Detection] = []

    def noisy(b: Box) -> Box:
        w, h = b[2] - b[0], b[3] - b[1]
        n = rng.normal(0, params.jitter, 4)
        return (b[0] + n[0] * w + ox, b[1] + n[1] * h + oy, b[2] + n[2] * w + ox, b[3] + n[3] * h + oy)

    def in_frame(b: Box) -> bool:
        vis_w = min(b[2], FRAME_W) - max(b[0], 0)
        vis_h = min(b[3], FRAME_H) - max(b[1], 0)
        return vis_w > 0.5 * (b[2] - b[0]) and vis_h > 0.5 * (b[3] - b[1])

    for p in sc.persons:
        b = pboxes[p.pid]
        if not in_frame(b):
            continue
        covered = max((_cover(b, pboxes[q.pid]) for q in sc.persons
                       if q.pid != p.pid and depth[q.pid] > depth[p.pid]), default=0.0)
        if covered >= 0.7 or rng.random() < params.person_miss:
            continue
        dets.append(Detection(noisy(b), float(np.clip(rng.normal(0.82, 0.06), 0.5, 0.97)), "person", PERSON))

    for bag in sc.bags:
        b = sc.bag_box(bag, t)
        if not in_frame(b):
            continue
        carriers = sc.bag_carriers(bag, t)
        bag_depth = max((depth[c] for c in carriers), default=b[3])
        hidden = any(_cover(b, pboxes[q.pid]) >= 0.5 for q in sc.persons
                     if q.pid not in carriers and depth[q.pid] > bag_depth + 1.0)
        c0, c1 = sc.bag_center(bag, t - dt), sc.bag_center(bag, t)
        speed = math.hypot(c1[0] - c0[0], c1[1] - c0[1]) / max(dt, 1e-6) / bag.ref_height
        miss = params.bag_miss + (params.bag_miss_fast_extra if speed > 1.5 else 0.0)
        if hidden or bag.is_hidden(t) or rng.random() < miss:
            continue
        dets.append(Detection(noisy(b), float(np.clip(rng.normal(0.58, 0.12), 0.31, 0.95)), bag.label, BAG))

    if rng.random() < params.spurious_bag_rate:
        x, y = rng.uniform(100, FRAME_W - 100), rng.uniform(200, FRAME_H - 60)
        dets.append(Detection((x, y, x + 50, y + 55), float(rng.uniform(0.3, 0.45)), "handbag", BAG))
    return dets, gm


def _cover(inner: Box, outer: Box) -> float:
    w = min(inner[2], outer[2]) - max(inner[0], outer[0])
    h = min(inner[3], outer[3]) - max(inner[1], outer[1])
    a = (inner[2] - inner[0]) * (inner[3] - inner[1])
    return (w * h / a) if w > 0 and h > 0 and a > 0 else 0.0
