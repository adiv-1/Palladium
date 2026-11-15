"""Detector-first cascade: cheap rules on every sampled frame, expensive models only on flags.

Tier 0  detector + ByteTrack on every sampled frame (ml.adapters.ByteTracker)
Rules   zones, dwell timers and track events (this module, CPU only)
Tier 1  pose estimation only for tracks the rules flag
Tier 2  the Qwen visual verifier only for rule candidates, with one narrow question each
Tier 3  a human reviewer decides; nothing here dispatches anyone

Tracks are ephemeral per-camera IDs with no appearance features. Nothing in this module
recognises faces, re-identifies people across cameras or reads plates.
"""
import asyncio
import json
import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

from apps.api.schemas import Detection

# COCO keypoint indices used by the pose rules.
L_SHOULDER, R_SHOULDER, L_WRIST, R_WRIST = 5, 6, 9, 10
L_HIP, R_HIP, L_KNEE, R_KNEE = 11, 12, 13, 14
KEYPOINT_MIN_CONF = 0.3

INCIDENT_LABELS = {
    'person_down': 'Person down',
    'altercation': 'Physical altercation',
    'rack_activity': 'Activity at bike rack',
}

# One narrow question per incident type for the Tier 2 verifier. Open-ended "is anything
# anomalous?" prompting is close to chance on published benchmarks; a specific question is not.
VERIFIER_QUESTIONS = {
    'person_down': 'Is a person lying on the ground and not getting up across these frames?',
    'altercation': 'Are two or more people striking, shoving or grappling with each other?',
    'rack_activity': ('Is a person working at a locked bicycle at the rack, or removing a bicycle '
                      'from it? Ownership cannot be judged from video; describe only the actions.'),
}

UNCERTAINTIES = {
    'person_down': 'Rule match on posture and stillness; someone resting or sitting low can trigger it.',
    'altercation': 'Rule match on proximity and fast movement; play, sport or dancing can trigger it.',
    'rack_activity': ('Rule match on dwell and a bicycle leaving the rack; owners unlocking their own '
                      'bicycles trigger it too. Ownership is not and cannot be determined.'),
}


@dataclass
class RuleConfig:
    down_seconds: float = 10
    down_aspect: float = 1.15         # box width / height above this reads as horizontal without pose
    down_motion: float = 0.15         # max centre drift over the window, in box diagonals
    fight_window_seconds: float = 2.0
    fight_min_samples: int = 3
    fight_speed: float = 0.8          # combined motion of the pair, in body heights per second
    fight_proximity: float = 0.9      # centre distance below this many body heights reads as close
    rack_dwell_seconds: float = 45
    rack_parked_seconds: float = 10   # a bicycle must sit in the zone this long to count as parked
    rack_gone_seconds: float = 3      # a parked bicycle unseen this long counts as gone
    rack_window_seconds: float = 30   # the dwell must overlap the removal within this window
    crouch_aspect: float = 1.4        # box height / width below this reads as crouched without pose
    cooldown_seconds: float = 30
    max_pose_per_frame: int = 4
    zones: list = field(default_factory=list)  # rack polygons, normalised [x, y] points


def parse_zones(raw):
    """RACK_ZONES is a JSON list of polygons, each a list of [x, y] points in 0..1 frame units."""
    if not raw or not raw.strip():
        return []
    zones = json.loads(raw)
    if not isinstance(zones, list):
        raise ValueError('RACK_ZONES must be a JSON list of polygons')
    for zone in zones:
        if (not isinstance(zone, list) or len(zone) < 3 or
                any(not isinstance(p, list) or len(p) != 2 or not all(0 <= v <= 1 for v in p) for p in zone)):
            raise ValueError('Each rack zone needs at least 3 [x, y] points between 0 and 1')
    return [[(float(x), float(y)) for x, y in zone] for zone in zones]


def inside(point, polygon):
    x, y = point
    hit = False
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            hit = not hit
    return hit


@dataclass
class Candidate:
    incident_type: str
    actors: list
    t_start: float
    t_end: float
    score: float
    evidence: list
    question: str

    def observation(self):
        return self.evidence[0]


@dataclass
class Sample:
    t: float
    box: tuple
    keypoints: list | None


def _center(box):
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def _height(box):
    return max(1.0, box[3] - box[1])


