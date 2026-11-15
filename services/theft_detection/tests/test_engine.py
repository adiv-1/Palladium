"""Event-engine tests on controlled traces. These use hand-written or scripted (mock)
detections - they verify the temporal logic, not real-world detection accuracy."""
import numpy as np
import pytest

from theft_detection.config import Config, EngineConfig, ZoneConfig
from theft_detection.events.engine import (
    POSSIBLE_RESTRICTED_AREA_ENTRY, POSSIBLE_SNATCHING, SEVERITY_REVIEW, SUSPICIOUS_OBJECT_REMOVAL,
    EventEngine,
)
from theft_detection.sim import scenarios
from theft_detection.sim.runner import run_trace, score_result
from theft_detection.sim.world import ObsParams

from conftest import H, bag, person

FRAME = (1280, 720)
DT = 0.1


def run(engine, frames, gm=None):
    """frames: list of track lists, one per 0.1 s. Returns every emitted RawEvent."""
    out = []
    for k, tracks in enumerate(frames):
        out += engine.update(k * DT, tracks, gm[k] if gm else (0.0, 0.0), FRAME)
    return out


def snatch_frames(run_speed=2.6, a_id=1, b_id=2, bag_id=3, x_a=400.0, dx=0.0, n=55):
    """A stands holding a bag; B runs past at t=3.0 s and the bag leaves with B."""
    frames = []
    for k in range(n):
        t = k * DT
        x_b = x_a + run_speed * H * (t - 3.0)
        bag_x = x_a + 42 if t < 3.0 else x_b + 42
        tracks = [person(a_id, x_a + dx), bag(bag_id, bag_x + dx)]
        if x_b > -60:
            tracks.append(person(b_id, x_b + dx, y=612.0))
        frames.append(tracks)
    return frames


def review(events):
    return [e for e in events if e.severity == SEVERITY_REVIEW]


def test_sudden_object_displacement_raises_possible_snatching():
    eng = EventEngine()
    events = run(eng, snatch_frames())
    assert [e.event_type for e in review(events)] == [POSSIBLE_SNATCHING]
    ev = events[0]
    assert 2.8 <= ev.onset_ts <= 3.3 and ev.detected_ts - ev.onset_ts <= 1.5
    assert ev.features["object_speed_after"] > 2.0 and ev.features["object_holder_separation"] >= 1.4
    assert {t["role"] for t in ev.tracks} == {"prior_holder", "subsequent_holder"}


def test_no_alert_from_a_single_frame_or_from_proximity_alone():
    eng = EventEngine()
    frames = [[person(1, 400), bag(3, 442)] for _ in range(30)]
    frames[15] = [person(1, 400), bag(3, 900)]                    # one wild bounding box
    frames += [[person(1, 400), bag(3, 442), person(2, 470, y=612)] for _ in range(40)]  # someone stands close
    assert run(eng, frames) == []


def test_gradual_handoff_is_dismissed_with_a_reason():
    eng = EventEngine()
    frames = []
    for k in range(80):
        t = k * DT
        u = min(max((t - 3.0) / 1.2, 0.0), 1.0)                    # bag moves 150 px in 1.2 s
        frames.append([person(1, 400), person(2, 550), bag(3, 442 + 66 * u)])
    assert run(eng, frames) == []
    assert [d.reason for d in eng.decisions] == ["gradual_transfer"]


def test_person_picking_up_own_object_is_not_an_event():
    eng = EventEngine()
    frames = []
    for k in range(200):
        t = k * DT
        if t < 2:    a, b = 400, 442                                # carrying
        elif t < 9:  a, b = 400 + min((t - 2) * 150, 380), 442      # walks away, bag stays (unattended)
        elif t < 12: a, b = 780 - min((t - 9) * 150, 380), 442      # comes back
        else:        a = 400 + (t - 12) * 150; b = a + 42           # leaves with it
        frames.append([person(1, a), bag(3, b, y=574 if 2 <= t < 12 else 516)])
    assert run(eng, frames) == []
    assert any(d.reason == "picked_up_by_a_track_that_handled_it_before" for d in eng.decisions)


def test_unattended_object_taken_by_another_track_while_depositor_in_view():
    eng = EventEngine()
    frames = []
    for k in range(170):
        t = k * DT
        a = 400 if t < 2 else max(400 - (t - 2) * 150, 60)           # A sets it down and steps away
        tracks = [person(1, a)]
        if t < 9:
            b_x, bag_x, bag_y = 1200 - max(t - 5, 0) * 190, 442, 516 if t < 2 else 574
        else:
            b_x = 440 + (t - 9) * 300
            bag_x, bag_y = b_x + 42, 516
        tracks += [person(2, b_x, y=606), bag(3, bag_x, y=bag_y)]
        frames.append(tracks)
    events = review(run(eng, frames))
    assert [e.event_type for e in events] == [SUSPICIOUS_OBJECT_REMOVAL]
    assert "person_2" in events[0].observation and events[0].features["unattended_s"] >= 3.0


