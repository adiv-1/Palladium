"""Cheap global (camera) motion estimate from two consecutive frames.

Phase correlation on a 160-pixel-wide greyscale thumbnail costs well under a millisecond and
gives the dominant image translation. The event engine subtracts it from every track and
refuses to decide while the camera is shaking.
"""
from __future__ import annotations

import cv2
import numpy as np


class GlobalMotionEstimator:
    def __init__(self, width: int = 160, min_response: float = 0.08) -> None:
        self.width = width
        self.min_response = min_response
        self._prev: np.ndarray | None = None
        self._window: np.ndarray | None = None

    def reset(self) -> None:
        self._prev = None

    def update(self, image: np.ndarray) -> tuple[float, float]:
        """Return (dx, dy) in image pixels: how far the scene moved since the previous call."""
        h, w = image.shape[:2]
        scale = self.width / w
        small = cv2.resize(image, (self.width, max(int(h * scale), 8)), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
        prev, self._prev = self._prev, gray
        if prev is None or prev.shape != gray.shape:
            return 0.0, 0.0
        if self._window is None or self._window.shape != gray.shape:
            self._window = cv2.createHanningWindow((gray.shape[1], gray.shape[0]), cv2.CV_32F)
        (dx, dy), response = cv2.phaseCorrelate(prev, gray, self._window)
        if response < self.min_response:
            return 0.0, 0.0
        return float(dx / scale), float(dy / scale)
