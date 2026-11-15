"""Colour-blob detector for the synthetic scenes rendered by ``sim.render``.

It finds saturated regions of the palette the renderer uses. It works on pixels (so the whole
video path is exercised) but it is *not* a learned detector and says nothing about real
footage. Events produced with it are flagged ``simulated: true``.
"""
from __future__ import annotations

import cv2
import numpy as np

from ..sim.render import BAG_HUES, PERSON_HUES
from ..types import BAG, PERSON, Detection

_BAG_LABELS = ("backpack", "handbag")


class ColorBlobDetector:
    name = "color_blob@synthetic"
    providers = ["numpy"]

    def __init__(self, hue_tol: int = 6, min_sat: int = 120, min_val: int = 90) -> None:
        self.hue_tol, self.min_sat, self.min_val = hue_tol, min_sat, min_val
        self._kernel = np.ones((3, 3), np.uint8)

    def warmup(self, runs: int = 2) -> None:
        return None

    def close(self) -> None:
        return None

    def detect(self, image: np.ndarray) -> list[Detection]:
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        frame_area = image.shape[0] * image.shape[1]
        dets: list[Detection] = []
        for hues, category in ((PERSON_HUES, PERSON), (BAG_HUES, BAG)):
            min_area = frame_area * (0.0008 if category == PERSON else 0.00015)
            for i, hue in enumerate(hues):
                mask = cv2.inRange(hsv, (hue - self.hue_tol, self.min_sat, self.min_val),
                                   (hue + self.hue_tol, 255, 255))
                mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._kernel)
                n, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
                for k in range(1, n):
                    x, y, w, h, area = stats[k]
                    if area < min_area:
                        continue
                    label = "person" if category == PERSON else _BAG_LABELS[i % len(_BAG_LABELS)]
                    fill = area / float(w * h)
                    score = 0.9 if category == PERSON else float(min(0.9, 0.5 + 0.4 * fill))
                    dets.append(Detection((float(x), float(y), float(x + w), float(y + h)),
                                          score, label, category))
        return dets
