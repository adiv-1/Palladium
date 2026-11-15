"""Real-detector robustness probe on real footage (no ground truth needed).

For every sampled frame the detector is run on the original and on degraded copies. A
degraded-frame detection "retains" an original one when it has IoU >= 0.5 with it. The result
is how much of the detector's own output survives darkness, compression, low resolution and
noise - a proxy for detector robustness, not an accuracy measurement.

    python benchmarks/bench_robustness.py data/samples/people-detection.mp4 data/samples/store-aisle-detection.mp4
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from theft_detection.config import DetectorConfig
from theft_detection.detection.yolox_onnx import YoloxOnnxDetector
from theft_detection.interactions.geometry import iou

THRESH = {"person": 0.45, "bag": 0.30}


def jpeg(img, q):
    return cv2.imdecode(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])[1], cv2.IMREAD_COLOR)


def low_res(img, f):
    h, w = img.shape[:2]
    return cv2.resize(cv2.resize(img, (int(w * f), int(h * f)), interpolation=cv2.INTER_AREA), (w, h))


CONDITIONS = {
    "brightness x0.5": lambda im, rng: (im * 0.5).astype(np.uint8),
    "brightness x0.25": lambda im, rng: (im * 0.25).astype(np.uint8),
    "brightness x0.1 + noise": lambda im, rng: np.clip(im * 0.1 + rng.normal(0, 6, im.shape), 0, 255).astype(np.uint8),
    "JPEG quality 20": lambda im, rng: jpeg(im, 20),
    "JPEG quality 8": lambda im, rng: jpeg(im, 8),
    "half resolution": lambda im, rng: low_res(im, 0.5),
    "quarter resolution": lambda im, rng: low_res(im, 0.25),
    "sensor noise sigma 25": lambda im, rng: np.clip(im + rng.normal(0, 25, im.shape), 0, 255).astype(np.uint8),
    "motion blur 15 px": lambda im, rng: cv2.blur(im, (15, 1)),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("videos", nargs="+")
    ap.add_argument("--model", default="yolox_tiny")
    ap.add_argument("--every", type=int, default=10)
    ap.add_argument("--json", default="benchmarks/results/robustness.json")
    args = ap.parse_args()
    det = YoloxOnnxDetector(DetectorConfig(model=args.model))
    rng = np.random.default_rng(0)
    base = {"person": 0, "bag": 0}
    kept = {c: {"person": 0, "bag": 0} for c in CONDITIONS}
    frames = 0
    for path in args.videos:
        cap = cv2.VideoCapture(path)
        i = -1
        while True:
            ok, img = cap.read()
            if not ok:
                break
            i += 1
            if i % args.every:
                continue
            if img.shape[1] > 960:
                img = cv2.resize(img, (960, int(img.shape[0] * 960 / img.shape[1])))
            frames += 1
            ref = [d for d in det.detect(img) if d.score >= THRESH[d.category]]
            for d in ref:
                base[d.category] += 1
            for name, fn in CONDITIONS.items():
                got = [d for d in det.detect(fn(img, rng)) if d.score >= THRESH[d.category]]
                for d in ref:
                    if any(g.category == d.category and iou(g.box, d.box) >= 0.5 for g in got):
                        kept[name][d.category] += 1
        cap.release()
    out = {"model": det.version, "videos": [Path(v).name for v in args.videos], "frames": frames,
           "reference_detections": base, "retained_fraction": {}}
    print(f"{det.version}: {frames} frames, reference detections {base}")
    print(f"{'condition':28s} {'people kept':>12s} {'bags kept':>10s}")
    for name in CONDITIONS:
        p = kept[name]["person"] / base["person"] if base["person"] else None
        b = kept[name]["bag"] / base["bag"] if base["bag"] else None
        out["retained_fraction"][name] = {"person": None if p is None else round(p, 3),
                                          "bag": None if b is None else round(b, 3)}
        print(f"{name:28s} {'n/a' if p is None else f'{p:.0%}':>12s} {'n/a' if b is None else f'{b:.0%}':>10s}")
    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json).write_text(json.dumps(out, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