def _width(box):
    return max(1.0, box[2] - box[0])


def _point(keypoints, *indices):
    pts = [keypoints[i] for i in indices if keypoints and len(keypoints) > i and keypoints[i][2] >= KEYPOINT_MIN_CONF]
    if len(pts) != len(indices):
        return None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def horizontal(sample, config):
    """Torso closer to horizontal than vertical, from pose when available, else box shape."""
    shoulders = _point(sample.keypoints, L_SHOULDER, R_SHOULDER)
    hips = _point(sample.keypoints, L_HIP, R_HIP)
    if shoulders and hips:
        return abs(shoulders[0] - hips[0]) > abs(shoulders[1] - hips[1])
    return _width(sample.box) / _height(sample.box) > config.down_aspect


def crouched(sample, config):
    """Hips dropped to near knee height, from pose when available, else a squat box."""
    shoulders = _point(sample.keypoints, L_SHOULDER, R_SHOULDER)
    hips = _point(sample.keypoints, L_HIP, R_HIP)
    knees = _point(sample.keypoints, L_KNEE, R_KNEE)
    if shoulders and hips and knees:
        torso = max(1.0, abs(hips[1] - shoulders[1]))
        return knees[1] - hips[1] < 0.5 * torso
    return _height(sample.box) / _width(sample.box) < config.crouch_aspect


def _motion(previous, current):
    """Movement between two samples in body heights per second; wrists count when visible."""
    dt = current.t - previous.t
    if dt <= 0:
        return 0.0
    scale = (_height(previous.box) + _height(current.box)) / 2
    (x1, y1), (x2, y2) = _center(previous.box), _center(current.box)
    speed = math.hypot(x2 - x1, y2 - y1)
    for wrist in (L_WRIST, R_WRIST):
        a, b = _point(previous.keypoints, wrist), _point(current.keypoints, wrist)
        if a and b:
            speed = max(speed, math.hypot(b[0] - a[0], b[1] - a[1]))
    return speed / scale / dt


