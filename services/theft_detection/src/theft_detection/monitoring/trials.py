"""Records staged live trials so each one can be scored and debugged afterwards.

A reviewer starts a trial ("normal hand-off, expect no alert"), acts it out in front of the
camera, and stops it. The recorder summarises what the pipeline did during that window and
appends one JSON line (metadata only, never pixels) to the trial log.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..types import BAG, PERSON, Track

# The staged procedure in docs/LIVE_TESTING.md. "expected" is what a correct system does.
SCENARIOS: dict[str, dict] = {
    "S1_normal_handoff": {"title": "Normal hand-off of a bag", "expected": "no_alert"},
    "S2_pick_up_own_bag": {"title": "Person picks up their own bag", "expected": "no_alert"},
    "S3_adjust_bag": {"title": "Adjusting or repositioning a bag", "expected": "no_alert"},
    "S4_walk_past": {"title": "Two people walk past one another", "expected": "no_alert"},
    "S5_fast_with_own_bag": {"title": "Person moves quickly carrying their own bag", "expected": "no_alert"},
    "S6_abrupt_snatch": {"title": "Simulated abrupt bag snatch (no struggle)", "expected": "alert"},
    "S7_obscured_transfer": {"title": "Bag transfer partly hidden by the two people", "expected": "any"},
    "S8_bag_hidden": {"title": "Bag temporarily invisible to the camera", "expected": "no_alert"},
    "custom": {"title": "Custom trial", "expected": "any"},
}


@dataclass
class _Active:
    trial_id: str
    scenario: str
    expected: str
    note: str
    start_ts: float
    started_utc: str
    frames: int = 0
    frames_with_bag: int = 0
    frames_bag_coasting: int = 0
    bag_ids: set[int] = field(default_factory=set)
    person_ids: set[int] = field(default_factory=set)
    bag_gaps: int = 0
    _bag_seen_last: dict[int, float] = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)


class TrialRecorder:
    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path) if path else None
        self._active: _Active | None = None
        self._lock = threading.Lock()
        self.completed: list[dict] = []
        if self.path and self.path.is_file():
            for line in self.path.read_text(encoding="utf-8").splitlines()[-50:]:
                try:
                    self.completed.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    @property
    def active(self) -> dict | None:
        with self._lock:
            a = self._active
            if a is None:
                return None
            return {"trial_id": a.trial_id, "scenario": a.scenario, "expected": a.expected,
                    "frames": a.frames, "events": len(a.events), "started_utc": a.started_utc}

    def start(self, scenario: str, stream_ts: float, expected: str | None = None, note: str = "") -> dict:
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown scenario '{scenario}'")
        with self._lock:
            if self._active is not None:
                raise RuntimeError("a trial is already running; stop it first")
            self._active = _Active(trial_id=f"trial_{uuid.uuid4().hex[:10]}", scenario=scenario,
                                   expected=expected or SCENARIOS[scenario]["expected"], note=note[:500],
                                   start_ts=stream_ts,
                                   started_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        return self.active  # type: ignore[return-value]

    def on_frame(self, ts: float, tracks: list[Track]) -> None:
        with self._lock:
            a = self._active
            if a is None:
                return
            a.frames += 1
            observed_bags = [t for t in tracks if t.category == BAG and t.lost_s == 0.0]
            if observed_bags:
                a.frames_with_bag += 1
            elif any(t.category == BAG for t in tracks):
                a.frames_bag_coasting += 1
            for t in tracks:
                if t.category == PERSON:
                    a.person_ids.add(t.track_id)
            for t in observed_bags:
                a.bag_ids.add(t.track_id)
                last = a._bag_seen_last.get(t.track_id)
                if last is not None and ts - last > 0.35:     # re-observed after a detection gap
                    a.bag_gaps += 1
                a._bag_seen_last[t.track_id] = ts

    def on_event(self, event) -> None:
        with self._lock:
            if self._active is None:
                return
            ev = event.evidence
            self._active.events.append({
                "event_id": event.event_id, "event_type": event.event_type, "severity": event.severity,
                "evidence_score": event.evidence_score, "onset_stream_s": ev.onset_stream_s,
                "detected_stream_s": ev.detected_stream_s, "detection_delay_s": ev.detection_delay_s,
                "processing_lag_s": ev.processing_lag_s, "observation": event.observation,
                "uncertainties": list(event.uncertainties)})

    def stop(self, stream_ts: float, decisions, open_candidates: list[dict], metrics: dict | None = None) -> dict:
        with self._lock:
            a = self._active
            if a is None:
                raise RuntimeError("no trial is running")
            self._active = None
        window = [d for d in decisions if a.start_ts <= d.ts <= stream_ts]
        candidates = [d for d in window if d.kind in ("transfer", "pickup", "removal", "disappearance")]
        review = [e for e in a.events if e["severity"] == "review"]
        info = [e for e in a.events if e["severity"] != "review"]
        if review:
            outcome = "alert"
        elif info:
            outcome = "info_only"
        elif candidates or open_candidates:
            outcome = "candidate_rejected"
        else:
            outcome = "no_candidate"
        if a.expected == "alert":
            verdict = "detected" if review else "missed"
        elif a.expected == "no_alert":
            verdict = "false_alert" if review else "correct"
        else:
            verdict = "not_scored"
        summary = {
            "trial_id": a.trial_id, "scenario": a.scenario, "title": SCENARIOS[a.scenario]["title"],
            "expected": a.expected, "outcome": outcome, "verdict": verdict, "note": a.note,
            "started_utc": a.started_utc, "duration_s": round(stream_ts - a.start_ts, 2),
            "frames_analysed": a.frames,
            "bag_detection_coverage": round(a.frames_with_bag / a.frames, 3) if a.frames else None,
            "frames_bag_only_predicted": a.frames_bag_coasting,
            "bag_track_ids": sorted(a.bag_ids), "person_track_ids": sorted(a.person_ids),
            "bag_detection_gaps": a.bag_gaps,
            "candidates": [{"kind": d.kind, "outcome": d.outcome, "reason": d.reason,
                            "at_stream_s": round(d.ts, 2), "features": d.features} for d in candidates],
            "still_open_candidates": open_candidates,
            "events": a.events,
            "alert_delay_s": [e["detection_delay_s"] for e in review],
            "metrics_at_stop": metrics or {},
            "recorded_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "wall_time": time.time(),
        }
        self.completed.append(summary)
        self.completed = self.completed[-50:]
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(summary, default=str) + "\n")
        return summary
