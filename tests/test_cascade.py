import asyncio
import json
import math

import numpy as np
import pytest

from apps.api.config import settings
from apps.api.db import Incident, Source, init, rows, save
from apps.api.schemas import Detection
from apps.api.video import Frame, digest, encode
from ml.cascade import CascadeDetector, RuleConfig, RuleGate, RulesEngine, parse_zones

FPS = 2


def times(seconds, start=0.0):
    return [start + n / FPS for n in range(int(seconds * FPS) + 1)]


def person(tid, box, keypoints=None):
    track = {'id': tid, 'kind': 'person', 'box': list(box)}
    if keypoints:
        track['keypoints'] = keypoints
    return track


def bike(tid, box):
    return {'id': tid, 'kind': 'bike', 'box': list(box)}


STANDING = (300, 100, 360, 260)   # 60 wide, 160 tall
LYING = (250, 300, 420, 350)      # 170 wide, 50 tall
CROUCHED = (300, 300, 370, 380)   # 70 wide, 80 tall


def run(engine, frames):
    found = []
    for t, tracks in frames:
        found += engine.update(tracks, t, (640, 480))
    return found


# -- person down ---------------------------------------------------------------------

def test_person_down_after_threshold_and_only_once():
    engine = RulesEngine(RuleConfig(down_seconds=10, cooldown_seconds=60))
    early = run(engine, [(t, [person('person_1', LYING)]) for t in times(8)])
    assert early == []
    later = run(engine, [(t, [person('person_1', LYING)]) for t in times(12, start=8.5)])
    assert [c.incident_type for c in later] == ['person_down']
    assert later[0].actors == ['person_1'] and later[0].t_end - later[0].t_start >= 9


def test_standing_still_or_moving_while_horizontal_is_not_down():
    engine = RulesEngine(RuleConfig(down_seconds=6))
    assert run(engine, [(t, [person('person_1', STANDING)]) for t in times(20)]) == []
    engine = RulesEngine(RuleConfig(down_seconds=6))
    moving = [(t, [person('person_2', (LYING[0] + 40 * t, LYING[1], LYING[2] + 40 * t, LYING[3]))]) for t in times(20)]
    assert run(engine, moving) == []


def keypoints_lying():
    points = [[0, 0, 0]] * 17
    points = [list(p) for p in points]
    points[5] = [200, 300, .9]; points[6] = [205, 310, .9]     # shoulders
    points[11] = [330, 305, .9]; points[12] = [335, 315, .9]   # hips, beside the shoulders
    return points


def test_pose_overrides_box_shape():
    # The box is tall (a camera looking down a body), but the torso keypoints are horizontal.
    engine = RulesEngine(RuleConfig(down_seconds=6))
    found = run(engine, [(t, [person('person_1', STANDING, keypoints_lying())]) for t in times(8)])
    assert [c.incident_type for c in found] == ['person_down']


# -- altercation -----------------------------------------------------------------------

def scuffle(t):
    jitter = 45 * math.sin(t * 7)
    a = (300 + jitter, 100, 360 + jitter, 260)
    b = (350 - jitter, 100 + jitter / 2, 410 - jitter, 260 + jitter / 2)
    return [person('person_1', a), person('person_2', b)]


def test_close_fast_pair_is_an_altercation():
    engine = RulesEngine(RuleConfig())
    found = run(engine, [(t, scuffle(t)) for t in times(4)])
    assert found and found[0].incident_type == 'altercation'
    assert sorted(found[0].actors) == ['person_1', 'person_2']


def test_walking_together_or_fast_apart_is_not():
    engine = RulesEngine(RuleConfig())
    side_by_side = [(t, [person('person_1', (100 + 30 * t, 100, 160 + 30 * t, 260)),
                         person('person_2', (170 + 30 * t, 100, 230 + 30 * t, 260))]) for t in times(6)]
    assert run(engine, side_by_side) == []
    engine = RulesEngine(RuleConfig())
    far = [(t, [person('person_1', (20 + 40 * math.sin(t * 7), 100, 80 + 40 * math.sin(t * 7), 260)),
                person('person_2', (560, 100, 620, 260))]) for t in times(6)]
    assert run(engine, far) == []