class RulesEngine:
    """Turns anonymous track boxes into incident candidates. Pure Python, no models."""

    def __init__(self, config=None):
        self.config = config or RuleConfig()
        self.history = defaultdict(deque)   # track id -> Samples
        self.last_seen = {}
        self.kind = {}
        self.zone_since = {}                # bike id -> stream time it entered a rack zone
        self.dwells = {}                    # person id -> [entered rack zone, last seen in it]
        self.parked = {}                    # bike id -> (zone index, parked since)
        self.removals = deque()             # (time, zone index, bike id)
        self.fight_hits = defaultdict(deque)
        self.cooldown = {}
        horizon = max(self.config.down_seconds, self.config.rack_dwell_seconds, self.config.rack_window_seconds)
        self.horizon = horizon + 5
        self.frame_size = (1.0, 1.0)

    # -- helpers -----------------------------------------------------------------
    def _norm(self, point):
        w, h = self.frame_size
        return (point[0] / w, point[1] / h)

    def _zone_of(self, point):
        p = self._norm(point)
        for index, polygon in enumerate(self.config.zones):
            if inside(p, polygon):
                return index
        return None

    def _cooled(self, key, now):
        if now - self.cooldown.get(key, -1e9) < self.config.cooldown_seconds:
            return False
        self.cooldown[key] = now
        return True

    def _box_motion(self, track, now):
        samples = self.history.get(track['id'])
        if not samples:
            return 0.0
        return _motion(Sample(samples[-1].t, samples[-1].box, None), Sample(now, tuple(track['box']), None))

    def wants_pose(self, tracks, now):
        """Tier 1 gate: pose only for people who already look down or crouched, are at a rack,
        or are close to someone while moving fast. Standing near someone is not enough; on a
        busy walkway that would put pose on almost everyone."""
        people = [t for t in tracks if t.get('kind', 'person') == 'person']
        wanted = []
        for track in people:
            box = track['box']
            sample = Sample(0, tuple(box), None)
            if (horizontal(sample, self.config) or crouched(sample, self.config) or
                    self._zone_of(((box[0] + box[2]) / 2, box[3])) is not None):
                wanted.append(track['id'])
        moving = {t['id'] for t in people if self._box_motion(t, now) >= self.config.fight_speed / 2}
        for i, a in enumerate(people):
            for b in people[i + 1:]:
                if self._close(a['box'], b['box']) and (a['id'] in moving or b['id'] in moving):
                    wanted += [a['id'], b['id']]
        unique = list(dict.fromkeys(wanted))
        return unique[:self.config.max_pose_per_frame]

    def _close(self, a, b):
        (ax, ay), (bx, by) = _center(a), _center(b)
        scale = (_height(a) + _height(b)) / 2
        return math.hypot(ax - bx, ay - by) < self.config.fight_proximity * scale

    # -- main entry ----------------------------------------------------------------
    def update(self, tracks, now, frame_size=None):
        if frame_size:
            self.frame_size = (float(frame_size[0]), float(frame_size[1]))
        for track in tracks:
            tid = track['id']
            self.kind[tid] = track.get('kind', 'person')
            self.history[tid].append(Sample(now, tuple(track['box']), track.get('keypoints')))
            self.last_seen[tid] = now
        self._expire(now)
        candidates = []
        candidates += self._person_down(now)
        candidates += self._altercation(tracks, now)
        if self.config.zones:
            candidates += self._rack(tracks, now)
        return candidates

    def _expire(self, now):
        for tid in list(self.history):
            samples = self.history[tid]
            while samples and now - samples[0].t > self.horizon:
                samples.popleft()
            if not samples or now - self.last_seen[tid] > self.horizon:
                for table in (self.history, self.last_seen, self.kind, self.zone_since):
                    table.pop(tid, None)
        while self.removals and now - self.removals[0][0] > self.config.rack_window_seconds:
            self.removals.popleft()

    def _person_down(self, now):
        found = []
        cfg = self.config
        for tid, samples in self.history.items():
            if self.kind.get(tid) != 'person' or self.last_seen.get(tid) != now:
                continue
            window = [s for s in samples if now - s.t <= cfg.down_seconds]
            if not window or now - window[0].t < cfg.down_seconds * 0.9:
                continue
            down = sum(horizontal(s, cfg) for s in window) / len(window)
            if down < 0.8:
                continue
            centers = [_center(s.box) for s in window]
            diag = math.hypot(_width(window[-1].box), _height(window[-1].box))
            drift = max(math.hypot(x - centers[0][0], y - centers[0][1]) for x, y in centers) / diag
            if drift > cfg.down_motion:
                continue
            if not self._cooled(('person_down', tid), now):
                continue
            seconds = now - window[0].t
            score = min(0.95, 0.6 + 0.3 * down + 0.05 * min(1.0, seconds / (2 * cfg.down_seconds)))
            found.append(Candidate('person_down', [tid], window[0].t, now, round(score, 3),
                [f'{tid} lying horizontal with little movement for {seconds:.0f} s',
                 f'horizontal in {down:.0%} of samples, drift {drift:.2f} box diagonals'],
                VERIFIER_QUESTIONS['person_down']))
        return found

    def _altercation(self, tracks, now):
        cfg = self.config
        found = []
        people = [t['id'] for t in tracks if t.get('kind', 'person') == 'person']
        for i, a in enumerate(people):
            for b in people[i + 1:]:
                pair = tuple(sorted((a, b)))
                ha, hb = self.history[a], self.history[b]
                if len(ha) < 2 or len(hb) < 2:
                    continue
                close = self._close(ha[-1].box, hb[-1].box)
                ma, mb = _motion(ha[-2], ha[-1]), _motion(hb[-2], hb[-1])
                energy = ma + mb
                hits = self.fight_hits[pair]
                # Both people must be moving: one jumping box (an ID swap, a detector glitch,
                # someone running past a person standing still) is not a struggle.
                if close and energy >= cfg.fight_speed and min(ma, mb) >= cfg.fight_speed * 0.25:
                    hits.append((now, energy))
                while hits and now - hits[0][0] > cfg.fight_window_seconds:
                    hits.popleft()
                if len(hits) < cfg.fight_min_samples:
                    continue
                if not self._cooled(('altercation', pair), now):
                    continue
                mean_energy = sum(e for _, e in hits) / len(hits)
                score = min(0.95, 0.6 + 0.1 * min(3.0, mean_energy / cfg.fight_speed))
                found.append(Candidate('altercation', list(pair), hits[0][0], now, round(score, 3),
                    [f'{pair[0]} and {pair[1]} in close contact with rapid movement for {now - hits[0][0]:.1f} s',
                     f'{len(hits)} close fast-moving samples, mean pair motion {mean_energy:.2f} body heights/s'],
                    VERIFIER_QUESTIONS['altercation']))
                hits.clear()
        for pair in [p for p, h in self.fight_hits.items() if not h]:
            self.fight_hits.pop(pair, None)
        return found

    def _rack(self, tracks, now):
        cfg = self.config
        seen = {t['id'] for t in tracks}
        # Zone membership: feet for people, box centre for bicycles.
        for track in tracks:
            tid, box = track['id'], track['box']
            if self.kind[tid] == 'person':
                if self._zone_of(((box[0] + box[2]) / 2, box[3])) is not None:
                    dwell = self.dwells.get(tid)
                    # A short step out of the zone continues the same dwell.
                    if dwell is None or now - dwell[1] > 2 * cfg.rack_gone_seconds:
                        dwell = [now, now]
                    dwell[1] = now
                    self.dwells[tid] = dwell
                continue
            zone = self._zone_of(_center(box))
            if zone is None:
                self.zone_since.pop(tid, None)
                if tid in self.parked:
                    self.removals.append((now, self.parked.pop(tid)[0], tid))
            else:
                self.zone_since.setdefault(tid, now)
                if now - self.zone_since[tid] >= cfg.rack_parked_seconds:
                    self.parked.setdefault(tid, (zone, self.zone_since[tid]))
        for bike, (zone, _) in list(self.parked.items()):
            if bike not in seen and now - self.last_seen.get(bike, -1e9) >= cfg.rack_gone_seconds:
                self.removals.append((now, zone, bike))
                self.parked.pop(bike)
        # Dwells are remembered for a while after the person leaves, since leaving with the
        # bicycle is exactly when the removal happens.
        for tid in [t for t, (_, last) in self.dwells.items() if now - last > cfg.rack_window_seconds]:
            self.dwells.pop(tid)
        found = []
        if not self.removals:
            return found
        for tid, (since, last_in) in self.dwells.items():
            dwell = last_in - since
            if dwell < cfg.rack_dwell_seconds:
                continue
            removal = next((r for r in self.removals
                            if r[0] >= since and abs(r[0] - last_in) <= cfg.rack_window_seconds), None)
            if removal is None:
                continue
            samples = [s for s in self.history.get(tid, ()) if since <= s.t <= last_in]
            crouch = sum(crouched(s, cfg) for s in samples) / max(1, len(samples))
            if not self._cooled(('rack_activity', tid), now):
                continue
            score = min(0.9, 0.5 + 0.25 * min(1.0, crouch * 2) + 0.1 * min(1.0, dwell / (2 * cfg.rack_dwell_seconds)))
            found.append(Candidate('rack_activity', [tid], since, max(now, last_in), round(score, 3),
                [f'{tid} stayed at the bike rack for {dwell:.0f} s, then a parked bicycle left the rack zone',
                 f'crouched in {crouch:.0%} of samples at the rack'],
                VERIFIER_QUESTIONS['rack_activity']))
        return found


