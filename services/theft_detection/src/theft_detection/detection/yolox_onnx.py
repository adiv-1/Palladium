"""YOLOX (Apache-2.0) object detector running in ONNX Runtime (MIT).

Pre/post-processing follows the upstream reference (``demo/ONNXRuntime/onnx_inference.py``):
top-left letterbox padded with 114, raw 0-255 BGR input, grid/stride decoding, then NMS.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from ..config import DetectorConfig
from ..types import BAG, Detection
from .models import MODEL_REGISTRY, ensure_model, sha256_of

COCO_CLASSES = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog",
    "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
    "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle",
    "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch", "potted plant",
    "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote", "keyboard", "cell phone",
    "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase", "scissors",
    "teddy bear", "hair drier", "toothbrush",
)


def nms(boxes: np.ndarray, scores: np.ndarray, iou_thr: float) -> list[int]:
    """Plain greedy non-maximum suppression; returns kept indices, best first."""
    if len(boxes) == 0:
        return []
    x1, y1, x2, y2 = boxes.T
    areas = (x2 - x1) * (y2 - y1)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break
        rest = order[1:]
        w = np.clip(np.minimum(x2[i], x2[rest]) - np.maximum(x1[i], x1[rest]), 0, None)
        h = np.clip(np.minimum(y2[i], y2[rest]) - np.maximum(y1[i], y1[rest]), 0, None)
        inter = w * h
        order = rest[inter / (areas[i] + areas[rest] - inter + 1e-9) <= iou_thr]
    return keep


class YoloxOnnxDetector:
    def __init__(self, cfg: DetectorConfig | None = None, download: bool = True) -> None:
        import onnxruntime as ort

        self.cfg = cfg or DetectorConfig()
        if self.cfg.model in MODEL_REGISTRY:
            path = ensure_model(self.cfg.model, self.cfg.weights_dir, download=download)
            digest = MODEL_REGISTRY[self.cfg.model].sha256
            self.model_name = self.cfg.model
        else:
            path = Path(self.cfg.model)
            if not path.exists():
                raise FileNotFoundError(f"detector model not found: {path}")
            digest = sha256_of(path)
            self.model_name = path.stem
        self.version = f"{self.model_name}@sha256:{digest[:12]}"

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = self.cfg.intra_op_threads
        opts.inter_op_num_threads = 1
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        available = ort.get_available_providers()
        providers = [p for p in self.cfg.providers if p in available] or ["CPUExecutionProvider"]
        self.session = ort.InferenceSession(str(path), sess_options=opts, providers=providers)
        self.providers = self.session.get_providers()
        inp = self.session.get_inputs()[0]
        self._input_name = inp.name
        self.input_h, self.input_w = int(inp.shape[2]), int(inp.shape[3])

        # class index -> (label, category) for the classes we keep
        self._classes = {COCO_CLASSES.index(name): (name, cat)
                         for name, cat in self.cfg.classes.items() if name in COCO_CLASSES}
        self._class_idx = np.array(sorted(self._classes), dtype=int)
        self._grids, self._strides = self._make_grids()
        self._canvas = np.full((self.input_h, self.input_w, 3), 114, dtype=np.uint8)

    @property
    def name(self) -> str:
        return self.version

    def _make_grids(self) -> tuple[np.ndarray, np.ndarray]:
        grids, strides = [], []
        for s in (8, 16, 32):
            hs, ws = self.input_h // s, self.input_w // s
            xv, yv = np.meshgrid(np.arange(ws), np.arange(hs))
            grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
            strides.append(np.full((hs * ws, 1), s))
        return np.concatenate(grids, 0).astype(np.float32), np.concatenate(strides, 0).astype(np.float32)

    def warmup(self, runs: int = 2) -> None:
        dummy = np.zeros((self.input_h, self.input_w, 3), dtype=np.uint8)
        for _ in range(runs):
            self.detect(dummy)

    def detect(self, image: np.ndarray) -> list[Detection]:
        h, w = image.shape[:2]
        r = min(self.input_h / h, self.input_w / w)
        nh, nw = int(h * r), int(w * r)
        canvas = self._canvas
        canvas[:] = 114
        canvas[:nh, :nw] = cv2.resize(image, (nw, nh), interpolation=cv2.INTER_LINEAR)
        blob = np.ascontiguousarray(canvas.transpose(2, 0, 1)[None], dtype=np.float32)

        out = self.session.run(None, {self._input_name: blob})[0][0]  # (N, 85)
        scores_all = out[:, 4:5] * out[:, 5 + self._class_idx]        # objectness * class prob
        best = scores_all.argmax(1)
        scores = scores_all[np.arange(len(best)), best]
        keep = scores >= self.cfg.min_score
        if not keep.any():
            return []
        out, scores, best = out[keep], scores[keep], best[keep]
        xy = (out[:, :2] + self._grids[keep]) * self._strides[keep]
        wh = np.exp(out[:, 2:4]) * self._strides[keep]
        boxes = np.concatenate([xy - wh / 2, xy + wh / 2], 1) / r
        boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, w)
        boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, h)

        labels = [self._classes[int(self._class_idx[b])] for b in best]
        cats = np.array([c for _, c in labels])
        dets: list[Detection] = []
        # NMS per tracking category: one physical bag often fires as both "backpack" and
        # "handbag", so bag classes suppress each other.
        for cat in np.unique(cats):
            idx = np.flatnonzero(cats == cat)
            iou_thr = self.cfg.nms_iou if cat != BAG else min(self.cfg.nms_iou, 0.4)
            for k in nms(boxes[idx], scores[idx], iou_thr):
                i = int(idx[k])
                b = boxes[i]
                if b[2] - b[0] < 2 or b[3] - b[1] < 2:
                    continue
                dets.append(Detection((float(b[0]), float(b[1]), float(b[2]), float(b[3])),
                                      float(scores[i]), labels[i][0], str(cat)))
        return dets

    def close(self) -> None:
        self.session = None  # type: ignore[assignment]