def test_one_jumping_box_next_to_a_still_person_is_not():
    # An ID swap or detector glitch makes one box jump while the neighbour stands still.
    engine = RulesEngine(RuleConfig())
    frames = [(t, [person('person_1', (300 + (120 if int(t * 2) % 2 else 0), 100, 360 + (120 if int(t * 2) % 2 else 0), 260)),
                   person('person_2', (380, 100, 440, 260))]) for t in times(6)]
    assert run(engine, frames) == []


def test_pose_is_not_requested_for_people_merely_standing_close():
    engine = RulesEngine(RuleConfig())
    still = [person('person_1', STANDING), person('person_2', (340, 100, 400, 260))]
    for t in times(3):
        assert engine.wants_pose(still, t) == []
        engine.update(still, t, (640, 480))
    moved = [person('person_1', (380, 100, 440, 260)), person('person_2', (340, 100, 400, 260))]
    assert sorted(engine.wants_pose(moved, 3.5)) == ['person_1', 'person_2']
    assert engine.wants_pose([person('person_3', LYING)], 3.5) == ['person_3']


# -- bike rack ------------------------------------------------------------------------------

RACK = [[0.4, 0.5], [0.8, 0.5], [0.8, 0.95], [0.4, 0.95]]
PARKED = (330, 280, 450, 380)


def rack_engine(**overrides):
    return RulesEngine(RuleConfig(zones=parse_zones(json.dumps([RACK])), rack_dwell_seconds=30, **overrides))


def theft_like_sequence(dwell_seconds, removal=True):
    frames = [(t, [bike('bike_9', PARKED)]) for t in times(15)]
    start = 15.5
    for t in times(dwell_seconds, start):
        frames.append((t, [bike('bike_9', PARKED), person('person_4', CROUCHED)]))
    if removal:
        end = start + dwell_seconds + 0.5
        for n, t in enumerate(times(4, end)):
            shift = 60 * (n + 1)
            frames.append((t, [bike('bike_9', (PARKED[0] - shift, 280, PARKED[2] - shift, 380)),
                               person('person_4', (CROUCHED[0] - shift, 100, CROUCHED[2] - shift, 260))]))
    return frames


def test_dwell_crouch_and_removal_flags_rack_activity():
    found = run(rack_engine(), theft_like_sequence(40))
    assert [c.incident_type for c in found] == ['rack_activity']
    assert found[0].actors == ['person_4']
    assert 'crouched' in found[0].evidence[1]


def test_rack_needs_both_dwell_and_removal():
    assert run(rack_engine(), theft_like_sequence(10)) == []           # short visit, bike leaves
    assert run(rack_engine(), theft_like_sequence(40, removal=False)) == []  # long dwell, bike stays


def test_parked_bike_disappearing_counts_as_removal():
    frames = theft_like_sequence(40, removal=False)
    last = frames[-1][0]
    frames += [(t, [person('person_4', CROUCHED)]) for t in times(5, last + 0.5)]
    assert [c.incident_type for c in run(rack_engine(), frames)] == ['rack_activity']


def test_no_zones_means_no_rack_rule():
    engine = RulesEngine(RuleConfig(rack_dwell_seconds=30))
    assert run(engine, theft_like_sequence(40)) == []


def test_parse_zones_validation():
    assert parse_zones('') == []
    assert len(parse_zones('[[[0,0],[1,0],[1,1]]]')) == 1
    for bad in ['{}', '[[[0,0],[1,0]]]', '[[[0,0],[2,0],[1,1]]]']:
        with pytest.raises(ValueError):
            parse_zones(bad)


# -- detector, verifier gating and cost ------------------------------------------------------

def frame(t, index):
    # Distinct pixels per frame: the review gate rejects windows whose cited frames are byte-identical.
    return Frame(t, index, encode(np.full((48, 64, 3), 90 + index % 50, np.uint8)))


async def drive(detector, frames):
    index = 0
    for t, tracks in frames:
        detector.evidence_frames = [frame(t - 0.5, index), frame(t, index + 1)]
        detector.frame_size = (640, 480)
        detector.tier0_ms = 4.0
        await detector.observe([frame(t, index + 1)], [t], tracks)
        index += 2
    await detector.finish()