class PoseEstimator:
    """Tier 1: YOLO pose on padded crops of the flagged person boxes only."""

    def __init__(self, weights='yolo11n-pose.pt'):
        from ultralytics import YOLO
        self.model = YOLO(weights)

    def estimate(self, image, boxes):
        height, width = image.shape[:2]
        crops, origins = [], []
        for x1, y1, x2, y2 in boxes:
            pad_x, pad_y = (x2 - x1) * 0.15, (y2 - y1) * 0.15
            left, top = max(0, int(x1 - pad_x)), max(0, int(y1 - pad_y))
            right, bottom = min(width, int(x2 + pad_x)), min(height, int(y2 + pad_y))
            if right - left < 8 or bottom - top < 8:
                crops.append(None)
            else:
                crops.append(image[top:bottom, left:right])
            origins.append((left, top))
        valid = [c for c in crops if c is not None]
        results = iter(self.model(valid, verbose=False)) if valid else iter(())
        output = []
        for crop, (left, top) in zip(crops, origins):
            if crop is None:
                output.append(None)
                continue
            result = next(results)
            if result.keypoints is None or len(result.keypoints.data) == 0:
                output.append(None)
                continue
            areas = (result.boxes.xyxy[:, 2] - result.boxes.xyxy[:, 0]) * (result.boxes.xyxy[:, 3] - result.boxes.xyxy[:, 1])
            best = int(areas.argmax())
            points = result.keypoints.data[best].tolist()
            output.append([[x + left, y + top, c] for x, y, c in points])
        return output


