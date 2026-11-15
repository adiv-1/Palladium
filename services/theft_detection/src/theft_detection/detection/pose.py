"""Body keypoints for tracked people: RTMPose (Apache-2.0) in ONNX Runtime.

Top-down: each person box is cropped, resized to 192x256 and passed through the network, which
returns 17 COCO keypoints. Used only for people who are close to another person (see
``pipeline``), so the cost is bounded and zero in an empty or single-person scene.

Keypoints are body joints (shoulders, elbows, wrists, hips, ...). Facial landmarks in the COCO
set (indices 0-4) are discarded; nothing here identifies anyone.
"""
from __future__ import annotations

import hashlib
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from ..types import Box

_BASE = "https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk"

# COCO keypoint indices used by the action engine
L_SHOULDER, R_SHOULDER, L_ELBOW, R_ELBOW, L_WRIST, R_WRIST, L_HIP, R_HIP = 5, 6, 7, 8, 9, 10, 11, 12


@dataclass(frozen=True)
class PoseModelInfo:
    name: str
    url: str
    sha256: str      # of the extracted end2end.onnx
    license: str


POSE_REGISTRY = {
    "rtmpose_t": PoseModelInfo(
        "rtmpose_t", f"{_BASE}/rtmpose-t_simcc-body7_pt-body7_420e-256x192-026a1439_20230504.zip",
        "a6c2f6a3896a4d51131d14d7a80a3d08b50f559af5a58a45d5b098aef510a70f", "Apache-2.0"),
    "rtmpose_s": PoseModelInfo(
        "rtmpose_s", f"{_BASE}/rtmpose-s_simcc-body7_pt-body7_420e-256x192-acd4a1ef_20230504.zip",
        "9aeb635b83f86aea45cf45d85798f7eba1a162de8e0d721c44e54fe5eebaf47d", "Apache-2.0"),
}


def ensure_pose_model(name: str, weights_dir: str | Path, download: bool = True) -> Path:
    info = POSE_REGISTRY[name]
    path = Path(weights_dir) / f"{name}.onnx"
    if not path.exists():
        if not download:
            raise FileNotFoundError(f"{path} is missing. Run `theft-detect fetch-models {name}`.")
        path.parent.mkdir(parents=True, exist_ok=True)
        archive = path.with_suffix(".zip.part")
        urllib.request.urlretrieve(info.url, archive)  # noqa: S310 - fixed https URL from the registry
        with zipfile.ZipFile(archive) as z:
            member = next(m for m in z.namelist() if m.endswith("end2end.onnx"))
            path.write_bytes(z.read(member))
        archive.unlink(missing_ok=True)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != info.sha256:
        raise ValueError(f"{path} has SHA-256 {digest}, expected {info.sha256}; refusing to load it.")
    return path


class RtmPoseEstimator:
    INPUT_W, INPUT_H = 192, 256
    _MEAN = np.array([123.675, 116.28, 103.53], dtype=np.float32)   # RGB
    _STD = np.array([58.395, 57.12, 57.375], dtype=np.float32)

    def __init__(self, model: str = "rtmpose_t", weights_dir: str | Path = "weights",
                 threads: int = 2, download: bool = True) -> None:
        import onnxruntime as ort

        path = ensure_pose_model(model, weights_dir, download)
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(path), sess_options=opts, providers=["CPUExecutionProvider"])
        self.name = f"{model}@sha256:{POSE_REGISTRY[model].sha256[:12]}"

    def estimate(self, image: np.ndarray, boxes: list[Box]) -> list[np.ndarray]:
        """One (17, 3) array of (x, y, score) in image pixels per box."""
        if not boxes:
            return []
        crops, metas = [], []
        for b in boxes:
            cx, cy = (b[0] + b[2]) * 0.5, (b[1] + b[3]) * 0.5
            w, h = (b[2] - b[0]) * 1.25, (b[3] - b[1]) * 1.25
            if w > h * self.INPUT_W / self.INPUT_H:          # pad to the network's aspect ratio
                h = w * self.INPUT_H / self.INPUT_W
            else:
                w = h * self.INPUT_W / self.INPUT_H
            x0, y0 = cx - w / 2, cy - h / 2
            m = np.array([[self.INPUT_W / w, 0, -x0 * self.INPUT_W / w],
                          [0, self.INPUT_H / h, -y0 * self.INPUT_H / h]], dtype=np.float32)
            crop = cv2.warpAffine(image, m, (self.INPUT_W, self.INPUT_H), flags=cv2.INTER_LINEAR)
            crop = (cv2.cvtColor(crop, cv2.COLOR_BGR2RGB).astype(np.float32) - self._MEAN) / self._STD
            crops.append(crop.transpose(2, 0, 1))
            metas.append((x0, y0, w, h))
        sx, sy = self.session.run(None, {"input": np.ascontiguousarray(np.stack(crops), dtype=np.float32)})
        out = []
        for i, (x0, y0, w, h) in enumerate(metas):
            xs, ys = sx[i].argmax(1) / 2.0, sy[i].argmax(1) / 2.0     # SimCC bins are half pixels
            score = np.minimum(sx[i].max(1), sy[i].max(1))
            kp = np.stack([x0 + xs / self.INPUT_W * w, y0 + ys / self.INPUT_H * h, score], axis=1)
            kp[:5, 2] = 0.0                                           # drop facial landmarks
            out.append(kp.astype(np.float32))
        return out

    def close(self) -> None:
        self.session = None  # type: ignore[assignment]
