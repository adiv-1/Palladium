"""Pinned detector weights: where they come from, their licence, and their SHA-256.

Weights are downloaded on demand into an ignored directory and verified before use. Nothing is
bundled in the repository.
"""
from __future__ import annotations

import hashlib
import urllib.request
from dataclasses import dataclass
from pathlib import Path

_RELEASE = "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0"


@dataclass(frozen=True)
class ModelInfo:
    name: str
    url: str
    sha256: str
    input_size: int
    license: str
    coco_map: float  # COCO val mAP 0.5:0.95 as published upstream (all 80 classes)


MODEL_REGISTRY: dict[str, ModelInfo] = {
    "yolox_nano": ModelInfo("yolox_nano", f"{_RELEASE}/yolox_nano.onnx",
                            "c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d",
                            416, "Apache-2.0", 25.8),
    "yolox_tiny": ModelInfo("yolox_tiny", f"{_RELEASE}/yolox_tiny.onnx",
                            "427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7",
                            416, "Apache-2.0", 32.8),
    "yolox_s": ModelInfo("yolox_s", f"{_RELEASE}/yolox_s.onnx",
                         "c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063",
                         640, "Apache-2.0", 40.5),
}


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_model(name: str, weights_dir: str | Path, download: bool = True) -> Path:
    """Return the verified local path of a registry model, downloading it if allowed."""
    info = MODEL_REGISTRY[name]
    path = Path(weights_dir) / f"{name}.onnx"
    if not path.exists():
        if not download:
            raise FileNotFoundError(
                f"{path} is missing. Run `theft-detect fetch-models {name}` (downloads from {info.url}).")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        urllib.request.urlretrieve(info.url, tmp)  # noqa: S310 - fixed https URL from the registry
        tmp.replace(path)
    digest = sha256_of(path)
    if digest != info.sha256:
        raise ValueError(f"{path} has SHA-256 {digest}, expected {info.sha256}; refusing to load it.")
    return path
