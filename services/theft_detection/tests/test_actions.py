"""Action route (body keypoints, object-agnostic). Keypoints here are scripted, not from a
pose model, except in the tests marked real_model."""
import numpy as np
import pytest

from theft_detection.config import Config
from theft_detection.events.actions import reach_towards
from theft_detection.events.engine import POSSIBLE_SNATCHING, SEVERITY_REVIEW
from theft_detection.sim import scenarios
from theft_detection.sim.runner import run_trace, score_result

from conftest import ROOT, WEIGHTS

STOCK = ROOT / "data" / "stock" / "mixkit_31359.mp4"


def review(events):
    return [e for e in events if e.severity == SEVERITY_REVIEW]


def body(x, wrist=None, H=200.0, y=600.0):
    kp = np.zeros((17, 3), np.float32)
    kp[5], kp[6] = (x - 24, y - 160, .9), (x + 24, y - 160, .9)
    kp[11], kp[12] = (x - 16, y - 100, .9), (x + 16, y - 100, .9)
    kp[9], kp[10] = (x - 30, y - 94, .9), (wrist if wrist else (x + 30, y - 94)) + (.9,)
    return kp, (x - 36, y - H, x + 36, y)


def test_reach_measure_separates_a_hanging_arm_from_a_stretched_one():
    hanging, box = body(400)
    stretched, _ = body(400, wrist=(490, 480))
    target = (560.0, 470.0)
    assert reach_towards(hanging, box, target, 200.0) < 0.2
    assert reach_towards(stretched, box, target, 200.0) > 0.35
    # overlapping torsos: direction is meaningless, a hanging arm must not count as a reach
    assert reach_towards(hanging, box, (410.0, 520.0), 200.0) == 0.0


def test_grab_of_an_object_the_detector_cannot_see_is_reported():
    hits = 0
    for seed in range(8):
        sc = scenarios.p07_grab_unseen_object_and_run(np.random.default_rng(300 + seed))
        assert sc.bags == []                                   # nothing for the object detector
        res = run_trace(sc, Config(), seed=seed)
        alerts = review(res.events)
        assert len(alerts) <= 1
        if alerts:
            ev = alerts[0]
            assert ev.event_type == POSSIBLE_SNATCHING and ev.object_label is None
            assert ev.features["action_route"] == 1.0 and ev.features["taker_speed_after"] >= 1.3
            assert any("not identified" in u for u in ev.uncertainties)
            assert {t["role"] for t in ev.tracks} == {"other_person", "departing_person"}
            hits += 1
    assert hits >= 7


def test_without_keypoints_the_same_scene_is_invisible():
    for seed in range(4):
        sc = scenarios.p07_grab_unseen_object_and_run(np.random.default_rng(300 + seed))
        assert run_trace(sc, Config(), seed=seed, poses=False).events == []


@pytest.mark.parametrize("builder", [scenarios.n19_handshake, scenarios.n21_touch_then_walks,
                                     scenarios.n01_handoff, scenarios.n07_crowd, scenarios.n13_crossing_walkers],
                         ids=lambda f: f.__name__)
def test_ordinary_hand_contact_and_passing_people_raise_nothing(builder):
    for seed in range(6):
        res = run_trace(builder(np.random.default_rng(320 + seed)), Config(), seed=seed)
        assert review(res.events) == [], (builder.__name__, seed, [e.observation for e in res.events])


def test_handshake_is_dismissed_with_a_recorded_reason():
    reasons = set()
    for seed in range(6):
        res = run_trace(scenarios.n19_handshake(np.random.default_rng(340 + seed)), Config(), seed=seed)
        reasons |= {d.reason for d in res.decisions if d.kind == "action"}
    assert reasons and reasons <= {"hand_contact_then_ordinary_parting", "hand_contact_then_stayed_together"}


def test_tag_and_run_lookalike_is_reported_because_video_cannot_tell():
    """Documented limitation, pinned so it is never mistaken for a regression or 'fixed' silently."""
    alerts = sum(bool(review(run_trace(scenarios.x01_tag_and_run(np.random.default_rng(360 + s)), Config(), seed=s).events))
                 for s in range(10))
    assert alerts >= 5


def test_object_and_action_routes_report_one_event_per_incident():
    for seed in range(10):
        s = score_result(run_trace(scenarios.p02_walkup_yank(np.random.default_rng(380 + seed)), Config(), seed=seed))
        assert s.fp == 0 and s.tp + s.fn == 1


# ---- real pose model -------------------------------------------------------------------
@pytest.mark.real_model
def test_pose_model_finds_arms_on_real_people():
    import cv2
    from theft_detection.detection.pose import RtmPoseEstimator
    from theft_detection.detection.yolox_onnx import YoloxOnnxDetector
    from theft_detection.config import DetectorConfig

    if not (WEIGHTS / "rtmpose_t.onnx").exists() or not STOCK.exists():
        pytest.skip("rtmpose_t weights or the staged clip are not downloaded")
    cap = cv2.VideoCapture(str(STOCK)); cap.set(cv2.CAP_PROP_POS_FRAMES, 108); ok, img = cap.read(); cap.release()
    img = cv2.resize(img, (960, 540))
    det = YoloxOnnxDetector(DetectorConfig(weights_dir=str(WEIGHTS)), download=False)
    boxes = [d.box for d in det.detect(img) if d.category == "person" and d.score > 0.45]
    kps = RtmPoseEstimator("rtmpose_t", WEIGHTS, download=False).estimate(img, boxes)
    assert len(kps) == 2 and all(k.shape == (17, 3) for k in kps)
    assert all((k[:5, 2] == 0).all() for k in kps)                       # facial landmarks discarded
    for k, b in zip(kps, boxes):
        assert k[[5, 6], 2].min() > 0.3 and k[[9, 10], 2].max() > 0.3     # shoulders and a wrist found
        assert b[0] - 60 <= k[5, 0] <= b[2] + 60


@pytest.mark.real_model
@pytest.mark.slow
def test_real_staged_snatch_clip_raises_one_action_alert():
    """The clip was also used while designing the action route, so this is a regression test,
    not independent evidence of accuracy."""
    from theft_detection import TheftDetector
    from theft_detection.config import load_config

    if not (WEIGHTS / "rtmpose_t.onnx").exists() or not STOCK.exists():
        pytest.skip("rtmpose_t weights or the staged clip are not downloaded")
    det = TheftDetector(load_config(None, {"source": {"uri": str(STOCK), "pace": "fast", "analysis_fps": 10},
                                           "detector": {"weights_dir": str(WEIGHTS)}}))
    det.run()
    alerts = det.store.list(severity="review")
    assert len(alerts) == 1 and alerts[0].event_type == "possible_snatching"
    f = alerts[0].evidence.features
    assert f["action_route"] == 1.0 and f["mutual_reach_frames"] >= 3 and f["pulled_apart"] >= 0.3
    assert 3.0 <= alerts[0].evidence.onset_stream_s <= 4.0 and det.simulated is False