class CascadeStats:
    """Measured per-tier cost for this camera, in stream time so replay speed does not matter."""

    def __init__(self):
        self.frames = 0
        self.tier0_ms = 0.0
        self.tier1_calls = 0
        self.tier1_ms = 0.0
        self.tier2_calls = 0
        self.tier2_ms = 0.0
        self.tier2_dismissed = 0
        self.tier2_skipped = 0
        self.candidates = defaultdict(int)
        self.alerts = defaultdict(int)
        self.first_t = None
        self.last_t = None
        self.device = 'cpu'

    def frame(self, t, tier0_ms):
        self.frames += 1
        self.tier0_ms += tier0_ms
        if self.first_t is None:
            self.first_t = t
        self.last_t = t

    def snapshot(self, sample_fps):
        hours = max(1e-9, ((self.last_t or 0) - (self.first_t or 0)) / 3600)
        per_frame = (self.tier0_ms + self.tier1_ms) / max(1, self.frames)
        vlm_ms_per_hour = self.tier2_ms / hours if self.tier2_calls else 0.0
        busy_ms_per_second = per_frame * sample_fps + vlm_ms_per_hour / 3600
        return {
            'frames': self.frames,
            'device': self.device,
            'tier0_ms_per_frame': round(self.tier0_ms / max(1, self.frames), 2),
            'tier1_pose_calls': self.tier1_calls,
            'tier1_ms_per_call': round(self.tier1_ms / max(1, self.tier1_calls), 2),
            'tier2_vlm_calls': self.tier2_calls,
            'tier2_dismissed': self.tier2_dismissed,
            'tier2_skipped_busy': self.tier2_skipped,
            'tier2_vlm_calls_per_hour': round(self.tier2_calls / hours, 2) if self.first_t is not None and hours > 1 / 3600 else None,
            'candidates': dict(self.candidates),
            'alerts': dict(self.alerts),
            # Cameras at this sample rate one device of this kind could keep up with, from measured time.
            'camera_equivalents_this_device': round(1000 / busy_ms_per_second, 1) if busy_ms_per_second > 0 else None,
            'stream_seconds': round((self.last_t or 0) - (self.first_t or 0), 1),
        }


