"""Render a scripted scenario as video frames (flat-colour figures on a textured background).

The output is intentionally cartoon-like and watermarked "SYNTHETIC": it exists to exercise
the full video -> detection -> tracking -> events path without filming anyone, not to look
real. The matching detector is ``detection.blob.ColorBlobDetector``.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .world import FRAME_H, FRAME_W, Scenario

SCALE = 0.75                       # rendered frames are 960 x 540
PERSON_HUES = (110, 65, 150, 90)   # OpenCV hue (0-179): blue, green, magenta, cyan
BAG_HUES = (15, 30)                # orange, yellow
_MARGIN = 64


def _bgr(hue: int, sat: int = 215, val: int = 225) -> tuple[int, int, int]:
    px = cv2.cvtColor(np.uint8([[[hue, sat, val]]]), cv2.COLOR_HSV2BGR)[0, 0]
    return int(px[0]), int(px[1]), int(px[2])


def _background(w: int, h: int) -> np.ndarray:
    """Low-saturation textured scene: wall, floor tiles, a few fixtures, fixed-seed grain."""
    rng = np.random.default_rng(7)
    img = np.zeros((h, w, 3), np.uint8)
    horizon = int(h * 0.42)
    img[:horizon] = (168, 164, 160)
    for y in range(horizon, h):
        img[y] = 120 + int(50 * (y - horizon) / max(h - horizon, 1))
    for x in range(0, w, 96):
        cv2.line(img, (x, horizon), (int(x + (x - w / 2) * 0.9), h), (104, 104, 104), 2)
    for k in range(1, 6):
        y = horizon + int((h - horizon) * (k / 6) ** 1.6)
        cv2.line(img, (0, y), (w, y), (108, 108, 108), 2)
    for x in range(40, w, 230):
        cv2.rectangle(img, (x, int(h * 0.10)), (x + 120, int(h * 0.30)), (188, 184, 178), -1)
        cv2.rectangle(img, (x, int(h * 0.10)), (x + 120, int(h * 0.30)), (120, 118, 116), 2)
    noise = rng.normal(0, 5, (h, w, 1))
    return np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8)


class Renderer:
    def __init__(self, scenario: Scenario) -> None:
        self.sc = scenario
        self.w, self.h = int(FRAME_W * SCALE), int(FRAME_H * SCALE)
        self._bg = _background(self.w + 2 * _MARGIN, self.h + 2 * _MARGIN)

    def frame(self, t: float) -> np.ndarray:
        sc = self.sc
        ox, oy = sc.camera_offset(t)
        ox, oy = int(round(ox * SCALE)), int(round(oy * SCALE))
        ox, oy = max(-_MARGIN, min(_MARGIN, ox)), max(-_MARGIN, min(_MARGIN, oy))
        # Scene content is drawn in canvas coordinates; shaking the camera moves the crop window.
        canvas = self._bg.copy()

        def P(x: float, y: float) -> tuple[int, int]:
            return int(round(x * SCALE)) + _MARGIN, int(round(y * SCALE)) + _MARGIN

        items: list[tuple[float, str, object]] = []
        depth = {p.pid: p.feet(t)[1] for p in sc.persons}
        for p in sc.persons:
            items.append((depth[p.pid], "person", p))
        for b in sc.bags:
            carriers = sc.bag_carriers(b, t)
            d = max((depth[c] for c in carriers), default=sc.bag_box(b, t)[3]) + 0.5
            items.append((d, "bag", b))
        for _, kind, obj in sorted(items, key=lambda it: it[0]):
            if kind == "person":
                x1, y1, x2, y2 = obj.box(t)
                color = _bgr(PERSON_HUES[obj.color % len(PERSON_HUES)])
                H, W = y2 - y1, x2 - x1
                cx = (x1 + x2) / 2
                cv2.rectangle(canvas, P(x1 + 0.08 * W, y1 + 0.17 * H), P(x2 - 0.08 * W, y1 + 0.60 * H), color, -1)
                cv2.rectangle(canvas, P(x1 + 0.14 * W, y1 + 0.55 * H), P(cx - 0.04 * W, y2), color, -1)
                cv2.rectangle(canvas, P(cx + 0.04 * W, y1 + 0.55 * H), P(x2 - 0.14 * W, y2), color, -1)
                cv2.rectangle(canvas, P(x1, y1 + 0.20 * H), P(x2, y1 + 0.30 * H), color, -1)   # shoulders
                cv2.circle(canvas, P(cx, y1 + 0.10 * H), int(0.10 * H * SCALE), color, -1)
            else:
                if obj.is_hidden(t):
                    continue
                x1, y1, x2, y2 = sc.bag_box(obj, t)
                color = _bgr(BAG_HUES[obj.color % len(BAG_HUES)])
                cv2.rectangle(canvas, P(x1, y1), P(x2, y2), color, -1)
        x0, y0 = _MARGIN - ox, _MARGIN - oy
        out = canvas[y0:y0 + self.h, x0:x0 + self.w].copy()
        cv2.putText(out, "SYNTHETIC SCENE - not real footage", (10, self.h - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (70, 70, 70), 1, cv2.LINE_AA)
        return out


def write_video(scenario: Scenario, path: str | Path, fps: float = 30.0) -> Path:
    """Encode the scenario to a video file (mp4v) for replay through the normal file source."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    r = Renderer(scenario)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (r.w, r.h))
    if not writer.isOpened():
        raise RuntimeError(f"could not open a video writer for {path}")
    for k in range(int(scenario.duration * fps)):
        writer.write(r.frame(k / fps))
    writer.release()
    return path