def test_camera_movement_does_not_create_an_event():
    rng = np.random.default_rng(0)
    eng = EventEngine()
    frames, gm, offset = [], [], 0.0
    for k in range(60):
        shift = float(rng.normal(0, 60)) if 20 <= k < 45 else 0.0   # violent shake
        offset += shift
        gm.append((shift, 0.0))
        frames.append([person(1, 400 + offset), bag(3, 442 + offset), person(2, 560 + offset)])
    assert run(eng, frames, gm) == []


def test_camera_shake_during_a_transfer_is_dismissed_as_camera_motion():
    rng = np.random.default_rng(1)
    eng = EventEngine()
    base = snatch_frames()
    frames, gm, offset = [], [], 0.0
    for k, tracks in enumerate(base):
        shift = float(rng.normal(0, 70)) if 25 <= k < 55 else 0.0
        offset += shift
        gm.append((shift, 0.0))
        frames.append([type(t)(t.track_id, t.category, t.label,
                               (t.box[0] + offset, t.box[1], t.box[2] + offset, t.box[3]),
                               t.score, t.hits, t.first_ts, t.lost_s) for t in tracks])
    assert review(run(eng, frames, gm)) == []
    assert "camera_motion" in [d.reason for d in eng.decisions]


def test_object_hidden_by_occlusion_is_not_an_event():
    eng = EventEngine()
    frames = []
    for k in range(80):
        t = k * DT
        tracks = [person(1, 400), person(2, 100 + t * 160, y=620)]   # passer-by walks in front
        if not (1.6 < t < 2.6):                                      # bag undetected meanwhile
            tracks.append(bag(3, 442))
        frames.append(tracks)
    assert run(eng, frames) == []


def test_multiple_simultaneous_interactions_are_reported_separately():
    eng = EventEngine()
    left = snatch_frames()
    right = snatch_frames(a_id=11, b_id=12, bag_id=13, x_a=880.0, run_speed=-2.6)
    events = review(run(eng, [a + b for a, b in zip(left, right)]))
    assert sorted(e.event_type for e in events) == [POSSIBLE_SNATCHING] * 2
    assert len({e.dedup_key for e in events}) == 2
    assert {e.object_ref for e in events} == {"object_3", "object_13"}


def test_cooldown_suppresses_a_repeat_for_the_same_pair():
    eng = EventEngine(EngineConfig(cooldown_s=30.0))
    first = snatch_frames()
    # the bag comes back to A, then the same thing happens again a few seconds later
    back = [[person(1, 400), bag(3, 442)] for _ in range(25)]
    events = []
    k = 0
    for tracks in first + back + snatch_frames()[25:]:
        events += eng.update(k * DT, tracks, (0.0, 0.0), FRAME)
        k += 1
    assert len(review(events)) == 1


def test_restricted_zone_needs_configuration_dwell_and_feet_inside():
    zone = ZoneConfig(zone_id="staff_only", kind="restricted",
                      polygon=[(0.6, 0.5), (0.95, 0.5), (0.95, 1.0), (0.6, 1.0)])
    inside = [[person(1, 900)] for _ in range(20)]
    assert run(EventEngine(), inside) == []                                 # no zone configured -> nothing
    events = run(EventEngine(zones=[zone]), inside)
    assert [e.event_type for e in events] == [POSSIBLE_RESTRICTED_AREA_ENTRY]
    assert events[0].detected_ts - events[0].onset_ts >= 1.0
    brief = [[person(1, 900)] for _ in range(5)] + [[person(1, 600)] for _ in range(20)]
    assert run(EventEngine(zones=[zone]), brief) == []                      # under the dwell time
    beside = [[person(1, 740)] for _ in range(30)]                          # box overlaps, feet outside
    assert run(EventEngine(zones=[zone]), beside) == []


def test_inactive_zone_is_ignored():
    zone = ZoneConfig(zone_id="staff_only", kind="restricted", active=False,
                      polygon=[(0.6, 0.5), (0.95, 0.5), (0.95, 1.0), (0.6, 1.0)])
    assert run(EventEngine(zones=[zone]), [[person(1, 900)] for _ in range(30)]) == []


