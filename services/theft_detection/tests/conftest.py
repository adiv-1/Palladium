"""Shared helpers. Tests that need the real ONNX detector are marked ``real_model`` and are
skipped automatically when the weights have not been downloaded."""
from pathlib import Path

import pytest

from theft_detection.types import BAG, PERSON, Track

ROOT = Path(__file__).resolve().parents[1]
WEIGHTS = ROOT / "weights"
SAMPLES = ROOT / "data" / "samples"


def pytest_collection_modifyitems(config, items):
    if (WEIGHTS / "yolox_tiny.onnx").exists():
        return
    skip = pytest.mark.skip(reason="yolox_tiny.onnx not downloaded (run `theft-detect fetch-models`)")
    for item in items:
        if "real_model" in item.keywords:
            item.add_marker(skip)


H = 200.0  # body height in pixels used by the hand-written traces


def pbox(x, y=600.0, h=H):
    w = 0.36 * h
    return (x - w / 2, y - h, x + w / 2, y)


def bbox(x, y, h=H):
    s = 0.22 * h
    return (x - s / 2, y - 0.13 * h, x + s / 2, y + 0.13 * h)


def person(tid, x, y=600.0, lost=0.0):
    return Track(tid, PERSON, "person", pbox(x, y), 0.9, 10, 0.0, lost)


def bag(tid, x, y=516.0, lost=0.0, first=0.0):
    return Track(tid, BAG, "backpack", bbox(x, y), 0.6, 10, first, lost)
