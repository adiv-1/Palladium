"""Temporal event engine: an explainable two-stage cascade over tracks.

Stage 1 (cheap, every analysed frame)
    Maintain, per tracked object, *who appears to be holding it* and whether it is resting
    unattended. A candidate is opened only on a structural change: the object leaves its
    established holder's reach and is with another person, or a resting object is lifted.

Stage 2 (only for candidates)
    Look at a short window before and after the candidate onset and measure object speed,
    speed change, separation from the prior holder, how well the object was observed, whether
    the two people's boxes overlapped so much that track identities are unreliable, camera
    motion, and whether the prior holder still has an object where it was. Emit, downgrade or
    dismiss - and record the reason either way.

The engine never sees pixels and never decides ownership or intent. It reports what moved,
how fast, and between which ephemeral tracks.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from statistics import median

from ..config import EngineConfig, ZoneConfig
from ..interactions.geometry import (
    center, dist, expand, foot_point, iou, overlap_frac, point_in_box, point_in_polygon, saturate,
)
from ..interactions.history import TrackHistory
from ..types import BAG, PERSON, Box, Decision, Detection, RawEvent, Track

POSSIBLE_SNATCHING = "possible_snatching"
SUSPICIOUS_OBJECT_REMOVAL = "suspicious_object_removal"
RAPID_OBJECT_TRANSFER = "rapid_object_transfer"
POSSIBLE_RESTRICTED_AREA_ENTRY = "possible_restricted_area_entry"
UNCERTAIN_INTERACTION = "uncertain_interaction"

SEVERITY_REVIEW = "review"   # shown to a human reviewer as a candidate
SEVERITY_INFO = "info"       # logged on the timeline; not counted as an alert

_VISIBLE_GRACE_S = 0.35      # a track coasting on prediction this long still counts as visible


def person_ref(track_id: int) -> str:
    return f"person_{track_id}"


def object_ref(track_id: int) -> str:
    return f"object_{track_id}"


@dataclass(slots=True)
class _Rest:
    pos: tuple[float, float]   # compensated centre while resting
    box: Box                   # raw box when the rest was established
    g: tuple[float, float]     # cumulative camera offset at that time
    since: float
    last_still_ts: float


@dataclass(slots=True)
class _Obj:
    track_id: int
    label: str
    first_ts: float
    last_seen: float
    holder: int | None = None
    holder_since: float = 0.0
    last_with_holder: float = 0.0
    holder_gone_since: float | None = None
    detach_count: int = 0
    assoc: int | None = None
    assoc_since: float = 0.0
    assoc_last_ts: float = 0.0
    prev_holder: int | None = None
    released_ts: float = -1e9
    handlers: set[int] = field(default_factory=set)
    still_since: float | None = None
    rest: _Rest | None = None
    adjacent: dict[int, float] = field(default_factory=dict)
    missing_since: float | None = None
    clear_since: float | None = None
    adjacent_at_loss: list[int] = field(default_factory=list)
    pending: bool = False
    relinked_ts: float | None = None
    ref_box: Box | None = None


@dataclass(slots=True)
class _Transfer:
    obj_id: int
    holder: int
    taker: int
    t0: float
    opened_ts: float
    h_ref: float
    held_s: float
    separated_ts: float | None = None


@dataclass(slots=True)
class _Pickup:
    obj_id: int
    taker: int
    t0: float
    opened_ts: float
    rest: _Rest
    h_ref: float


@dataclass(slots=True)
class _ZoneDwell:
    enter: float
    last: float
    hits: int
    emitted: bool = False


class EventEngine:
    def __init__(self, cfg: EngineConfig | None = None, zones: list[ZoneConfig] | None = None) -> None:
        self.cfg = cfg or EngineConfig()
        self.zones = [z for z in (zones or []) if z.active]
        self.decisions: deque[Decision] = deque(maxlen=200)
        from .actions import ActionEngine
        self.actions = ActionEngine(self)
        self.reset()

    def reset(self) -> None:
        self.hist: dict[int, TrackHistory] = {}
        self.objs: dict[int, _Obj] = {}
        self._transfers: list[_Transfer] = []
        self._pickups: list[_Pickup] = []
        self._g = (0.0, 0.0)
        self._prev_gm = (0.0, 0.0)
        self._unstable: deque[tuple[float, bool]] = deque()
        self._zone_state: dict[tuple[int, str], _ZoneDwell] = {}
        self._cooldowns: dict[str, float] = {}
        self._scene_h: deque[float] = deque(maxlen=300)
        # Raw bag detections of recent frames (including ones no confirmed track claimed).
        # None = the caller does not supply detections, and the checks that need them are skipped.
        self._bag_dets: deque[tuple[float, list[Box]]] | None = None
        self._last_ts: float | None = None
        self._frame_size: tuple[int, int] | None = None
        self.actions.reset()
        self.decisions.clear()

    # ====================================================================================
    # per-frame entry point
    # ====================================================================================
    def update(
        self,
        ts: float,
        tracks: list[Track],
        global_motion: tuple[float, float] = (0.0, 0.0),
        frame_size: tuple[int, int] | None = None,
        detections: list[Detection] | None = None,
        poses: dict | None = None,
    ) -> list[RawEvent]:
        """Advance one analysed frame. ``detections`` (optional) are this frame's raw detector
        output; with them the verifier can see objects that no confirmed track has claimed."""
        cfg = self.cfg
        if frame_size is not None:
            self._frame_size = frame_size
        dt = 0.0 if self._last_ts is None else max(ts - self._last_ts, 1e-3)
        self._last_ts = ts
        events: list[RawEvent] = []
        if detections is not None:
            if self._bag_dets is None:
                self._bag_dets = deque()
            # Only detections confident enough to count as a tracked observation may serve as
            # evidence that someone still has a bag (weaker ones would veto real alerts).
            self._bag_dets.append((ts, [d.box for d in detections
                                        if d.category == BAG and d.score >= cfg.veto_min_score]))
            while self._bag_dets[0][0] < ts - cfg.verify_window_s - 1.0:
                self._bag_dets.popleft()

        # camera motion bookkeeping -----------------------------------------------------
        self._g = (self._g[0] + global_motion[0], self._g[1] + global_motion[1])
        diag = math.hypot(*self._frame_size) if self._frame_size else 1000.0
        shift_speed = math.hypot(*global_motion) / dt / diag if dt > 0 else 0.0
        jitter = math.hypot(global_motion[0] - self._prev_gm[0], global_motion[1] - self._prev_gm[1]) / dt / diag \
            if dt > 0 else 0.0
        self._prev_gm = global_motion
        self._unstable.append((ts, jitter > cfg.camera_jitter or shift_speed > cfg.camera_max_speed))
        while self._unstable and self._unstable[0][0] < ts - cfg.history_s:
            self._unstable.popleft()

        persons = {t.track_id: t for t in tracks if t.category == PERSON}
        objects = {t.track_id: t for t in tracks if t.category == BAG}
        visible_persons = {k: p for k, p in persons.items() if p.lost_s <= _VISIBLE_GRACE_S}
        visible_objects = {k: o for k, o in objects.items() if o.lost_s == 0.0}

        # bridge object identity across tracker ID switches -------------------------------
        for oid, o in visible_objects.items():
            if oid not in self.objs:
                self._try_relink(oid, o, ts, visible_objects)

        # histories -----------------------------------------------------------------------
        for t in tracks:
            h = self.hist.get(t.track_id)
            if h is None:
                h = self.hist[t.track_id] = TrackHistory()
            # Only real observations enter the history: a coasting Kalman prediction would
            # otherwise look like motion. People get a short grace period.
            if t.lost_s == 0.0 or (t.category == PERSON and t.lost_s <= _VISIBLE_GRACE_S):
                h.add(ts, t.box, t.lost_s == 0.0, self._g)
            if t.category == PERSON and t.lost_s == 0.0:
                self._scene_h.append(t.box[3] - t.box[1])

        # stage 1: holder / rest state per object -----------------------------------------
        for oid, o in visible_objects.items():
            self._update_object(ts, o, visible_persons, events)
        for oid in [k for k in self.objs if k not in visible_objects]:
            self._update_missing(ts, self.objs[oid], visible_persons, objects, events)

        # stage 2: verify open candidates -------------------------------------------------
        for c in list(self._transfers):
            self._verify_transfer(c, ts, visible_persons, visible_objects, events)
        for c in list(self._pickups):
            self._verify_pickup(c, ts, visible_persons, events)

        if poses is not None:
            self.actions.update(ts, visible_persons, poses, events)
        self._update_zones(ts, persons, events)
        self._cleanup(ts, tracks)
        if not cfg.emit_info_events:
            events = [e for e in events if e.severity != SEVERITY_INFO]
        return events

    def snapshot(self) -> dict:
        """What the engine currently believes, for the live debugging view. Read-only."""
        ts = self._last_ts or 0.0
        objects = []
        for st in self.objs.values():
            if st.missing_since is not None:
                state = f"not detected for {ts - st.missing_since:.1f} s"
            elif st.rest is not None:
                state = f"resting unattended {ts - st.rest.since:.1f} s"
            elif st.holder is not None:
                state = f"held by {person_ref(st.holder)} for {ts - st.holder_since:.1f} s"
            elif st.assoc is not None:
                state = f"near {person_ref(st.assoc)}"
            else:
                state = "no holder"
            objects.append({"ref": object_ref(st.track_id), "label": st.label, "state": state,
                            "candidate_open": st.pending})
        candidates = [{"kind": "transfer", "object": object_ref(c.obj_id), "from": person_ref(c.holder),
                       "to": person_ref(c.taker), "elapsed_s": round(ts - c.t0, 2)} for c in self._transfers]
        candidates += [{"kind": "pickup", "object": object_ref(c.obj_id), "from": None,
                        "to": person_ref(c.taker), "elapsed_s": round(ts - c.t0, 2)} for c in self._pickups]
        candidates += self.actions.snapshot()
        return {"stream_time_s": round(ts, 2), "objects": objects, "open_candidates": candidates,
                "camera_unstable": bool(self._unstable and self._unstable[-1][1])}

    # ====================================================================================
    # helpers
    # ====================================================================================
    def _scene_height(self, fallback: float) -> float:
        if self._scene_h:
            return median(self._scene_h)
        if self._frame_size:
            return self._frame_size[1] * 0.4
        return fallback

    def _person_height(self, pid: int, t0: float, t1: float) -> float | None:
        h = self.hist.get(pid)
        return h.median_h(t0, t1) if h else None

    def _reach_box(self, person: Track) -> Box:
        ph = person.box[3] - person.box[1]
        return expand(person.box, self.cfg.reach * ph, 0.05 * ph, 0.10 * ph)

    def _observed_frac(self, hist: TrackHistory, t0: float, t1: float) -> float:
        """Share of analysed frames in [t0, t1] on which the track was actually detected."""
        frames = sum(1 for t, _ in self._unstable if t0 <= t <= t1 + 1e-9)
        seen = sum(1 for i in hist.samples_between(t0, t1 + 1e-9) if hist.obs[i])
        return min(seen / frames, 1.0) if frames else 0.0

    def _unstable_frac(self, t0: float, t1: float) -> float:
        flags = [u for t, u in self._unstable if t0 <= t <= t1]
        return sum(flags) / len(flags) if flags else 0.0

    def _cooling(self, key: str, ts: float) -> bool:
        until = self._cooldowns.get(key)
        return until is not None and ts < until

    def _cool(self, key: str, ts: float, seconds: float) -> None:
        self._cooldowns[key] = ts + seconds

    def _dismiss(self, ts: float, kind: str, reason: str, features: dict | None = None) -> None:
        self.decisions.append(Decision(ts=ts, kind=kind, outcome="dismissed", reason=reason,
                                       features=_round(features or {})))

    def _emit(self, events: list[RawEvent], ev: RawEvent, kind: str) -> None:
        ev.features = _round(ev.features)
        ev.score = round(min(max(ev.score, 0.0), 1.0), 3)
        events.append(ev)
        self.decisions.append(Decision(ts=ev.detected_ts, kind=kind, outcome="emitted",
                                       reason=ev.event_type, event_type=ev.event_type,
                                       features=ev.features))

    def _rest_box_now(self, rest: _Rest) -> Box:
        dx, dy = self._g[0] - rest.g[0], self._g[1] - rest.g[1]
        b = rest.box
        return b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy

    def _monitored_zone(self, rest: _Rest) -> ZoneConfig | None:
        if not self._frame_size:
            return None
        w, h = self._frame_size
        cx, cy = center(rest.box)
        for z in self.zones:
            if z.kind == "monitored" and point_in_polygon((cx / w, cy / h), z.polygon):
                return z
        return None

    # ====================================================================================
    # stage 1
    # ====================================================================================
    def _try_relink(self, oid: int, o: Track, ts: float, visible_objects: dict[int, Track]) -> None:
        """A new object track may be the continuation of one the tracker just lost."""
        cfg = self.cfg
        pos = center(o.box)
        best: tuple[float, _Obj] | None = None
        for st in self.objs.values():
            if st.missing_since is None or st.track_id in visible_objects:
                continue
            old = self.hist.get(st.track_id)
            if old is None or not old.boxes:
                continue
            href = self._scene_height(max(o.box[2] - o.box[0], o.box[3] - o.box[1]) * 3.0)
            gap = ts - st.missing_since
            if st.rest is not None:
                d = dist(pos, center(self._rest_box_now(st.rest)))
                ok = d <= 0.3 * href
            else:
                d = dist(pos, center(old.boxes[-1]))
                ok = gap <= cfg.relink_window_s and d <= href * (cfg.relink_base + cfg.relink_per_s * gap)
            if ok and (best is None or d < best[0]):
                best = (d, st)
        if best is None:
            return
        st = best[1]
        old_id = st.track_id
        new_hist = self.hist.setdefault(oid, TrackHistory())
        new_hist.prepend(self.hist.pop(old_id, TrackHistory()))
        del self.objs[old_id]
        st.track_id = oid
        st.missing_since = None
        st.clear_since = None
        st.relinked_ts = ts
        self.objs[oid] = st
        for c in self._transfers:
            if c.obj_id == old_id:
                c.obj_id = oid
        for p in self._pickups:
            if p.obj_id == old_id:
                p.obj_id = oid

    def _update_object(self, ts: float, o: Track, persons: dict[int, Track], events: list[RawEvent]) -> None:
        cfg = self.cfg
        st = self.objs.get(o.track_id)
        if st is None:
            st = self.objs[o.track_id] = _Obj(track_id=o.track_id, label=o.label,
                                               first_ts=o.first_ts, last_seen=ts)
        st.label = o.label
        st.last_seen = ts
        st.ref_box = o.box
        st.missing_since = None
        st.clear_since = None

        overlaps = {pid: overlap_frac(o.box, self._reach_box(p)) for pid, p in persons.items()}
        best: int | None = None
        if overlaps:
            oc = center(o.box)
            cand = max(overlaps, key=lambda pid: (round(overlaps[pid], 2), -dist(oc, center(persons[pid].box))))
            if overlaps[cand] >= cfg.assoc_min_overlap:
                best = cand

        for pid, ov in overlaps.items():       # who has been within reach of it recently
            if ov >= cfg.assoc_min_overlap:
                st.adjacent[pid] = ts

        # ---- object has an established holder -----------------------------------------
        if st.holder is not None:
            a = st.holder
            if a not in persons:
                if st.holder_gone_since is None:
                    st.holder_gone_since = ts
                if ts - st.holder_gone_since > cfg.holder_grace_s:
                    st.handlers.add(a)
                    st.holder, st.assoc, st.detach_count = None, None, 0
                    st.holder_gone_since = None
                return
            st.holder_gone_since = None
            if overlaps[a] >= cfg.assoc_keep_overlap:
                st.last_with_holder = ts
                st.detach_count = 0
                return
            st.detach_count += 1
            if st.detach_count < 2 and ts - st.last_with_holder < cfg.detach_confirm_s:
                return
            # confirmed: the object is out of its holder's reach
            t0 = st.last_with_holder
            held_s = t0 - st.holder_since
            st.handlers.add(a)
            st.prev_holder, st.released_ts = a, t0
            st.holder, st.detach_count = None, 0
            st.still_since = None
            if best is not None and best != a:
                self._open_transfer(st, a, best, t0, ts, held_s)
                st.assoc, st.assoc_since, st.assoc_last_ts = best, ts, ts
            else:
                st.assoc = None
            return

        # ---- no established holder ----------------------------------------------------
        if best is None:
            if st.assoc is not None and ts - st.assoc_last_ts > cfg.assoc_gap_s:
                if st.assoc_last_ts - st.assoc_since >= 0.5:   # a fleeting overlap is not handling
                    st.handlers.add(st.assoc)
                st.assoc = None
            if st.assoc is None:
                self._update_rest(ts, o, st)
            return

        if st.assoc != best:
            if (st.prev_holder is not None and best != st.prev_holder and not st.pending
                    and ts - st.released_ts <= cfg.late_take_s):
                # released a moment ago and now with someone else (e.g. pulled away, then caught)
                self._open_transfer(st, st.prev_holder, best, st.released_ts, ts,
                                    st.released_ts - st.holder_since)
            st.assoc, st.assoc_since = best, ts
        st.assoc_last_ts = ts
        st.still_since = None

        if st.rest is not None:
            self._check_pickup(ts, o, st, best)
            return
        if ts - st.assoc_since >= cfg.hold_min_s:
            st.holder, st.holder_since = best, st.assoc_since
            st.last_with_holder = ts
            st.detach_count = 0

    def _update_rest(self, ts: float, o: Track, st: _Obj) -> None:
        cfg = self.cfg
        hist = self.hist[o.track_id]
        href = self._scene_height(max(o.box[2] - o.box[0], o.box[3] - o.box[1]) * 3.0)
        moved = dist(hist.pos(ts), hist.pos(ts - cfg.rest_window_s)) / href
        if st.rest is not None:
            if dist(hist.pos(ts), st.rest.pos) / href <= cfg.rest_move_tol:
                st.rest.last_still_ts = ts
            elif ts - st.rest.last_still_ts > 0.5:
                st.rest, st.still_since = None, None   # it moved on its own; start over
            return
        if moved > cfg.rest_move_tol:
            st.still_since = None
            return
        if st.still_since is None:
            st.still_since = ts
        if ts - st.still_since >= cfg.unattended_min_s and not st.pending:
            st.rest = _Rest(pos=hist.pos(ts), box=o.box, g=self._g, since=st.still_since, last_still_ts=ts)
            st.adjacent.clear()

    def _open_transfer(self, st: _Obj, holder: int, taker: int, t0: float, ts: float, held_s: float) -> None:
        if st.pending or self._cooling(f"obj:{st.track_id}", ts):
            return
        h_ref = (self._person_height(holder, t0 - 1.5, t0)
                 or self._person_height(taker, t0 - 1.5, ts)
                 or self._scene_height(100.0))
        st.pending = True
        self._transfers.append(_Transfer(obj_id=st.track_id, holder=holder, taker=taker, t0=t0,
                                         opened_ts=ts, h_ref=max(h_ref, 1.0), held_s=max(held_s, 0.0)))

    def _check_pickup(self, ts: float, o: Track, st: _Obj, person: int) -> None:
        cfg = self.cfg
        rest = st.rest
        assert rest is not None
        hist = self.hist[o.track_id]
        href = self._person_height(person, ts - 1.5, ts) or self._scene_height(100.0)
        moved = dist(hist.pos(ts), rest.pos) / href
        if moved <= cfg.rest_move_tol:
            rest.last_still_ts = ts
            return
        if moved < cfg.pickup_move_min or st.pending:
            return
        if person in st.handlers:
            self._dismiss(ts, "pickup", "picked_up_by_a_track_that_handled_it_before",
                          {"moved": moved, "unattended_s": rest.last_still_ts - rest.since})
            self._hand_to(st, person, ts)
            return
        st.pending = True
        self._pickups.append(_Pickup(obj_id=st.track_id, taker=person, t0=rest.last_still_ts,
                                     opened_ts=ts, rest=rest, h_ref=max(href, 1.0)))

    def _hand_to(self, st: _Obj, person: int, ts: float) -> None:
        st.rest, st.still_since, st.pending = None, None, False
        st.handlers.add(person)
        st.holder, st.holder_since, st.last_with_holder = person, ts, ts
        st.assoc, st.assoc_since, st.assoc_last_ts = person, ts, ts
        st.detach_count = 0

    def _update_missing(self, ts: float, st: _Obj, persons: dict[int, Track],
                        objects: dict[int, Track], events: list[RawEvent]) -> None:
        """The object is not detected on this frame (occluded, missed, or actually gone)."""
        cfg = self.cfg
        if st.missing_since is None:
            st.missing_since = ts
            st.adjacent_at_loss = [p for p, t in st.adjacent.items() if ts - t <= 1.0 and p != st.holder]
        if st.pending:
            return
        if st.rest is None:
            if st.holder is not None and cfg.emit_contact_disappearance:
                self._check_vanished_from_holder(ts, st, persons, events)
            return
        rest_box = self._rest_box_now(st.rest)
        rc = center(rest_box)
        occluded = any(point_in_box(rc, p.box) or overlap_frac(rest_box, p.box) >= 0.3
                       for p in persons.values())
        if occluded:
            st.clear_since = None
            for pid, p in persons.items():
                if overlap_frac(rest_box, self._reach_box(p)) >= cfg.assoc_min_overlap:
                    if pid not in st.adjacent_at_loss:
                        st.adjacent_at_loss.append(pid)
            return
        if st.clear_since is None:
            st.clear_since = ts
        if ts - st.clear_since < cfg.absent_confirm_s:
            return

        # The resting spot is in clear view and the object has not been detected there.
        takers = [p for p in st.adjacent_at_loss if p not in st.handlers]
        rest = st.rest
        feats = {"unattended_s": st.missing_since - rest.since, "absent_s": ts - st.missing_since,
                 "adjacent_people": float(len(st.adjacent_at_loss))}
        del self.objs[st.track_id]
        if len(st.adjacent_at_loss) != 1 or not takers:
            self._dismiss(ts, "removal", "object_no_longer_detected_without_a_single_adjacent_person", feats)
            return
        taker = takers[0]
        href = self._person_height(taker, st.missing_since - 1.5, ts) or self._scene_height(100.0)
        th = self.hist.get(taker)
        if taker in persons and th is not None and dist(th.pos(ts), rest.pos) / href < cfg.carry_min:
            self._dismiss(ts, "removal", "adjacent_person_did_not_leave", feats)
            return
        has_new_object = any(
            o.first_ts >= st.missing_since - 0.5 and taker in persons
            and overlap_frac(o.box, self._reach_box(persons[taker])) >= cfg.assoc_min_overlap
            for o in objects.values() if o.lost_s == 0.0
        )
        self._decide_removal(ts, st, taker, rest, href, persons, events, feats,
                             observed=has_new_object, onset=st.missing_since)

    def _check_vanished_from_holder(self, ts: float, st: _Obj, persons: dict[int, Track],
                                    events: list[RawEvent]) -> None:
        """A carried object is no longer detected. If exactly one other person was within reach
        of it when it vanished and that person has since left, say so - at info level only,
        because where the object went was not observed."""
        cfg = self.cfg
        a = st.holder
        gone = ts - st.missing_since
        ha = self.hist.get(a)
        if a not in persons or ha is None or gone < cfg.absent_confirm_s:
            return
        if len(st.adjacent_at_loss) != 1:
            del self.objs[st.track_id]          # plain detection drop-out: nothing to report
            return
        b = st.adjacent_at_loss[0]
        hb = self.hist.get(b)
        h = self._person_height(a, st.missing_since - 1.5, ts) or self._scene_height(100.0)
        if hb is None:
            del self.objs[st.track_id]
            return
        if b in persons and dist(hb.pos(ts), ha.pos(ts)) / h < cfg.separation_min:
            return                               # still together; keep waiting (bounded by cleanup)
        # Before/after possession check (raw detections required).
        verdict, pfeats = None, {}
        if cfg.possession_checks and self._bag_dets is not None:
            sep_ts = next((t for t in hb.ts if t >= st.missing_since and
                           (dist(hb.pos(t), ha.pos(t)) / h >= cfg.separation_min)), hb.last_ts)
            ref = st.ref_box if st.ref_box is not None else (0.0, 0.0, 0.25 * h, 0.25 * h)
            hits, examined = self._bag_frames_near(ha, sep_ts - 0.05, ts, ref, exclude=None)
            dep = hb.net_speed(max(st.missing_since, hb.last_ts - 1.0), hb.last_ts) / h
            seen_with_b = any(o.track_id != st.track_id and o.first_ts >= st.missing_since - 0.5
                              and b in (o.assoc, o.holder) for o in self.objs.values())
            pfeats = {"holder_bag_frames_after_separation": float(hits), "possession_frames_examined": float(examined),
                      "other_person_departure_speed": dep, "object_seen_with_other_person": 1.0 if seen_with_b else 0.0}
            if hits >= 2:
                verdict = "holder_has_bag"
            elif examined < cfg.possession_check_frames and gone < cfg.absent_confirm_s + 4.0:
                return                           # keep looking at the holder a little longer
            elif examined >= cfg.possession_check_frames and seen_with_b and dep >= cfg.fast_speed:
                verdict = "taken"
        feats = {"held_before_s": st.missing_since - st.holder_since, "undetected_s": gone,
                 "other_person_speed_after": hb.net_speed(st.missing_since, ts) / h,
                 "separation": dist(hb.pos(ts), ha.pos(ts)) / h,
                 "camera_unstable_frac": self._unstable_frac(st.missing_since - 0.5, ts)}
        del self.objs[st.track_id]
        pair = f"pair:{min(a, b)}:{max(a, b)}"
        if feats["camera_unstable_frac"] > cfg.max_unstable_frac:
            self._dismiss(ts, "disappearance", "camera_motion", feats)
            return
        if self._cooling(pair, ts):
            self._dismiss(ts, "disappearance", "duplicate_within_cooldown", feats)
            return
        feats.update(pfeats)
        if verdict == "holder_has_bag":
            self._dismiss(ts, "disappearance", "prior_holder_still_has_an_object", feats)
            return
        self._cool(pair, ts, cfg.cooldown_s)
        a_ref, b_ref = person_ref(a), person_ref(b)
        if verdict == "taken":
            self._emit(events, RawEvent(
                event_type=POSSIBLE_SNATCHING, severity=SEVERITY_REVIEW, onset_ts=st.missing_since, detected_ts=ts,
                score=min(0.55 + 0.15 * saturate(feats["other_person_departure_speed"], cfg.fast_speed,
                                                  cfg.fast_speed + 1.5), 0.70),
                observation=(f"A {st.label} carried by {a_ref} for {feats['held_before_s']:.0f} s went out of view while "
                             f"{b_ref} was within reach of it; a {st.label} was then seen with {b_ref}, who moved away "
                             f"at {feats['other_person_departure_speed']:.1f} body heights per second, and none has "
                             f"been detected with {a_ref} in {int(feats['possession_frames_examined'])} frames since."),
                features=feats,
                tracks=[{"ref": a_ref, "role": "prior_holder"}, {"ref": b_ref, "role": "subsequent_holder"}],
                object_label=st.label, object_ref=object_ref(st.track_id),
                uncertainties=[f"the {st.label} was not visible while it changed hands; this is inferred from possession "
                               f"before and after the contact",
                               f"the {st.label} seen with {b_ref} is assumed to be the same object (no appearance matching)"],
                dedup_key=f"vanish|{st.track_id}|{a}|{b}|{st.missing_since:.1f}"), "disappearance")
            return
        self._emit(events, RawEvent(
            event_type=UNCERTAIN_INTERACTION, severity=SEVERITY_INFO,
            onset_ts=st.missing_since, detected_ts=ts, score=0.35,
            observation=(f"A {st.label} carried by {a_ref} for {feats['held_before_s']:.0f} s stopped being "
                         f"detected while {b_ref} was within reach of it; {b_ref} then "
                         f"{'moved away' if b in persons else 'left the view'} and the {st.label} has not "
                         f"been detected with {a_ref} since."),
            features=feats,
            tracks=[{"ref": a_ref, "role": "prior_holder"}, {"ref": b_ref, "role": "in_contact"}],
            object_label=st.label, object_ref=object_ref(st.track_id),
            uncertainties=["the object was not detected after the contact, so where it went was not observed",
                           "a missed detection or the object being hidden by the body would look the same"],
            dedup_key=f"vanish|{st.track_id}|{a}|{b}|{st.missing_since:.1f}"), "disappearance")

    # ====================================================================================
    # stage 2
    # ====================================================================================
    def _verify_transfer(self, c: _Transfer, ts: float, persons: dict[int, Track],
                         objects: dict[int, Track], events: list[RawEvent]) -> None:
        cfg = self.cfg
        st = self.objs.get(c.obj_id)
        ho, ha, hb = self.hist.get(c.obj_id), self.hist.get(c.holder), self.hist.get(c.taker)
        elapsed = ts - c.t0

        def close(reason: str | None = None, feats: dict | None = None) -> None:
            self._transfers.remove(c)
            if st is not None:
                st.pending = False
            if reason:
                self._dismiss(ts, "transfer", reason, feats)

        if st is None or ho is None or ha is None or hb is None or not len(ho):
            close("track_ended_before_verification")
            return
        relinked = st.relinked_ts is not None and st.relinked_ts >= c.t0 - 0.5
        if elapsed < (cfg.relinked_min_verify_s if relinked else cfg.min_verify_s):
            return

        h = c.h_ref
        t_obj = ho.last_ts   # last real observation of the object
        pre0 = c.t0 - cfg.pre_window_s
        speed_before = ho.net_speed(pre0, c.t0 - 0.1) / h
        speed_after = ho.net_speed(c.t0, t_obj) / h
        holder_visible = c.holder in persons
        taker_visible = c.taker in persons
        f = {
            "object_speed_before": speed_before,
            "object_speed_after": speed_after,
            "speed_increase": speed_after - speed_before,
            "transfer_peak_speed": ho.max_window_speed(c.t0 - 0.4, min(ts, c.t0 + 0.6), 0.3) / h,
            "object_holder_separation": dist(ho.pos(t_obj), ha.pos(t_obj)) / h,
            "taker_holder_separation": dist(hb.pos(ts), ha.pos(ts)) / h,
            "taker_speed_after": hb.net_speed(c.t0, ts) / h,
            "holder_speed_before": ha.net_speed(pre0, c.t0) / h,
            "holder_speed_after": ha.net_speed(c.t0, ts) / h,
            "held_before_s": c.held_s,
            "companion_s": self._companion_time(ha, hb, c.t0, h),
            "person_overlap_max": self._max_person_iou(ha, hb, c.t0 - 0.5, c.t0 + 0.3),
            "object_observed_frac": self._observed_frac(ho, c.t0 - 0.5, ts),
            "camera_unstable_frac": self._unstable_frac(c.t0 - 0.5, ts),
            "elapsed_s": elapsed,
        }

        # The prior holder still has a freshly detected object where this one was: the
        # "transfer" was an occlusion plus an identity mix-up, not a movement.
        if holder_visible:
            for o in objects.values():
                if (o.track_id != c.obj_id and o.first_ts >= c.t0 - 0.5
                        and overlap_frac(o.box, self._reach_box(persons[c.holder])) >= cfg.assoc_min_overlap):
                    close("prior_holder_still_has_an_object", f)
                    return
        # Same question asked of the raw detections: a similar-sized bag keeps being detected
        # within the prior holder's reach after the transfer, even if no track claims it (an
        # intermittently detected bag needs consecutive hits to form a track).
        # If the bag was hidden while the two were in contact, the possession question is asked
        # after they separate (_verify_unobserved), not while they are still tangled together.
        hidden_exchange = (cfg.possession_checks and self._bag_dets is not None
                           and self._hidden_during_contact(ho, ha, hb, c.t0 - 2.0, c.opened_ts, c.h_ref))
        hits, examined = self._holder_bag_frames(c, ha, ho, ts)
        f["holder_bag_frames_after"] = float(hits)
        if hits >= 2 and not hidden_exchange:
            close("prior_holder_still_has_an_object", f)
            return
        if f["camera_unstable_frac"] > cfg.max_unstable_frac:
            if elapsed >= cfg.verify_window_s:
                close("camera_motion", f)
            return

        va, vb = ha.velocity(c.t0, ts), hb.velocity(c.t0, ts)
        pursuit = (f["holder_speed_after"] >= cfg.pursuit_speed
                   and f["holder_speed_after"] - f["holder_speed_before"] >= 0.6
                   and va[0] * vb[0] + va[1] * vb[1] > 0)
        abrupt = speed_after >= cfg.fast_speed and f["speed_increase"] >= cfg.speed_jump
        departed = (f["object_holder_separation"] >= cfg.separation_min
                    and f["taker_holder_separation"] >= cfg.separation_min - 0.4)
        if pursuit and f["object_holder_separation"] >= cfg.pursuit_separation_min:
            departed = True
        f["holder_followed"] = 1.0 if pursuit else 0.0

        # Hard doubts block a review-level alert; soft ones only lower the score and are
        # reported to the reviewer.
        uncertainties: list[str] = []
        hard_doubt = False
        if f["object_observed_frac"] < cfg.min_observed_frac:
            uncertainties.append("the object was detected in fewer than half of the frames around the event")
            hard_doubt = True
        if not holder_visible:
            uncertainties.append("the prior holder was not tracked after the event")
            hard_doubt = True
        overlapped = f["person_overlap_max"] >= cfg.ambiguous_iou
        if overlapped:
            uncertainties.append("the two people overlapped heavily in the image, so which track is which is less certain")
        if not taker_visible:
            uncertainties.append("the other person had left the view by decision time")
        # The taker was already carrying an object of their own, and that object stopped being
        # detected around the moment of the "transfer". Two similar objects passing each other
        # can swap track identities; the observed motion is then the taker's own object moving.
        swapped = [o for oid, o in self.objs.items()
                   if oid != c.obj_id and c.taker in (o.holder, o.assoc)
                   and o.missing_since is not None and o.missing_since >= c.t0 - 1.0]
        f["taker_own_object_lost"] = 1.0 if swapped else 0.0
        if swapped:
            uncertainties.append("the other person was already carrying a similar object, which stopped being "
                                 "detected at the same moment; the two objects may have been confused")
            hard_doubt = True
        if relinked:
            uncertainties.append("the object track was interrupted and re-linked by position")

        s = (0.30 * saturate(speed_after, cfg.fast_speed, cfg.fast_speed + 1.7)
             + 0.20 * saturate(f["speed_increase"], cfg.speed_jump, cfg.speed_jump + 1.4)
             + 0.25 * saturate(f["object_holder_separation"], cfg.separation_min, cfg.separation_min + 1.6)
             + 0.10 * (1.0 - saturate(f["companion_s"], 0.0, cfg.companion_full_s))
             + 0.10 * saturate(f["object_observed_frac"], 0.5, 1.0)
             + 0.05 * (1.0 if pursuit else 0.0))
        score = (0.55 + 0.45 * s) * (0.9 if relinked else 1.0) * (0.93 if overlapped else 1.0)
        if f["companion_s"] >= cfg.companion_full_s:
            uncertainties.append("the two people had been close together for several seconds beforehand")

        a_ref, b_ref = person_ref(c.holder), person_ref(c.taker)
        tracks = [{"ref": a_ref, "role": "prior_holder"}, {"ref": b_ref, "role": "subsequent_holder"}]
        common = dict(onset_ts=c.t0, detected_ts=ts, features=f, tracks=tracks, object_label=st.label,
                      object_ref=object_ref(c.obj_id),
                      dedup_key=f"transfer|{c.obj_id}|{c.holder}|{c.taker}|{c.t0:.1f}")

        # With raw detections available and the prior holder in view, look at a few frames
        # first: a bag still detected with them would mean nothing was taken.
        still_checking = (self._bag_dets is not None and holder_visible
                          and examined < cfg.holder_check_frames and elapsed < cfg.verify_window_s)
        if abrupt and departed and not hard_doubt and score >= cfg.review_min_score and not still_checking:
            obs = (f"A tracked {st.label} left the reach of {a_ref}, who had carried it for "
                   f"{c.held_s:.0f} s, and moved away with {b_ref} at {speed_after:.1f} body heights "
                   f"per second, ending {f['object_holder_separation']:.1f} body heights from {a_ref} "
                   f"after {elapsed:.1f} s.")
            if pursuit:
                obs += f" {a_ref} then moved quickly in the same direction."
            self._finish_transfer(c, st, ts, events, RawEvent(
                event_type=POSSIBLE_SNATCHING, severity=SEVERITY_REVIEW, score=score, observation=obs,
                uncertainties=uncertainties, **common))
            return

        # The object was mostly hidden while it left the holder (bodies in contact). Its motion
        # was not observed, so judge possession before vs after instead (see _verify_unobserved).
        if hidden_exchange:
            self._verify_unobserved(c, st, ts, persons, events, f, uncertainties, common, ha, hb, ho, close)
            return

        if elapsed < cfg.verify_window_s:
            return  # keep watching until the window closes

        if abrupt and departed:
            obs = (f"A tracked {st.label} moved quickly from {a_ref} to {b_ref} and the two separated, "
                   f"but the visual evidence is incomplete.")
            self._finish_transfer(c, st, ts, events, RawEvent(
                event_type=UNCERTAIN_INTERACTION, severity=SEVERITY_INFO, score=score * 0.7,
                observation=obs, uncertainties=uncertainties, **common))
            return
        if f["transfer_peak_speed"] >= cfg.fast_speed and f["speed_increase"] >= cfg.speed_jump * 0.5:
            obs = (f"A tracked {st.label} passed quickly from {a_ref} to {b_ref}; "
                   f"they stayed {f['taker_holder_separation']:.1f} body heights apart afterwards.")
            self._finish_transfer(c, st, ts, events, RawEvent(
                event_type=RAPID_OBJECT_TRANSFER, severity=SEVERITY_INFO,
                score=0.25 + 0.25 * saturate(f["transfer_peak_speed"], cfg.fast_speed, cfg.fast_speed + 2.0),
                observation=obs, uncertainties=uncertainties, **common))
            return
        close("gradual_transfer" if not abrupt else "no_separation_after_transfer", f)

    def _hidden_during_contact(self, ho: TrackHistory, ha: TrackHistory, hb: TrackHistory,
                               t0: float, t1: float, h: float) -> bool:
        """Was the object out of sight for >= 0.4 s while the two people were close together?"""
        times = [t for t, o in zip(ho.ts, ho.obs) if o and t <= t1 + 1e-6]
        prev = None
        for t in times:
            if prev is not None and t >= t0 and t - prev >= 0.4:
                mid = (t + prev) * 0.5
                if (ha.first_ts <= mid <= ha.last_ts + 0.2 and hb.first_ts <= mid <= hb.last_ts + 0.2
                        and dist(ha.pos(mid), hb.pos(mid)) / h <= self.cfg.companion_dist):
                    return True
            prev = t
        return False

    def _verify_unobserved(self, c: _Transfer, st: _Obj, ts: float, persons: dict[int, Track],
                           events: list[RawEvent], f: dict, uncertainties: list[str], common: dict,
                           ha: TrackHistory, hb: TrackHistory, ho: TrackHistory, close) -> None:
        """Unobserved change of possession.

        Evidence used: the holder carried the object; during contact with the other person it was
        mostly undetected; it was then detected with the other person (that is what opened the
        candidate). Wait until the two separate, then look at the holder for several frames. A bag
        still detected with them means nothing was taken. If none is, possession changed - which a
        calm hand-over between friends also produces, so only a fast departure makes it a review
        alert; otherwise it is logged at info level.
        """
        cfg = self.cfg
        h = c.h_ref
        deadline = c.opened_ts + cfg.possession_window_s
        taker_visible = c.taker in persons
        if c.separated_ts is None:
            if f["taker_holder_separation"] >= cfg.separation_min or (not taker_visible and ts - c.opened_ts >= 0.3):
                c.separated_ts = ts
            elif ts >= deadline:
                close("no_separation_after_unobserved_transfer", f)
                return
            else:
                return
        hits, examined = self._holder_bag_frames(c, ha, ho, ts, since=c.separated_ts - 0.05)
        f["holder_bag_frames_after_separation"] = float(hits)
        f["possession_frames_examined"] = float(examined)
        if hits >= 2:
            close("prior_holder_still_has_an_object", f)
            return
        if examined < cfg.possession_check_frames:
            if ts >= deadline + 2.0:
                close("prior_holder_not_observed_after_separation", f)
            return
        # Departure speed: the taker's own motion around separation, and the object's motion
        # after it reappeared. Both measured only from real observations.
        t_from = max(c.opened_ts, hb.last_ts - 1.0)
        taker_dep = hb.net_speed(t_from, hb.last_ts) / h
        obj_dep = ho.net_speed(c.opened_ts, ho.last_ts) / h
        f["taker_departure_speed"] = taker_dep
        f["object_departure_speed"] = obj_dep
        f["unobserved_transfer"] = 1.0
        a_ref, b_ref = person_ref(c.holder), person_ref(c.taker)
        notes = [u for u in uncertainties if "fewer than half" not in u] + [
            f"the {st.label} was not visible while it left {a_ref}; that it changed hands is inferred from it "
            f"being with {a_ref} before the contact, seen with {b_ref} after it, and not detected with {a_ref} since"]
        common = {**common, "features": f, "uncertainties": notes}
        if max(taker_dep, obj_dep) >= cfg.fast_speed and c.holder in persons:
            score = min(0.55 + 0.15 * saturate(max(taker_dep, obj_dep), cfg.fast_speed, cfg.fast_speed + 1.5), 0.70)
            obs = (f"A {st.label} carried by {a_ref} for {c.held_s:.0f} s was out of view while {a_ref} and {b_ref} "
                   f"were in contact; it was then seen with {b_ref}, who moved away at "
                   f"{max(taker_dep, obj_dep):.1f} body heights per second, and it has not been detected with "
                   f"{a_ref} in {examined} frames since.")
            self._finish_transfer(c, st, ts, events, RawEvent(
                event_type=POSSIBLE_SNATCHING, severity=SEVERITY_REVIEW, score=score, observation=obs,
                **{k: v for k, v in common.items() if k != "uncertainties"}, uncertainties=notes))
        else:
            obs = (f"A {st.label} left {a_ref} while {a_ref} and {b_ref} were in contact and was then seen with "
                   f"{b_ref}; the exchange itself was not visible and {b_ref} left at walking pace.")
            self._finish_transfer(c, st, ts, events, RawEvent(
                event_type=UNCERTAIN_INTERACTION, severity=SEVERITY_INFO, score=0.3, observation=obs,
                **{k: v for k, v in common.items() if k != "uncertainties"}, uncertainties=notes))

    def _finish_transfer(self, c: _Transfer, st: _Obj, ts: float, events: list[RawEvent], ev: RawEvent) -> None:
        self._transfers.remove(c)
        st.pending = False
        pair = f"pair:{min(c.holder, c.taker)}:{max(c.holder, c.taker)}"
        if self._cooling(pair, ts):
            self._dismiss(ts, "transfer", "duplicate_within_cooldown", ev.features)
            return
        self._cool(pair, ts, self.cfg.cooldown_s)
        self._cool(f"obj:{c.obj_id}", ts, self.cfg.cooldown_s)
        self._emit(events, ev, "transfer")

    def _bag_frames_near(self, hp: TrackHistory, since: float, ts: float, ref: Box,
                         exclude: TrackHistory | None) -> tuple[int, int]:
        """(frames with a similar-sized bag detection within the person's reach, frames examined)
        in (since, ts]. Only frames in which the person was observed are examined; detections
        overlapping ``exclude`` (the moving object itself) are ignored."""
        if not self._bag_dets:
            return 0, 0
        ref_size = max(ref[2] - ref[0], ref[3] - ref[1], 1.0)
        frames = examined = 0
        for t, boxes in self._bag_dets:
            if t <= since or t > ts or t < hp.first_ts:
                continue
            i = hp._idx(t)
            if abs(hp.ts[i] - t) > 0.15 or not hp.obs[i]:
                continue
            examined += 1
            pb = hp.boxes[i]
            ph = pb[3] - pb[1]
            reach = expand(pb, self.cfg.reach * ph, 0.05 * ph, 0.10 * ph)
            obj_box = exclude.box_at(t) if exclude is not None and exclude.first_ts <= t <= exclude.last_ts + 1e-6 else None
            for b in boxes:
                size = max(b[2] - b[0], b[3] - b[1], 1.0)
                if not (0.5 <= size / ref_size <= 2.0):
                    continue
                if obj_box is not None and iou(b, obj_box) >= 0.3:
                    continue
                if overlap_frac(b, reach) >= self.cfg.assoc_min_overlap:
                    frames += 1
                    break
        return frames, examined

    def _holder_bag_frames(self, c: _Transfer, ha: TrackHistory, ho: TrackHistory, ts: float,
                           since: float | None = None) -> tuple[int, int]:
        """(frames with a bag detection other than the moving object within the prior holder's
        reach, frames examined) since the transfer (or since ``since``). Only frames in which
        the holder was actually observed are examined."""
        if not self._bag_dets or not len(ho):
            return 0, 0
        ref = ho.box_at(c.t0)
        ref_size = max(ref[2] - ref[0], ref[3] - ref[1], 1.0)
        frames = examined = 0
        for t, boxes in self._bag_dets:
            # Skip the first 0.3 s: the grabbed object is still leaving the holder's reach.
            start = since if since is not None else c.t0 + 0.3
            if t <= start or t > ts or t < ha.first_ts:
                continue
            i = ha._idx(t)
            if abs(ha.ts[i] - t) > 0.15 or not ha.obs[i]:
                continue                         # the holder was not observed in this frame
            examined += 1
            holder_box = ha.box_at(t)
            ph = holder_box[3] - holder_box[1]
            reach = expand(holder_box, self.cfg.reach * ph, 0.05 * ph, 0.10 * ph)
            obj_box = ho.box_at(t) if ho.first_ts <= t <= ho.last_ts + 1e-6 else None
            for b in boxes:
                size = max(b[2] - b[0], b[3] - b[1], 1.0)
                if not (0.5 <= size / ref_size <= 2.0):
                    continue
                if obj_box is not None and iou(b, obj_box) >= 0.3:
                    continue                     # that is the moving object itself
                if overlap_frac(b, reach) >= self.cfg.assoc_min_overlap:
                    frames += 1
                    break
        return frames, examined

    def _companion_time(self, ha: TrackHistory, hb: TrackHistory, t0: float, h: float) -> float:
        """Seconds during which the two people were close together before the event."""
        cfg = self.cfg
        total, prev = 0.0, None
        for i in hb.samples_between(t0 - cfg.companion_window_s, t0 - 0.5):
            t = hb.ts[i]
            if t < ha.first_ts:
                continue
            near = dist((hb.x[i], hb.y[i]), ha.pos(t)) / h <= cfg.companion_dist
            if near and prev is not None:
                total += min(t - prev, 0.5)
            prev = t if near else None
        return total

    @staticmethod
    def _max_person_iou(ha: TrackHistory, hb: TrackHistory, t0: float, t1: float) -> float:
        best = 0.0
        for i in hb.samples_between(t0, t1):
            t = hb.ts[i]
            if ha.first_ts <= t <= ha.last_ts + 1e-6:
                best = max(best, iou(hb.boxes[i], ha.box_at(t)))
        return best

    def _verify_pickup(self, c: _Pickup, ts: float, persons: dict[int, Track], events: list[RawEvent]) -> None:
        cfg = self.cfg
        st = self.objs.get(c.obj_id)
        ho, hp = self.hist.get(c.obj_id), self.hist.get(c.taker)
        if st is None or ho is None or hp is None or not len(ho):
            self._pickups.remove(c)
            if st is not None:
                st.pending = False
            self._dismiss(ts, "pickup", "track_ended_before_verification")
            return
        h = c.h_ref
        moved = dist(ho.pos(ts), c.rest.pos) / h
        obj_lost = st.missing_since is not None
        if obj_lost and c.taker in persons:
            moved = max(moved, dist(hp.pos(ts), hp.pos(c.opened_ts)) / h)
        with_taker = st.assoc == c.taker or st.holder == c.taker or obj_lost
        feats = {"object_displacement": moved, "unattended_s": c.rest.last_still_ts - c.rest.since,
                 "taker_speed_after": hp.net_speed(c.t0, ts) / h, "elapsed_s": ts - c.t0,
                 "object_observed_frac": self._observed_frac(ho, c.t0, ts),
                 "camera_unstable_frac": self._unstable_frac(c.t0 - 0.5, ts)}
        if moved >= cfg.carry_min and with_taker:
            self._pickups.remove(c)
            if feats["camera_unstable_frac"] > cfg.max_unstable_frac:
                self._dismiss(ts, "pickup", "camera_motion", feats)
            else:
                self._decide_removal(ts, st, c.taker, c.rest, h, persons, events, feats,
                                     observed=not obj_lost, onset=c.t0)
            self._hand_to(st, c.taker, ts)
            return
        if ts - c.t0 >= cfg.removal_window_s:
            self._pickups.remove(c)
            self._dismiss(ts, "pickup", "moved_only_a_short_distance", feats)
            st.pending = False
            st.handlers.add(c.taker)
            st.rest, st.still_since = None, None

    def _decide_removal(self, ts: float, st: _Obj, taker: int, rest: _Rest, h: float,
                        persons: dict[int, Track], events: list[RawEvent], feats: dict,
                        observed: bool, onset: float) -> None:
        cfg = self.cfg
        key = f"obj:{st.track_id}"
        if self._cooling(key, ts):
            self._dismiss(ts, "removal", "duplicate_within_cooldown", feats)
            return
        if taker in st.handlers:
            self._dismiss(ts, "removal", "picked_up_by_a_track_that_handled_it_before", feats)
            return
        zone = self._monitored_zone(rest)
        th = self.hist.get(taker)
        present = [p for p in st.handlers if p != taker and p in persons]
        t_ref = person_ref(taker)
        tracks = [{"ref": t_ref, "role": "subsequent_holder"}]
        uncertainties: list[str] = []
        if not observed:
            uncertainties.append("the object was not detected while it was being carried")
        unattended_s = feats.get("unattended_s", 0.0)
        speed = feats.get("taker_speed_after", 0.0)
        score = (0.55 + 0.20 * saturate(speed, 0.8, 2.5) + 0.10 * saturate(unattended_s, 3.0, 20.0)
                 + (0.10 if observed else 0.0))

        event_type, severity, obs = None, SEVERITY_REVIEW, ""
        if present and th is not None:
            dep = present[0]
            hd = self.hist[dep]
            gap_now = dist(th.pos(ts), hd.pos(ts)) / h
            gap_then = dist(th.pos(onset), hd.pos(onset)) / h
            feats = {**feats, "distance_to_prior_holder": gap_now, "distance_change": gap_now - gap_then}
            if gap_now < 1.0 or gap_now < gap_then - 0.2:
                if not zone:
                    self._dismiss(ts, "removal", "carried_toward_or_stayed_with_prior_holder", feats)
                    return
            else:
                tracks.insert(0, {"ref": person_ref(dep), "role": "prior_holder"})
                event_type = SUSPICIOUS_OBJECT_REMOVAL
                obs = (f"A {st.label} that had been stationary and unattended for {unattended_s:.0f} s was "
                       f"lifted and carried away by {t_ref}. It had been set down by a different track, "
                       f"{person_ref(dep)}, which was still in view {gap_now:.1f} body heights away.")
        if event_type is None and zone is not None:
            event_type = SUSPICIOUS_OBJECT_REMOVAL
            obs = (f"A {st.label} that had been stationary for {unattended_s:.0f} s inside monitored zone "
                   f"'{zone.zone_id}' was removed by {t_ref}, a track that had not been seen handling it.")
            uncertainties.append("who placed the object is not known to the system")
        if event_type is None:
            if not cfg.emit_unknown_origin_pickups:
                self._dismiss(ts, "removal", "no_other_handler_in_view_and_not_a_monitored_zone", feats)
                return
            event_type, severity, score = UNCERTAIN_INTERACTION, SEVERITY_INFO, score * 0.6
            obs = (f"A {st.label} that had been stationary for {unattended_s:.0f} s was picked up by {t_ref}. "
                   f"The system has no information about who placed it.")
        if not observed:
            score *= 0.8
        self._cool(key, ts, cfg.cooldown_s)
        self._emit(events, RawEvent(
            event_type=event_type, severity=severity, onset_ts=onset, detected_ts=ts, score=score,
            observation=obs, features=feats, tracks=tracks, object_label=st.label,
            object_ref=object_ref(st.track_id), uncertainties=uncertainties,
            zone_id=zone.zone_id if zone else None,
            dedup_key=f"removal|{st.track_id}|{taker}|{onset:.1f}"), "removal")

    # ====================================================================================
    # zones
    # ====================================================================================
    def _update_zones(self, ts: float, persons: dict[int, Track], events: list[RawEvent]) -> None:
        cfg = self.cfg
        if not self.zones or not self._frame_size:
            return
        w, h = self._frame_size
        for z in self.zones:
            if z.kind != "restricted":
                continue
            for pid, p in persons.items():
                if p.lost_s > 0:
                    continue
                fx, fy = foot_point(p.box)
                key = (pid, z.zone_id)
                zs = self._zone_state.get(key)
                if not point_in_polygon((fx / w, fy / h), z.polygon):
                    continue
                if zs is None or ts - zs.last > cfg.zone_gap_s:
                    emitted = zs.emitted if zs else False
                    zs = self._zone_state[key] = _ZoneDwell(enter=ts, last=ts, hits=0, emitted=emitted)
                zs.last = ts
                zs.hits += 1
                dwell = ts - zs.enter
                if zs.emitted or dwell < cfg.zone_dwell_s or zs.hits < cfg.zone_min_hits:
                    continue
                zs.emitted = True
                feats = {"dwell_s": dwell, "frames_inside": float(zs.hits), "track_score": p.score}
                if self._cooling(f"zone:{z.zone_id}", ts):
                    self._dismiss(ts, "zone", "duplicate_within_cooldown", feats)
                    continue
                self._cool(f"zone:{z.zone_id}", ts, cfg.zone_cooldown_s)
                self._emit(events, RawEvent(
                    event_type=POSSIBLE_RESTRICTED_AREA_ENTRY, severity=SEVERITY_REVIEW,
                    onset_ts=zs.enter, detected_ts=ts,
                    score=0.55 + 0.25 * saturate(p.score, 0.4, 0.9) + 0.15 * saturate(dwell, 1.0, 3.0),
                    observation=(f"{person_ref(pid)} stood inside configured zone '{z.zone_id}' for "
                                 f"{dwell:.1f} s. Whether this entry was permitted is not known to the system."),
                    features=feats, tracks=[{"ref": person_ref(pid), "role": "in_zone"}], zone_id=z.zone_id,
                    dedup_key=f"zone|{z.zone_id}|{pid}|{zs.enter:.1f}"), "zone")

    # ====================================================================================
    def _cleanup(self, ts: float, tracks: list[Track]) -> None:
        cfg = self.cfg
        live = {t.track_id for t in tracks}
        for tid in list(self.hist):
            h = self.hist[tid]
            h.prune(ts - cfg.history_s)
            if tid not in live and (not len(h) or ts - h.last_ts > cfg.history_s):
                del self.hist[tid]
        for oid in list(self.objs):
            st = self.objs[oid]
            if st.missing_since is None or st.pending:
                continue
            gone = ts - st.missing_since
            keep = 30.0 if st.rest is not None else (
                cfg.absent_confirm_s + 6.0 if st.holder is not None and cfg.emit_contact_disappearance
                else cfg.relink_window_s + 1.0)
            if gone > keep:
                del self.objs[oid]
        for key in [k for k, z in self._zone_state.items() if ts - z.last > cfg.history_s]:
            del self._zone_state[key]
        for key in [k for k, until in self._cooldowns.items() if ts >= until]:
            del self._cooldowns[key]


def _round(features: dict) -> dict:
    return {k: (round(float(v), 3) if isinstance(v, (int, float)) else v) for k, v in features.items()}
