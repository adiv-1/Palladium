import cv2
import numpy as np
import pytest

from theft_detection.config import DetectorConfig
from theft_detection.detection.base import Detector, NullDetector, build_detector
from theft_detection.detection.blob import ColorBlobDetector
from theft_detection.detection.models import MODEL_REGISTRY, ensure_model
from theft_detection.detection.yolox_onnx import COCO_CLASSES, nms
from theft_detection.interactions.motion import GlobalMotionEstimator
from theft_detection.sim import scenarios
from theft_detection.sim.render import SCALE, Renderer
from theft_detection.types import BAG, PERSON

from conftest import SAMPLES, WEIGHTS


def _valid(dets, w, h):
    return all(0 <= d.box[0] < d.box[2] <= w and 0 <= d.box[1] < d.box[3] <= h and 0 < d.score <= 1 for d in dets)


def test_nms_keeps_the_best_of_overlapping_boxes():
    boxes = np.array([[0, 0, 100, 100], [5, 5, 105, 105], [300, 300, 400, 400]], float)
    assert nms(boxes, np.array([0.6, 0.9, 0.5]), 0.45) == [1, 2]
    assert nms(np.zeros((0, 4)), np.zeros(0), 0.45) == []


def test_registry_models_are_permissively_licensed_and_pinned():
    for info in MODEL_REGISTRY.values():
        assert info.license == "Apache-2.0" and len(info.sha256) == 64 and info.url.startswith("https://")


def test_missing_weights_fail_loudly_when_download_is_disabled(tmp_path):
    with pytest.raises(FileNotFoundError, match="fetch-models"):
        ensure_model("yolox_nano", tmp_path, download=False)


def test_tampered_weights_are_refused(tmp_path):
    (tmp_path / "yolox_nano.onnx").write_bytes(b"not the real model")
    with pytest.raises(ValueError, match="SHA-256"):
        ensure_model("yolox_nano", tmp_path, download=False)


def test_default_classes_exist_in_coco_and_exclude_small_items():
    classes = DetectorConfig().classes
    assert set(classes) <= set(COCO_CLASSES)
    assert set(classes.values()) == {PERSON, BAG}
    assert not {"cell phone", "laptop", "book"} & set(classes)   # not claimed as supported


def test_blob_detector_finds_the_synthetic_people_and_bag():
    sc = scenarios.build("N01_handoff_to_friend", 2)
    frame = Renderer(sc).frame(1.0)
    dets = ColorBlobDetector().detect(frame)
    assert _valid(dets, frame.shape[1], frame.shape[0])
    assert sorted(d.category for d in dets) == [BAG, PERSON, PERSON]
    truth = sc.persons[0].box(1.0)
    got = min((d for d in dets if d.category == PERSON), key=lambda d: abs(d.box[0] - truth[0] * SCALE))
    assert all(abs(g - t * SCALE) < 6 for g, t in zip(got.box, truth))


def test_blob_detector_returns_nothing_on_an_empty_scene():
    assert ColorBlobDetector().detect(np.full((270, 480, 3), 128, np.uint8)) == []


def test_null_detector_and_factory():
    assert isinstance(build_detector(DetectorConfig(backend="none")), NullDetector)
    assert isinstance(build_detector(DetectorConfig(backend="color_blob")), Detector)


def test_global_motion_estimator_measures_a_known_shift():
    rng = np.random.default_rng(0)
    base = cv2.GaussianBlur(rng.integers(0, 255, (540, 1100, 3), dtype=np.uint8), (0, 0), 6)
    est = GlobalMotionEstimator()
    assert est.update(base[:, 50:1010]) == (0.0, 0.0)             # first frame: no reference yet
    dx, dy = est.update(base[:, 26:986])                           # scene moved 24 px to the right
    assert abs(dx - 24) < 3 and abs(dy) < 3
    dx, dy = est.update(base[:, 26:986])
    assert abs(dx) < 1 and abs(dy) < 1


# ---- real model ------------------------------------------------------------------------
@pytest.fixture(scope="module")
def yolox():
    from theft_detection.detection.yolox_onnx import YoloxOnnxDetector
    return YoloxOnnxDetector(DetectorConfig(model="yolox_tiny", weights_dir=str(WEIGHTS)), download=False)


@pytest.mark.real_model
def test_real_detector_runs_on_cpu_and_reports_its_version(yolox):
    assert yolox.providers == ["CPUExecutionProvider"]
    assert yolox.version.startswith("yolox_tiny@sha256:427cc366d34e")
    assert (yolox.input_h, yolox.input_w) == (416, 416)


@pytest.mark.real_model
def test_real_detector_returns_nothing_for_blank_or_noise_images(yolox):
    assert [d for d in yolox.detect(np.full((432, 768, 3), 127, np.uint8)) if d.score >= 0.3] == []
    noise = np.random.default_rng(0).integers(0, 255, (432, 768, 3), dtype=np.uint8)
    assert [d for d in yolox.detect(noise) if d.score >= 0.5] == []


@pytest.mark.real_model
def test_real_detector_finds_people_in_real_footage_with_valid_boxes(yolox):
    path = SAMPLES / "people-detection.mp4"
    if not path.exists():
        pytest.skip("sample footage not downloaded (demo/fetch_sample_footage.py)")
    cap = cv2.VideoCapture(str(path))
    cap.set(cv2.CAP_PROP_POS_FRAMES, 200)
    ok, img = cap.read()
    cap.release()
    assert ok
    dets = yolox.detect(img)
    assert _valid(dets, img.shape[1], img.shape[0])
    assert {d.label for d in dets} <= set(DetectorConfig().classes)
    assert len([d for d in dets if d.category == PERSON and d.score >= 0.6]) == 2
    low = [d for d in dets if d.score < 0.3]
    assert all(d.score >= DetectorConfig().min_score for d in low)  # low scores are passed on, flagged by score
