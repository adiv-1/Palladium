import itertools

import numpy as np

from theft_detection.config import TrackerConfig
from theft_detection.tracking.assignment import linear_assignment
from theft_detection.tracking.bytetrack import ByteTracker
from theft_detection.types import BAG, PERSON, Detection

from conftest import bbox, pbox


def det_p(x, score=0.9, y=600.0):
    return Detection(pbox(x, y), score, "person", PERSON)


def det_b(x, y=516.0, score=0.6, label="backpack"):
    return Detection(bbox(x, y), score, label, BAG)


def test_assignment_matches_brute_force():
    rng = np.random.default_rng(0)
    for _ in range(50):
        n, m = int(rng.integers(1, 6)), int(rng.integers(1, 6))
        cost = rng.random((n, m))
        matches, _, _ = linear_assignment(cost, max_cost=10.0)
        got = sum(cost[r, c] for r, c in matches)
        k = min(n, m)
        best = min(sum(cost[r, c] for r, c in zip(rows, cols))
                   for rows in itertools.permutations(range(n), k)
                   for cols in itertools.combinations(range(m), k))
        assert len(matches) == k and abs(got - best) < 1e-9


def test_assignment_respects_gate_and_empty_inputs():
    cost = np.array([[0.1, 0.9], [0.9, 0.95]])
    matches, un_r, un_c = linear_assignment(cost, max_cost=0.5)
    assert matches == [(0, 0)] and un_r == [1] and un_c == [1]
    assert linear_assignment(np.zeros((0, 3)), 1.0) == ([], [], [0, 1, 2])


def test_consistent_id_for_a_walking_person():
    tr, ids = ByteTracker(), set()
    for k in range(40):
        tracks = tr.update([det_p(100 + 16 * k)], k * 0.1)   # 0.8 body heights per second
        ids |= {t.track_id for t in tracks}
    assert len(ids) == 1


def test_track_survives_temporary_occlusion():
    tr, seen = ByteTracker(), []
    for k in range(40):
        dets = [] if 15 <= k < 22 else [det_p(100 + 10 * k)]     # 0.7 s without detections
        seen.append([(t.track_id, t.lost_s) for t in tr.update(dets, k * 0.1)])
    assert {tid for frame in seen for tid, _ in frame} == {1}
    assert any(lost > 0 for tid, lost in seen[18])               # reported as coasting, not as observed


def test_track_expires_after_max_lost():
    tr = ByteTracker(TrackerConfig(max_lost_s={"person": 1.0, "bag": 1.0}))
    for k in range(5):
        tr.update([det_p(300)], k * 0.1)
    assert tr.update([], 1.0) != []
    assert tr.update([], 1.6) == []
    assert [t.track_id for t in tr.update([det_p(300)], 1.7)] == []      # tentative again
    assert [t.track_id for t in tr.update([det_p(300)], 1.8)] == [2]     # new ephemeral id


def test_multiple_objects_keep_separate_ids_and_categories():
    tr = ByteTracker()
    for k in range(30):
        tracks = tr.update([det_p(200 + 5 * k), det_p(900 - 5 * k), det_b(242 + 5 * k), det_b(860 - 5 * k)], k * 0.1)
    assert sorted(t.category for t in tracks) == [BAG, BAG, PERSON, PERSON]
    assert len({t.track_id for t in tracks}) == 4


def test_single_noisy_detection_never_becomes_a_track():
    tr = ByteTracker()
    out = []
    for k in range(10):
        out += tr.update([det_b(500, score=0.4)] if k == 4 else [], k * 0.1)
    assert out == []


def test_low_score_detection_keeps_an_existing_track_but_cannot_start_one():
    tr = ByteTracker()
    for k in range(5):
        tr.update([det_p(300)], k * 0.1)
    tracks = tr.update([det_p(302, score=0.2)], 0.5)
    assert [(t.track_id, t.lost_s) for t in tracks] == [(1, 0.0)]
    tr2 = ByteTracker()
    assert all(tr2.update([det_p(300, score=0.2)], k * 0.1) == [] for k in range(6))


def test_runner_faster_than_own_box_width_is_tracked():
    """At 10 fps a 2.8 heights/s runner moves 56 px per frame with a 72 px wide box (IoU < 0.2)."""
    tr, ids = ByteTracker(), []
    for k in range(15):
        ids.append([t.track_id for t in tr.update([det_p(50 + 56 * k)], k * 0.1)])
    assert ids[1] == [1] and all(i == [1] for i in ids[1:])


def test_grabbed_bag_keeps_its_id_despite_zero_overlap():
    tr = ByteTracker()
    for k in range(10):
        tr.update([det_b(400)], k * 0.1)
    ids = [[t.track_id for t in tr.update([det_b(400 + 60 * (k + 1))], 1.0 + k * 0.1)] for k in range(8)]
    assert all(i == [1] for i in ids)


def test_spurious_far_detection_cannot_capture_a_hidden_bag_track():
    """A bag hidden for a while must not be re-linked to a lone detection far from any person."""
    tr = ByteTracker()
    for k in range(10):
        tr.update([det_p(400), det_b(442)], k * 0.1)
    for k in range(10, 15):
        tr.update([det_p(400)], k * 0.1)                          # bag hidden 0.5 s
    tracks = tr.update([det_p(400), det_b(900, y=300)], 1.5)       # phantom, far from the person
    assert not any(t.category == "bag" and t.track_id == 2 and t.lost_s == 0.0 for t in tracks)


def test_hidden_bag_reappearing_next_to_a_person_keeps_its_id():
    tr = ByteTracker()
    for k in range(10):
        tr.update([det_p(400), det_p(560), det_b(442)], k * 0.1)
    for k in range(10, 15):
        tr.update([det_p(400), det_p(560)], k * 0.1)
    tracks = tr.update([det_p(400), det_p(560), det_b(600)], 1.5)  # beside the other person
    assert any(t.category == "bag" and t.track_id == 3 and t.lost_s == 0.0 for t in tracks)
