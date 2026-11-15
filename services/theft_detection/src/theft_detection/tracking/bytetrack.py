"""ByteTrack-style multi-object tracker (Zhang et al., ECCV 2022; reference code is MIT).

This is an independent NumPy implementation of the ByteTrack association idea - match
high-score detections first, then use low-score detections to keep existing tracks alive -
with three adaptations for this module:

* time-aware Kalman prediction, because the analysed frame rate is variable (frames are
  sampled and may be dropped);
* association is done per category, so a person can never be matched to a bag;
* bags are small and move fast relative to their size, so they use a buffered IoU and a
  distance-gated recovery step instead of plain IoU.

Track IDs are ephemeral and local to one stream. No appearance features are extracted, so
the tracker cannot re-identify anyone after they leave the view.
"""
from __future__ import annotations

from collections import Counter

import numpy as np

from ..config import TrackerConfig
from ..types import BAG, PERSON, Box, Detection, Track
from .assignment import linear_assignment


class _KalmanBox:
    """Constant-velocity filter on (cx, cy, w, h); velocities are in pixels per second."""

    def __init__(self, box: Box, accel_sizes: float) -> None:
        cx, cy = (box[0] + box[2]) * 0.5, (box[1] + box[3]) * 0.5
        w, h = box[2] - box[0], box[3] - box[1]
        self.x = np.array([cx, cy, w, h, 0.0, 0.0, 0.0, 0.0])
        s = max(w, h, 1.0)
        self.P = np.diag([(0.1 * s) ** 2] * 4 + [(1.0 * s) ** 2] * 4)
        self._accel = accel_sizes

    def predict(self, dt: float) -> None:
        dt = max(dt, 1e-3)
        F = np.eye(8)
        F[:4, 4:] = np.eye(4) * dt
        s = max(self.x[2], self.x[3], 1.0)
        q_pos = (0.05 * s) ** 2 * dt
        q_vel = (self._accel * s) ** 2 * dt
        self.x = F @ self.x
        self.P = F @ self.P @ F.T + np.diag([q_pos] * 4 + [q_vel] * 4)
        self.x[2] = max(self.x[2], 1.0)
        self.x[3] = max(self.x[3], 1.0)

    def update(self, box: Box) -> None:
        z = np.array(
            [(box[0] + box[2]) * 0.5, (box[1] + box[3]) * 0.5, box[2] - box[0], box[3] - box[1]]
        )
        s = max(z[2], z[3], 1.0)
        R = np.diag([(0.05 * s) ** 2] * 4)
        H = np.zeros((4, 8))
        H[:4, :4] = np.eye(4)
        S = H @ self.P @ H.T + R
        K = self.P @ H.T @ np.linalg.inv(S)
        self.x = self.x + K @ (z - H @ self.x)
        self.P = (np.eye(8) - K @ H) @ self.P

    @property
    def box(self) -> Box:
        cx, cy, w, h = self.x[:4]
        return (float(cx - w / 2), float(cy - h / 2), float(cx + w / 2), float(cy + h / 2))


class _STrack:
    __slots__ = ("track_id", "category", "kf", "score", "hits", "first_ts", "last_obs_ts",
                 "labels", "confirmed", "last_obs_box")

    def __init__(self, track_id: int, det: Detection, ts: float) -> None:
        self.track_id = track_id
        self.category = det.category
        self.kf = _KalmanBox(det.box, accel_sizes=2.0 if det.category == PERSON else 8.0)
        self.score = det.score
        self.hits = 1
        self.first_ts = ts
        self.last_obs_ts = ts
        self.last_obs_box = det.box
        self.labels: Counter[str] = Counter({det.label: 1})
        self.confirmed = False

    def mark_matched(self, det: Detection, ts: float, min_hits: int) -> None:
        self.kf.update(det.box)
        self.score = det.score
        self.hits += 1
        self.last_obs_ts = ts
        self.last_obs_box = det.box
        self.labels[det.label] += 1
        if self.hits >= min_hits:
            self.confirmed = True

    @property
    def label(self) -> str:
        return self.labels.most_common(1)[0][0]