def test_zone_polygon_validation():
    with pytest.raises(ValueError):
        ZoneConfig(zone_id="z", kind="restricted", polygon=[(0, 0), (1, 1)])
    with pytest.raises(ValueError):
        ZoneConfig(zone_id="z", kind="restricted", polygon=[(0, 0), (2, 0), (1, 1)])


@pytest.mark.parametrize("builder", scenarios.NEGATIVES, ids=lambda f: f.__name__)
def test_scripted_ordinary_interactions_raise_no_review_alert(builder):
    for seed in range(4):
        sc = builder(np.random.default_rng(500 + seed))
        res = run_trace(sc, Config(), fps=10.0, params=ObsParams(), seed=seed)
        assert review(res.events) == [], (sc.name, seed, [e.observation for e in res.events])


@pytest.mark.parametrize("builder", scenarios.POSITIVES, ids=lambda f: f.__name__)
def test_scripted_staged_positives_are_mostly_detected(builder):
    hits = total = 0
    for seed in range(6):
        sc = builder(np.random.default_rng(700 + seed))
        s = score_result(run_trace(sc, Config(), fps=10.0, params=ObsParams(), seed=seed))
        assert s.fp == 0
        hits, total = hits + s.tp, total + s.tp + s.fn
    assert hits >= total - 1, f"{hits}/{total}"


def test_bag_still_detected_with_prior_holder_cancels_the_transfer():
    """Tracks say the bag left with the runner; raw detections show a bag still at A's side
    (two similar bags whose track identities swapped). No alert, and the reason is recorded."""
    from theft_detection.types import BAG, Detection

    eng = EventEngine()
    events = []
    for k, tracks in enumerate(snatch_frames()):
        t = k * DT
        dets = [Detection(tr.box, 0.6, tr.label, tr.category) for tr in tracks]
        if t >= 3.3 and k % 2 == 0:                      # A's own bag, detected intermittently
            dets.append(Detection(bag(99, 442).box, 0.4, "handbag", BAG))
        events += eng.update(t, tracks, (0.0, 0.0), FRAME, detections=dets)
    assert review(events) == []
    assert [d.reason for d in eng.decisions] == ["prior_holder_still_has_an_object"]


def test_the_same_trace_without_that_bag_is_still_reported():
    from theft_detection.types import Detection

    eng = EventEngine()
    events = []
    for k, tracks in enumerate(snatch_frames()):
        dets = [Detection(tr.box, 0.6, tr.label, tr.category)
                for tr in tracks]
        events += eng.update(k * DT, tracks, (0.0, 0.0), FRAME, detections=dets)
    assert [e.event_type for e in review(events)] == [POSSIBLE_SNATCHING]
    assert events[0].features["holder_bag_frames_after"] == 0


def test_runner_with_own_bag_identity_swap_seed_is_dismissed():
    """Regression: with 40 % of bag detections missing this seed produced a false
    possible_snatching. (Seeds where the bystander's bag is never detected during the window
    still can; see docs/KNOWN_LIMITATIONS.md.)"""
    from dataclasses import replace

    sc = scenarios.n10_runner_own_bag(np.random.default_rng(90004))
    res = run_trace(sc, Config(), fps=10.0, params=replace(ObsParams(), bag_miss=0.4), seed=90004, poses=False)   # object route only: the pinned seed predates simulated keypoints
    assert review(res.events) == []
    assert "prior_holder_still_has_an_object" in [d.reason for d in res.decisions]


def test_snatch_with_bag_hidden_during_grab_is_reported_with_its_uncertainty():
    hits = 0
    for seed in range(8):
        sc = scenarios.p06_snatch_bag_hidden(np.random.default_rng(800 + seed))
        res = run_trace(sc, Config(), fps=10.0, params=ObsParams(), seed=seed)
        alerts = review(res.events)
        assert len(alerts) <= 1
        if alerts and alerts[0].event_type == POSSIBLE_SNATCHING:
            hits += 1
            if alerts[0].features.get("unobserved_transfer") or "possession_frames_examined" in alerts[0].features:
                assert any("not visible" in u for u in alerts[0].uncertainties)
    assert hits >= 6, hits


def test_previous_behaviour_switch_disables_possession_checks():
    from theft_detection.config import load_config

    cfg = load_config(None, {"engine": {"possession_checks": False}, "tracker": {"bag_recover_requires_person": False}})
    for seed in range(4):
        res = run_trace(scenarios.n17_hidden_handoff_walk(np.random.default_rng(810 + seed)), cfg, seed=seed)
        assert all("possession_frames_examined" not in d.features for d in res.decisions)
