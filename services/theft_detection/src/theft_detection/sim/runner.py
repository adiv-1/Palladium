"""Run scenarios through the real tracker + event engine using mock detections, and score them.

"Trace-level" evaluation: the object detector is replaced by a noise model, everything
downstream (tracking, association, candidate generation, verification, de-duplication) is the
production code path.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..config import Config
from ..events.engine import SEVERITY_REVIEW, EventEngine
from ..tracking.bytetrack import ByteTracker
from ..types import Decision, RawEvent
from .world import FRAME_H, FRAME_W, ObsParams, Scenario, observe


@dataclass
class TraceResult:
    scenario: Scenario
    events: list[RawEvent]
    decisions: list[Decision]
    frames: int


@dataclass
class Score:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    delays: list[float] = field(default_factory=list)
    false_alerts: list[tuple[str, str]] = field(default_factory=list)   # (scenario, event_type)
    missed: list[tuple[str, str]] = field(default_factory=list)
    info_events: int = 0
    negative_seconds: float = 0.0
    total_seconds: float = 0.0
    fp_negative: int = 0

    def add(self, other: "Score") -> None:
        self.tp += other.tp
        self.fp += other.fp
        self.fn += other.fn
        self.delays += other.delays
        self.false_alerts += other.false_alerts
        self.missed += other.missed
        self.info_events += other.info_events
        self.fp_negative += other.fp_negative
        self.negative_seconds += other.negative_seconds
        self.total_seconds += other.total_seconds

    @property
    def precision(self) -> float | None:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else None

    @property
    def recall(self) -> float | None:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else None

    @property
    def false_alerts_per_hour(self) -> float | None:
        """False alerts per hour of footage that contains no labelled event."""
        return self.fp_negative / (self.negative_seconds / 3600.0) if self.negative_seconds else None

    def summary(self) -> dict:
        d = sorted(self.delays)
        return {
            "true_positives": self.tp, "false_positives": self.fp, "false_negatives": self.fn,
            "precision": None if self.precision is None else round(self.precision, 3),
            "recall": None if self.recall is None else round(self.recall, 3),
            "false_alerts_in_negative_footage": self.fp_negative,
            "negative_footage_hours": round(self.negative_seconds / 3600.0, 3),
            "false_alerts_per_hour_negative": (None if self.false_alerts_per_hour is None
                                               else round(self.false_alerts_per_hour, 2)),
            "median_time_to_detection_s": round(d[len(d) // 2], 2) if d else None,
            "p90_time_to_detection_s": round(d[min(len(d) - 1, int(0.9 * len(d)))], 2) if d else None,
            "info_events": self.info_events,
        }


def run_trace(sc: Scenario, cfg: Config | None = None, fps: float = 10.0,
              params: ObsParams | None = None, seed: int = 0) -> TraceResult:
    cfg = cfg or Config()
    params = params or ObsParams()
    rng = np.random.default_rng(seed)
    tracker = ByteTracker(cfg.tracker)
    engine = EventEngine(cfg.engine, list(cfg.zones) + list(sc.zones))
    events: list[RawEvent] = []
    step = 1.0 / fps
    n = int(sc.duration * fps) + 1
    last_t = -step
    frames = 0
    for k in range(n):
        t = k * step
        if params.frame_loss and rng.random() < params.frame_loss:
            continue
        dets, gm = observe(sc, t, rng, params, t - last_t)
        last_t = t
        tracks = tracker.update(dets, t)
        events += engine.update(t, tracks, gm, (FRAME_W, FRAME_H), detections=dets)
        frames += 1
    return TraceResult(sc, events, list(engine.decisions), frames)


def score_result(res: TraceResult, early_tol: float = 0.75, late_tol: float = 6.0) -> Score:
    """Event-level matching: each label may match one alert of the same type detected within
    [onset - early_tol, onset + late_tol]; every other review-severity alert is a false alert."""
    sc = res.scenario
    s = Score(total_seconds=sc.duration)
    alerts = [e for e in res.events if e.severity == SEVERITY_REVIEW]
    s.info_events = len(res.events) - len(alerts)
    used: set[int] = set()
    for label in sc.labels:
        hit = next((i for i, e in enumerate(alerts)
                    if i not in used and e.event_type == label.event_type
                    and label.onset - early_tol <= e.detected_ts <= label.onset + late_tol), None)
        if hit is None:
            s.fn += 1
            s.missed.append((sc.name, label.event_type))
        else:
            used.add(hit)
            s.tp += 1
            s.delays.append(alerts[hit].detected_ts - label.onset)
    extra = [e for i, e in enumerate(alerts) if i not in used]
    s.fp = len(extra)
    s.false_alerts = [(sc.name, e.event_type) for e in extra]
    if not sc.labels:
        s.negative_seconds = sc.duration
        s.fp_negative = len(extra)
    return s