@pytest.mark.asyncio
async def test_unverified_detection_carries_type_and_validates():
    detector = CascadeDetector(RulesEngine(RuleConfig(down_seconds=4)))
    await drive(detector, [(t, [person('person_1', LYING)]) for t in times(6)])
    [detection] = detector.drain()
    assert detection.incident_type == 'person_down' and detection.state == 'REVIEW_REQUIRED'
    assert len(detection.supporting_frames) == 2 and detection.rule_evidence
    assert 'without visual verification' in ' '.join(detection.uncertainties)
    stats = detector.stats.snapshot(FPS)
    assert stats['alerts'] == {'person_down': 1} and stats['tier2_vlm_calls'] == 0
    assert stats['tier0_ms_per_frame'] == 4.0 and stats['camera_equivalents_this_device'] == 125.0


class FakeVerifier:
    def __init__(self, state):
        self.state = state
        self.questions = []
        self.last_audit = {'status': 'accepted'}

    def verify_frames(self, frames, notes, tracks, question):
        self.questions.append(question)
        ids = [f.index for f in frames]
        events = [] if self.state == 'NORMAL' else [{'timestamp_start': frames[0].timestamp,
            'timestamp_end': frames[-1].timestamp, 'actors': ['person_1'], 'observation': 'person_1 lies still on the ground'}]
        return Detection(state=self.state, confidence=.8, observable_events=events,
                         uncertainties=[], supporting_frames=ids if events else [])


@pytest.mark.asyncio
async def test_verifier_runs_only_on_candidates_and_can_dismiss():
    for state, expected in [('NORMAL', 0), ('REVIEW_REQUIRED', 1)]:
        detector = CascadeDetector(RulesEngine(RuleConfig(down_seconds=4)), use_verifier=True)
        detector.verifier = FakeVerifier(state)
        quiet = [(t, [person('person_1', STANDING)]) for t in times(10)]
        await drive(detector, quiet)
        assert detector.verifier.questions == []          # nothing flagged, no VLM call
        await drive(detector, [(t, [person('person_1', LYING)]) for t in times(6, 10.5)])
        results = detector.drain()
        assert len(results) == expected
        assert detector.verifier.questions and 'lying on the ground' in detector.verifier.questions[0]
        stats = detector.stats.snapshot(FPS)
        assert stats['tier2_vlm_calls'] == 1 and stats['tier2_dismissed'] == 1 - expected
        if expected:
            assert results[0].incident_type == 'person_down' and results[0].rule_evidence


def test_rule_gate_needs_two_buffered_frames():
    detector = CascadeDetector(RulesEngine())
    gate = RuleGate()
    detection = Detection(state='WATCH', confidence=.6, observable_events=[{'timestamp_start': 0,
        'timestamp_end': 5, 'actors': ['person_1'], 'observation': 'person_1 at the rack'}],
        uncertainties=[], supporting_frames=[3, 4])
    assert gate.consider(detection, 5, range(10))
    assert not gate.consider(detection, 5, [4])
    assert not gate.consider(detection, 3, range(10))   # event ends in the future
    assert detector.drain() == []


# -- whole session ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cascade_session_persists_typed_incident(monkeypatch):
    from apps.api import runtime

    class ScriptedTracker:
        def __init__(self, *args):
            pass

        def update(self, image, timestamp):
            return [person('person_7', LYING)] if timestamp >= 1 else [person('person_7', STANDING)]

    init()
    for key, value in [('streaming_model', 'cascade'), ('cascade_pose', False), ('observation_verifier_model', 'none'),
                       ('person_down_seconds', 4), ('post_event_seconds', 1), ('pre_event_seconds', 4)]:
        monkeypatch.setattr(settings, key, value)
    monkeypatch.setattr(runtime, 'ByteTracker', ScriptedTracker)
    source = save(Source(name='Cascade test', kind='upload'))
    session = runtime.VideoSession(source)
    runtime.sessions[source.id] = session
    for n in range(16):
        await session.push(frame(n * 0.5, n))
        for _ in range(200):
            if session.inference_frames >= n + 1:
                break
            await asyncio.sleep(0.005)
    telemetry = session.source.telemetry
    await session.close()
    incident = next(i for i in rows(Incident) if i.source_id == source.id)
    assert incident.record['incident_type'] == 'person_down'
    assert incident.record['rule_evidence'] and not incident.simulated
    assert incident.record['prompt_version'] == 'safesc-cascade-rules-v1'
    assert digest(settings.data_dir / 'incidents' / incident.id / 'original.mp4') == incident.record['original_sha256']
    assert telemetry['cascade']['alerts'] == {'person_down': 1}
    assert telemetry['cascade']['tier2_vlm_calls'] == 0
