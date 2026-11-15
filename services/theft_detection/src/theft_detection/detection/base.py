"""Detector adapter interface. Any backend that returns ``Detection`` lists can be plugged in."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np

from ..config import DetectorConfig
from ..types import Detection


@runtime_checkable
class Detector(Protocol):
    name: str

    def detect(self, image: np.ndarray) -> list[Detection]: ...

    def warmup(self, runs: int = 2) -> None: ...

    def close(self) -> None: ...


class NullDetector:
    """Used when detections are supplied externally (trace replay, tests)."""

    name = "none"

    def detect(self, image: np.ndarray) -> list[Detection]:
        return []

    def warmup(self, runs: int = 2) -> None:
        return None

    def close(self) -> None:
        return None


def build_detector(cfg: DetectorConfig) -> Detector:
    if cfg.backend == "yolox_onnx":
        from .yolox_onnx import YoloxOnnxDetector
        return YoloxOnnxDetector(cfg)
    if cfg.backend == "color_blob":
        from .blob import ColorBlobDetector
        return ColorBlobDetector()
    return NullDetector()