def _iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)))
    x1 = np.maximum(a[:, None, 0], b[None, :, 0])
    y1 = np.maximum(a[:, None, 1], b[None, :, 1])
    x2 = np.minimum(a[:, None, 2], b[None, :, 2])
    y2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    return inter / np.maximum(area_a[:, None] + area_b[None, :] - inter, 1e-9)


def _buffer(boxes: np.ndarray, ratio: float) -> np.ndarray:
    if len(boxes) == 0 or ratio <= 0:
        return boxes
    w = (boxes[:, 2] - boxes[:, 0]) * ratio * 0.5
    h = (boxes[:, 3] - boxes[:, 1]) * ratio * 0.5
    return np.stack([boxes[:, 0] - w, boxes[:, 1] - h, boxes[:, 2] + w, boxes[:, 3] + h], axis=1)


class ByteTracker:
    def __init__(self, cfg: TrackerConfig | None = None) -> None:
        self.cfg = cfg or TrackerConfig()
        self._tracks: list[_STrack] = []
        self._next_id = 1
        self._last_ts: float | None = None

    def reset(self) -> None:
        self._tracks.clear()
        self._next_id = 1
        self._last_ts = None

    def update(self, detections: list[Detection], ts: float) -> list[Track]:
        dt = 0.0 if self._last_ts is None else max(ts - self._last_ts, 0.0)
        self._last_ts = ts
        if dt > 0:
            for t in self._tracks:
                t.kf.predict(dt)

        for category in (PERSON, BAG):
            self._update_category(category, [d for d in detections if d.category == category], ts)

        max_lost = self.cfg.max_lost_s
        self._tracks = [
            t for t in self._tracks
            if (ts - t.last_obs_ts) <= max_lost.get(t.category, 1.5)
            and (t.confirmed or t.last_obs_ts == ts)  # unconfirmed tracks die on their first miss
        ]
        return [
            Track(
                track_id=t.track_id, category=t.category, label=t.label, box=t.kf.box,
                score=t.score, hits=t.hits, first_ts=t.first_ts, lost_s=ts - t.last_obs_ts,
            )
            for t in self._tracks if t.confirmed
        ]

    # ------------------------------------------------------------------------------------
    def _update_category(self, category: str, dets: list[Detection], ts: float) -> None:
        cfg = self.cfg
        high_thr = cfg.high_thresh.get(category, 0.5)
        new_thr = cfg.new_track_thresh.get(category, 0.6)
        buf = cfg.bag_iou_buffer if category == BAG else cfg.person_iou_buffer

        tracks = [t for t in self._tracks if t.category == category]
        confirmed = [t for t in tracks if t.confirmed]
        tentative = [t for t in tracks if not t.confirmed]
        high = [d for d in dets if d.score >= high_thr]
        low = [d for d in dets if cfg.low_thresh <= d.score < high_thr]

        # 1) confirmed tracks (tracked + lost) vs high-score detections
        m, un_t, un_d = self._associate(confirmed, high, 1.0 - cfg.match_iou, buf)
        for ti, di in m:
            confirmed[ti].mark_matched(high[di], ts, cfg.min_hits)
        remaining_tracks = [confirmed[i] for i in un_t]
        remaining_high = [high[i] for i in un_d]

        # 2) still-unmatched tracks vs low-score detections (the "BYTE" step)
        m, un_t, _ = self._associate(remaining_tracks, low, 1.0 - cfg.low_match_iou, buf)
        for ti, di in m:
            remaining_tracks[ti].mark_matched(low[di], ts, cfg.min_hits)
        remaining_tracks = [remaining_tracks[i] for i in un_t]

        # 3) tentative tracks vs remaining high-score detections
        m, un_t, un_d = self._associate(tentative, remaining_high, 1.0 - cfg.match_iou, buf)
        for ti, di in m:
            tentative[ti].mark_matched(remaining_high[di], ts, cfg.min_hits)
        tentative = [tentative[i] for i in un_t]
        remaining_high = [remaining_high[i] for i in un_d]

        # 4) motion-gated matching where IoU is zero. A grabbed bag moves several times its
        #    own size between analysed frames, and a runner more than their own box width, so
        #    neither a brand-new track (no velocity yet) nor a suddenly accelerating one can be
        #    matched by overlap. The gate is the furthest the object could plausibly have moved.
        candidates = tentative + [
            t for t in remaining_tracks
            if category == BAG or ts - t.last_obs_ts <= cfg.person_recover_max_gap_s
        ]
        if candidates and remaining_high:
            remaining_high = self._match_by_motion_gate(category, candidates, remaining_high, ts)

        # 5) start new tracks
        for d in remaining_high:
            if d.score >= new_thr:
                self._tracks.append(_STrack(self._next_id, d, ts))
                self._next_id += 1

    def _associate(self, tracks: list[_STrack], dets: list[Detection], max_cost: float, buf: float):
        if not tracks or not dets:
            return [], list(range(len(tracks))), list(range(len(dets)))
        tb = np.array([t.kf.box for t in tracks], dtype=float)
        db = np.array([d.box for d in dets], dtype=float)
        cost = 1.0 - _iou_matrix(_buffer(tb, buf), _buffer(db, buf))
        return linear_assignment(cost, max_cost)

    def _near_person(self, box) -> bool:
        for t in self._tracks:
            if t.category != PERSON or t.last_obs_ts != self._last_ts:
                continue
            pb = t.kf.box
            ph = pb[3] - pb[1]
            reach = (pb[0] - 0.3 * ph, pb[1] - 0.05 * ph, pb[2] + 0.3 * ph, pb[3] + 0.1 * ph)
            w = min(box[2], reach[2]) - max(box[0], reach[0])
            h = min(box[3], reach[3]) - max(box[1], reach[1])
            area = max((box[2] - box[0]) * (box[3] - box[1]), 1e-6)
            if w > 0 and h > 0 and w * h / area >= 0.3:
                return True
        return False

    def _match_by_motion_gate(self, category: str, tracks: list[_STrack], dets: list[Detection],
                              ts: float) -> list[Detection]:
        cfg = self.cfg
        cost = np.full((len(tracks), len(dets)), 1e6)
        for i, t in enumerate(tracks):
            b = t.last_obs_box
            size = max(b[2] - b[0], b[3] - b[1], 1.0)
            gap = max(ts - t.last_obs_ts, 0.0)
            if category == BAG:
                gate = size * min(cfg.bag_recover_sizes + cfg.bag_recover_sizes_per_s * gap,
                                  cfg.bag_recover_max_sizes)
                lo, hi = 0.5, 2.0
            else:
                gate = size * (0.25 + cfg.person_max_speed * gap)
                lo, hi = 0.7, 1.4
            tcx, tcy = (b[0] + b[2]) * 0.5, (b[1] + b[3]) * 0.5
            for j, d in enumerate(dets):
                dsize = max(d.box[2] - d.box[0], d.box[3] - d.box[1], 1.0)
                if not (lo <= dsize / size <= hi):
                    continue  # a very different size is unlikely to be the same object
                dd = float(np.hypot((d.box[0] + d.box[2]) * 0.5 - tcx, (d.box[1] + d.box[3]) * 0.5 - tcy))
                if dd > gate:
                    continue
                # A bag unseen for a while may only jump far if it reappears next to a person: carried
                # bags move with people. Otherwise one spurious detection elsewhere can capture the
                # track of a bag that is merely hidden.
                if (category == BAG and cfg.bag_recover_requires_person and gap > 0.3 and dd > size * cfg.bag_recover_sizes
                        and not self._near_person(d.box)):
                    continue
                cost[i, j] = dd / gate
        matches, _, un_d = linear_assignment(cost, 1.0)
        for ti, di in matches:
            t = tracks[ti]
            # Re-seed the filter at the new position with the velocity implied by the jump:
            # the previous estimate is stale (or, for a new track, does not exist yet).
            old, gap = t.last_obs_box, max(ts - t.last_obs_ts, 1e-3)
            new = dets[di].box
            t.kf = _KalmanBox(new, accel_sizes=2.0 if category == PERSON else 8.0)
            t.kf.x[4] = ((new[0] + new[2]) - (old[0] + old[2])) * 0.5 / gap
            t.kf.x[5] = ((new[1] + new[3]) - (old[1] + old[3])) * 0.5 / gap
            t.mark_matched(dets[di], ts, cfg.min_hits)
        return [dets[i] for i in un_d]
