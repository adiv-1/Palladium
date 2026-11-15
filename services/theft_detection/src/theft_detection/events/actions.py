"""Action route: classify the *act* of taking something from a person, whatever the object is.

Works on body keypoints (wrists, shoulders) of two tracked people; no object detection is
needed, so it also covers phones, wallets, laptops or a bag the detector cannot see.

The observable pattern, in order:

1. hand contact  - a wrist of one person meets the other's wrist, or reaches into their body
                   area with the arm extended towards them;
2. separation    - the two move apart (or one leaves the view) shortly after the contact ends;
3. and either:
   flight        - the person who reached leaves fast (>= fast_speed body heights/s), after a
                   contact of several frames or with a reaction from the other person (an arm kept
                   stretched towards them after the hands part, or setting off after them); or
   pulling       - during the contact both arms were stretched towards each other for several
                   frames while the two bodies moved apart (a tug over something).

A handshake, a hand-over or a hug has step 1 and usually step 2, but neither flight nor a
reaction, and is dismissed. A game of tag or a playful grab-and-run is the same motion and
*will* be reported: video cannot tell them apart, a reviewer can.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from ..detection.pose import L_HIP, L_SHOULDER, L_WRIST, R_HIP, R_SHOULDER, R_WRIST
from ..interactions.geometry import dist, expand, point_in_box, saturate
from ..types import RawEvent, Track

if TYPE_CHECKING:
    from .engine import EventEngine

_KP_MIN = 0.3   # keypoint score needed to use a joint


@dataclass(slots=True)
class _Episode:
    a: int
    b: int
    start: float
    last: float
    frames: int = 0
    mutual_frames: int = 0
    reach_frames: dict[int, int] = field(default_factory=dict)
    max_reach: dict[int, float] = field(default_factory=dict)
    ended: float | None = None
    after_reach: dict[int, int] = field(default_factory=dict)
    arm_kept_out: dict[int, bool] = field(default_factory=dict)
    gone_since: dict[int, float] = field(default_factory=dict)
    h: float = 1.0
    gap_min: float = math.inf        # closest the two torsos came during the contact
    pulled_apart: float = 0.0        # how far they moved apart afterwards while still linked


def _torso(kp: np.ndarray, box) -> tuple[float, float]:
    pts = [kp[i, :2] for i in (L_SHOULDER, R_SHOULDER, L_HIP, R_HIP) if kp[i, 2] >= _KP_MIN]
    if len(pts) >= 2:
        c = np.mean(pts, axis=0)
        return float(c[0]), float(c[1])
    return (box[0] + box[2]) * 0.5, (box[1] + box[3]) * 0.5


def _wrists(kp: np.ndarray) -> list[tuple[float, float]]:
    return [(float(kp[i, 0]), float(kp[i, 1])) for i in (L_WRIST, R_WRIST) if kp[i, 2] >= _KP_MIN]


def reach_towards(kp: np.ndarray, box, target: tuple[float, float], h: float) -> float:
    """How far (body heights) the furthest wrist extends from the torso centre towards ``target``
    (the other person's torso centre). A hanging arm scores about 0.15; a stretched one 0.3-0.45.
    Undefined, and reported as 0, when the two torsos overlap in the image: the direction to the
    other person is then meaningless and a hanging arm would look like a reach."""
    s = _torso(kp, box)
    dx, dy = target[0] - s[0], target[1] - s[1]
    n = math.hypot(dx, dy)
    if n < 0.3 * h:
        return 0.0
    ux, uy = dx / n, dy / n
    return max(((w[0] - s[0]) * ux + (w[1] - s[1]) * uy for w in _wrists(kp)), default=0.0) / h


class ActionEngine:
    def __init__(self, engine: "EventEngine") -> None:
        self.eng = engine
        self.episodes: dict[tuple[int, int], _Episode] = {}

    def reset(self) -> None:
        self.episodes.clear()

    def snapshot(self) -> list[dict]:
        from .engine import person_ref
        ts = self.eng._last_ts or 0.0
        return [{"kind": "hand contact" if e.ended is None else "after contact", "object": "unidentified",
                 "from": person_ref(e.a), "to": person_ref(e.b),
                 "elapsed_s": round(ts - (e.start if e.ended is None else e.ended), 2)}
                for e in self.episodes.values() if e.frames >= 2]

    # ------------------------------------------------------------------------------------
    def update(self, ts: float, persons: dict[int, Track], poses: dict[int, np.ndarray],
               events: list[RawEvent]) -> None:
        cfg = self.eng.cfg
        ids = sorted(pid for pid in persons if pid in poses and persons[pid].lost_s == 0.0)
        seen_contact: set[tuple[int, int]] = set()
        for n, a in enumerate(ids):
            for b in ids[n + 1:]:
                pa, pb, ka, kb = persons[a], persons[b], poses[a], poses[b]
                h = 0.5 * ((pa.box[3] - pa.box[1]) + (pb.box[3] - pb.box[1]))
                ta, tb = _torso(ka, pa.box), _torso(kb, pb.box)
                if dist(ta, tb) / h > cfg.action_near:
                    continue
                wa, wb = _wrists(ka), _wrists(kb)
                hands = min((dist(x, y) for x in wa for y in wb), default=math.inf) / h
                ra, rb = reach_towards(ka, pa.box, tb, h), reach_towards(kb, pb.box, ta, h)
                # a wrist inside the other's body area with that arm extended towards them
                into_b = ra >= cfg.action_reach and any(point_in_box(w, expand(pb.box, 0.05 * h)) for w in wa)
                into_a = rb >= cfg.action_reach and any(point_in_box(w, expand(pa.box, 0.05 * h)) for w in wb)
                gap = dist(ta, tb) / h
                # both arms stretched towards each other with the hands an object-width apart:
                # two people holding the same thing
                linked = ra >= cfg.action_reach and rb >= cfg.action_reach and hands <= cfg.action_linked_dist
                contact = hands <= cfg.action_hand_dist or linked or into_a or into_b
                key = (a, b)
                ep = self.episodes.get(key)
                if contact:
                    if ep is None:            # a pause shorter than the decision window resumes the episode
                        ep = self.episodes[key] = _Episode(a=a, b=b, start=ts, last=ts, h=h)
                    ep.ended = None
                    ep.after_reach.clear()
                    ep.arm_kept_out.clear()
                    ep.last, ep.h = ts, h
                    ep.frames += 1
                    for pid, r in ((a, ra), (b, rb)):
                        ep.max_reach[pid] = max(ep.max_reach.get(pid, 0.0), r)
                        if r >= cfg.action_reach:
                            ep.reach_frames[pid] = ep.reach_frames.get(pid, 0) + 1
                    ep.gap_min = min(ep.gap_min, gap)
                    if ra >= cfg.action_reach and rb >= cfg.action_reach and gap >= 0.45:
                        ep.mutual_frames += 1
                        ep.pulled_apart = max(ep.pulled_apart, gap - ep.gap_min)
                    seen_contact.add(key)
                elif ep is not None:
                    # after the hands part: is either person still stretching towards the other?
                    for pid, r in ((a, ra), (b, rb)):
                        if ep.arm_kept_out.get(pid, True) and r >= cfg.action_reach and ts - ep.last <= 1.5:
                            ep.after_reach[pid] = ep.after_reach.get(pid, 0) + 1
                            ep.arm_kept_out[pid] = True
                        elif ts - ep.last > 0.25:
                            ep.arm_kept_out[pid] = False     # arm came back: later waves do not count

        for key, ep in list(self.episodes.items()):
            if key in seen_contact:
                continue
            if ep.ended is None:
                if ts - ep.last < cfg.action_gap_s:
                    continue
                ep.ended = ep.last
            self._after_contact(ts, key, ep, persons, events)

    def _at_edge(self, pid: int) -> bool:
        hist, size = self.eng.hist.get(pid), self.eng._frame_size
        if hist is None or not hist.boxes or not size:
            return False
        b, m = hist.boxes[-1], 0.04 * size[0]
        return b[0] <= m or b[2] >= size[0] - m

    # ------------------------------------------------------------------------------------
    def _after_contact(self, ts: float, key, ep: _Episode, persons: dict[int, Track],
                       events: list[RawEvent]) -> None:
        from .engine import (POSSIBLE_SNATCHING, SEVERITY_INFO, SEVERITY_REVIEW, UNCERTAIN_INTERACTION,
                             person_ref)
        eng, cfg, h = self.eng, self.eng.cfg, ep.h
        ha, hb = eng.hist.get(ep.a), eng.hist.get(ep.b)
        if ha is None or hb is None or ep.frames < 2:
            del self.episodes[key]
            return
        for pid in (ep.a, ep.b):
            if pid not in persons:
                ep.gone_since.setdefault(pid, ts)
            else:
                ep.gone_since.pop(pid, None)
        # "left the view" only if last seen at the frame edge; otherwise they are just hidden
        gone = [pid for pid, t in ep.gone_since.items() if ts - t >= 0.3 and self._at_edge(pid)]
        if len(ep.gone_since) == 2 and all(ts - t >= 0.3 for t in ep.gone_since.values()):
            del self.episodes[key]
            return
        sep = dist(ha.pos(ha.last_ts), hb.pos(hb.last_ts)) / h
        departed = sep >= cfg.separation_min or len(gone) == 1
        if not departed:
            if ts - ep.ended >= cfg.action_window_s:
                del self.episodes[key]
                if ep.frames >= 3:
                    eng._dismiss(ts, "action", "hand_contact_then_stayed_together",
                                 {"contact_s": ep.ended - ep.start, "separation": sep})
            return

        speed = {pid: hist.net_speed(ep.ended, hist.last_ts) / h for pid, hist in ((ep.a, ha), (ep.b, hb))}
        taker = gone[0] if gone else max(speed, key=speed.get)
        other = ep.b if taker == ep.a else ep.a
        ht, ho = (ha, hb) if taker == ep.a else (hb, ha)
        # measure the leaver's speed over the last stretch they were seen (a camera pan or the
        # frame edge often hides the full get-away)
        v_taker = max(speed[taker], ht.net_speed(max(ep.ended, ht.last_ts - 0.8), ht.last_ts) / h)
        vo, vt = ho.velocity(ep.ended, ho.last_ts), ht.velocity(ep.ended, ht.last_ts)
        pursuit = speed[other] >= cfg.pursuit_speed and vo[0] * vt[0] + vo[1] * vt[1] > 0
        reaction = ep.after_reach.get(other, 0) >= 2 or pursuit
        flee = v_taker >= cfg.fast_speed
        taker_reached = ep.reach_frames.get(taker, 0) >= 1
        sustained = ep.frames >= 3
        # a tug: arms stretched towards each other for several frames while the bodies moved apart
        pulling = ep.mutual_frames >= 3 and ep.pulled_apart >= cfg.action_pull_apart
        # give a reaction a moment to show before deciding
        if not reaction and ts - ep.ended < 0.8 and other in persons:
            return
        del self.episodes[key]
        f = {"contact_s": ep.ended - ep.start, "contact_frames": float(ep.frames),
             "mutual_reach_frames": float(ep.mutual_frames), "pulled_apart": ep.pulled_apart,
             "taker_reach_max": ep.max_reach.get(taker, 0.0), "other_reach_max": ep.max_reach.get(other, 0.0),
             "taker_speed_after": v_taker, "other_speed_after": speed[other], "separation": sep,
             "other_reach_frames_after": float(ep.after_reach.get(other, 0)),
             "other_followed": 1.0 if pursuit else 0.0, "taker_left_view": 1.0 if gone else 0.0,
             "companion_s": eng._companion_time(ho, ht, ep.start, h),
             "camera_unstable_frac": eng._unstable_frac(ep.start, ts), "action_route": 1.0}
        if f["camera_unstable_frac"] > cfg.max_unstable_frac:
            eng._dismiss(ts, "action", "camera_motion", f)
            return
        pair = f"pair:{min(ep.a, ep.b)}:{max(ep.a, ep.b)}"
        if eng._cooling(pair, ts):
            eng._dismiss(ts, "action", "duplicate_within_cooldown", f)
            return

        t_ref, o_ref = person_ref(taker), person_ref(other)
        left = "left the view" if gone else f"moved away at {v_taker:.1f} body heights per second"
        react = []
        if ep.after_reach.get(other, 0) >= 2:
            react.append(f"{o_ref} kept an arm stretched towards {t_ref} after their hands parted")
        if pursuit:
            react.append(f"{o_ref} set off in the same direction")
        uncertainties = ["what, if anything, changed hands was not identified",
                         "a greeting, a game or other consensual contact followed by one person leaving quickly "
                         "looks the same in video"]
        common = dict(onset_ts=ep.start, detected_ts=ts, features=f, object_label=None, object_ref=None,
                      tracks=[{"ref": o_ref, "role": "other_person"}, {"ref": t_ref, "role": "departing_person"}],
                      dedup_key=f"action|{ep.a}|{ep.b}|{ep.start:.1f}")

        if (flee and taker_reached and (sustained or reaction)) or pulling:
            score = (0.55 + 0.08 * saturate(v_taker, cfg.fast_speed, cfg.fast_speed + 1.5)
                     + (0.07 if ep.after_reach.get(other, 0) >= 2 else 0.0) + (0.05 if pursuit else 0.0)
                     + (0.05 if pulling else 0.0)
                     - 0.08 * saturate(f["companion_s"], 0.0, cfg.companion_full_s))
            how = (f"both had an arm stretched towards the other for {ep.mutual_frames} frames while they moved "
                   f"{ep.pulled_apart:.1f} body heights apart" if pulling else f"{t_ref} reached towards {o_ref}")
            obs = (f"Hand contact between {t_ref} and {o_ref} for {f['contact_s']:.1f} s ({how}); "
                   f"{t_ref} then {left}" + ("; " + " and ".join(react) if react else "") + ".")
            eng._cool(pair, ts, cfg.cooldown_s)
            eng._emit(events, RawEvent(event_type=POSSIBLE_SNATCHING, severity=SEVERITY_REVIEW,
                                       score=score, observation=obs, uncertainties=uncertainties, **common), "action")
        elif flee and taker_reached:
            obs = (f"Brief hand contact between {t_ref} and {o_ref}, after which {t_ref} {left}; "
                   f"no reaction from {o_ref} was observed.")
            eng._cool(pair, ts, cfg.cooldown_s)
            eng._emit(events, RawEvent(event_type=UNCERTAIN_INTERACTION, severity=SEVERITY_INFO, score=0.3,
                                       observation=obs, uncertainties=uncertainties, **common), "action")
        elif sustained:
            eng._dismiss(ts, "action", "hand_contact_then_ordinary_parting", f)