class CascadeDetector:
    """Streaming detector with the same interface as MossDetector, driven by rules."""

    def __init__(self, rules, pose_factory=None, use_verifier=False, verifier_checkpoint='', max_queue=3):
        self.rules = rules
        self.pose_factory = pose_factory
        self.pose = None
        self.use_verifier = use_verifier
        self.verifier_checkpoint = verifier_checkpoint
        self.pending = deque()
        self.queue = deque()
        self.max_queue = max_queue
        self.error = None
        self.phase = 'not_started'
        self.response_audit = deque(maxlen=8)
        self.evidence_frames = []
        self.frame_size = None
        self.current_image = None
        self.tier0_ms = 0.0
        self.verifier = None
        self.verification_task = None
        self.stats = CascadeStats()
        self.ws = True  # the runtime reads this to tell "loading" from "observing"

    async def prepare_verifier(self):
        if self.use_verifier and self.verifier is None:
            from ml.verifier import acquire_verifier
            self.phase = 'loading'
            self.verifier = await asyncio.to_thread(acquire_verifier, self.verifier_checkpoint)

    async def observe(self, frames, timestamps, tracks):
        now = timestamps[-1]
        self.stats.frame(now, self.tier0_ms)
        tracks = [dict(t) for t in tracks]
        wanted = set(self.rules.wants_pose(tracks, now)) if self.pose_factory else set()
        if wanted and self.current_image is not None:
            started = time.perf_counter()
            if self.pose is None:
                self.pose = await asyncio.to_thread(self.pose_factory)
            flagged = [t for t in tracks if t['id'] in wanted]
            points = await asyncio.to_thread(self.pose.estimate, self.current_image, [t['box'] for t in flagged])
            for track, keypoints in zip(flagged, points):
                if keypoints:
                    track['keypoints'] = keypoints
            self.stats.tier1_calls += len(flagged)
            self.stats.tier1_ms += (time.perf_counter() - started) * 1000
        candidates = self.rules.update(tracks, now, self.frame_size)
        evidence = list(self.evidence_frames)
        people = [{'id': t['id'], 'box': t['box']} for t in tracks if t.get('kind', 'person') == 'person']
        for candidate in candidates:
            self.stats.candidates[candidate.incident_type] += 1
            if len({f.index for f in evidence}) < 2:
                continue
            if not self.use_verifier:
                self._accept(self.rule_detection(candidate, evidence), candidate)
            elif len(self.queue) < self.max_queue:
                self.queue.append((candidate, evidence, people))
            else:
                self.stats.tier2_skipped += 1
        if self.use_verifier and self.queue and (self.verification_task is None or self.verification_task.done()):
            self.verification_task = asyncio.create_task(self._verify_queue())
        self.phase = 'verifying' if self.verification_task and not self.verification_task.done() else 'observing'

    def rule_detection(self, candidate, evidence):
        state = 'WATCH' if candidate.incident_type == 'rack_activity' else 'REVIEW_REQUIRED'
        return Detection(state=state, confidence=candidate.score,
            observable_events=[{'timestamp_start': candidate.t_start, 'timestamp_end': candidate.t_end,
                                'actors': candidate.actors, 'observation': candidate.observation()}],
            uncertainties=[UNCERTAINTIES[candidate.incident_type],
                           'Detected by rules without visual verification; score is uncalibrated.'],
            supporting_frames=sorted({f.index for f in evidence}),
            incident_type=candidate.incident_type, rule_evidence=candidate.evidence)

    def _accept(self, detection, candidate):
        self.stats.alerts[candidate.incident_type] += 1
        self.pending.append(detection)

    async def _verify_queue(self):
        while self.queue:
            candidate, evidence, people = self.queue.popleft()
            started = time.perf_counter()
            try:
                await self.prepare_verifier()
                self.phase = 'verifying'
                detection = await asyncio.to_thread(self.verifier.verify_frames, evidence,
                    f'Rules flagged: {candidate.evidence[0]}', people, candidate.question)
                self.response_audit.append({**dict(self.verifier.last_audit), 'incident_type': candidate.incident_type})
                if detection.state == 'NORMAL':
                    self.stats.tier2_dismissed += 1
                    continue
                detection = detection.model_copy(update={'incident_type': candidate.incident_type,
                    'rule_evidence': candidate.evidence,
                    'uncertainties': (detection.uncertainties + [UNCERTAINTIES[candidate.incident_type]])[:20]})
                self._accept(detection, candidate)
                self.error = None
            except Exception as error:
                self.error = f'Visual verification rejected: {error}'
                audit = dict(self.verifier.last_audit) if self.verifier else {}
                self.response_audit.append({**audit, 'status': 'rejected', 'error': str(error),
                                            'incident_type': candidate.incident_type})
            finally:
                self.stats.tier2_calls += 1
                self.stats.tier2_ms += (time.perf_counter() - started) * 1000
        self.phase = 'observing'

    async def finish(self):
        if self.verification_task:
            await self.verification_task

    def drain(self):
        result = list(self.pending)
        self.pending.clear()
        return result

    async def close(self):
        await self.finish()
        self.verifier = None


class RuleGate:
    """Cascade rules already require persistence over time; this checks evidence bounds only."""

    last_reason = 'awaiting_evidence'

    def consider(self, detection, now, valid_indices):
        if detection.state == 'NORMAL' or not detection.observable_events:
            self.last_reason = 'ordinary_or_uncertain'
            return False
        supported = set(detection.supporting_frames) & set(valid_indices)
        if len(supported) < 2:
            self.last_reason = 'below_threshold_or_unsupported'
            return False
        if max(e.timestamp_end for e in detection.observable_events) > now + 0.5:
            self.last_reason = 'evidence_from_future'
            return False
        self.last_reason = 'accepted'
        return True
