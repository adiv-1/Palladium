"""Video sources behind one interface: files, webcams, RTSP/HTTP streams and synthetic scenes.

``grab()`` advances the stream and returns the frame's (index, timestamp) *without decoding*;
``retrieve()`` decodes the frame that was just grabbed. Splitting the two lets the sampler skip
frames it will not analyse without paying for a decode.
"""
from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod

import cv2
import numpy as np

from ..config import SourceConfig


class SourceError(RuntimeError):
    """The source could not be opened (bad path, missing device, unreachable stream)."""


class VideoSource(ABC):
    is_live: bool = False
    fps: float = 30.0
    session_key: str = ""
    simulated: bool = False

    @abstractmethod
    def open(self) -> None: ...

    @abstractmethod
    def grab(self) -> tuple[int, float] | None:
        """Advance one frame. ``None`` means the stream ended (or could not be recovered)."""

    @abstractmethod
    def retrieve(self) -> np.ndarray | None:
        """Decode the grabbed frame; ``None`` if it is missing or invalid."""

    @abstractmethod
    def close(self) -> None: ...

    def __enter__(self) -> "VideoSource":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def is_valid_frame(img) -> bool:
    return (isinstance(img, np.ndarray) and img.ndim == 3 and img.shape[2] == 3
            and img.shape[0] >= 16 and img.shape[1] >= 16 and img.dtype == np.uint8)


class OpenCVSource(VideoSource):
    """Local file, webcam index, or network stream via OpenCV/FFmpeg."""

    def __init__(self, cfg: SourceConfig) -> None:
        self.cfg = cfg
        uri = cfg.uri.strip()
        self._uri: str | int = uri
        self.kind = "file"
        if uri.isdigit():
            self._uri, self.kind = int(uri), "webcam"
        elif "://" in uri:
            self.kind = "stream"
        self.is_live = self.kind != "file"
        self._cap: cv2.VideoCapture | None = None
        self._index = -1
        self._t_open = 0.0
        self._ts_offset = 0.0      # keeps time monotonic across file loops
        self._last_ts = 0.0
        self._pending: np.ndarray | None = None
        self.reconnects = 0

    def _open_capture(self) -> cv2.VideoCapture:
        if self.kind == "webcam":
            cap = cv2.VideoCapture(self._uri, cv2.CAP_DSHOW) if os.name == "nt" else cv2.VideoCapture(self._uri)
            if not cap.isOpened():
                cap.release()
                cap = cv2.VideoCapture(self._uri)
        else:
            cap = cv2.VideoCapture(self._uri)
        if self.is_live:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)   # keep the driver's own queue short
        if self.kind == "webcam" and self.cfg.capture_width and self.cfg.capture_height:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.cfg.capture_width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.cfg.capture_height)
        return cap

    def open(self) -> None:
        if self.kind == "file":
            path = str(self._uri)
            if not os.path.isfile(path):
                raise SourceError(f"video file not found: {path}")
            st = os.stat(path)
            self.session_key = f"file:{os.path.basename(path)}:{st.st_size}"
        else:
            self.session_key = f"live:{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
        cap = self._open_capture()
        if not cap.isOpened():
            cap.release()
            if self.kind == "webcam":
                raise SourceError(
                    f"could not open webcam {self.cfg.uri}. Check that the camera exists (`theft-detect cameras`), "
                    "that no other app is using it, and that camera access is allowed for desktop apps "
                    "(Windows: Settings > Privacy & security > Camera).")
            raise SourceError(f"could not open video source: {self.cfg.uri}")
        fps = cap.get(cv2.CAP_PROP_FPS)
        self.fps = float(fps) if fps and 1.0 <= fps <= 240.0 else 30.0
        self._cap = cap
        self._t_open = time.monotonic()

    def grab(self) -> tuple[int, float] | None:
        if self._cap is None:
            return None
        if not self._advance():
            if self.kind == "file":
                if not self.cfg.loop or self._index < 0:
                    return None
                self._cap.release()
                self._cap = self._open_capture()
                self._ts_offset = self._last_ts + 1.0 / self.fps
                self._index = -1
                if not self._cap.isOpened() or not self._cap.grab():
                    return None
            elif not self._reconnect():
                return None
        self._index += 1
        if self.kind == "file":
            ts = self._ts_offset + self._index / self.fps
        else:
            ts = time.monotonic() - self._t_open
        self._last_ts = ts
        return self._index, ts

    def _reconnect(self) -> bool:
        """Live sources only: reopen with linear back-off, give up after the configured attempts."""
        for attempt in range(1, self.cfg.reconnect_attempts + 1):
            if self._cap is not None:
                self._cap.release()
            time.sleep(self.cfg.reconnect_backoff_s * attempt)
            self._cap = self._open_capture()
            if self._cap.isOpened() and self._advance():
                self.reconnects += 1
                return True
        return False

    def _advance(self) -> bool:
        """Move to the next frame. Webcams are read (decoded) every time: with some drivers
        (DirectShow) grab() without a following retrieve() returns immediately without waiting
        for a new frame - measured at ~1,000,000 calls/s - which turns the capture thread into a
        busy loop and makes frame counts meaningless."""
        if self.kind == "webcam":
            ok, img = self._cap.read()
            self._pending = img if ok else None
            return ok
        self._pending = None
        return self._cap.grab()

    def retrieve(self) -> np.ndarray | None:
        if self._cap is None:
            return None
        if self.kind == "webcam":
            img = self._pending
            return img if is_valid_frame(img) else None
        ok, img = self._cap.retrieve()
        return img if ok and is_valid_frame(img) else None

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None


class SyntheticSource(VideoSource):
    """Renders a scripted scenario on the fly: ``synthetic:<scenario name>[:seed]``."""

    simulated = True

    def __init__(self, cfg: SourceConfig) -> None:
        from ..sim import scenarios
        from ..sim.render import Renderer

        parts = cfg.uri.split(":")
        name = parts[1] if len(parts) > 1 else ""
        seed = int(parts[2]) if len(parts) > 2 else 0
        if name not in scenarios.BY_NAME:
            raise SourceError(f"unknown synthetic scenario '{name}'. Available: {', '.join(scenarios.BY_NAME)}")
        self.scenario = scenarios.build(name, seed)
        self._renderer = Renderer(self.scenario)
        self.fps = 30.0
        self.session_key = f"synthetic:{name}:{seed}"
        self._index = -1
        self._loop = cfg.loop
        self._n = int(self.scenario.duration * self.fps)

    def open(self) -> None:
        self._index = -1

    def grab(self) -> tuple[int, float] | None:
        self._index += 1
        if self._index >= self._n and not self._loop:
            return None
        return self._index, self._index / self.fps

    def retrieve(self) -> np.ndarray | None:
        return self._renderer.frame((self._index % self._n) / self.fps)

    def close(self) -> None:
        return None


def open_source(cfg: SourceConfig) -> VideoSource:
    src: VideoSource = SyntheticSource(cfg) if cfg.uri.startswith("synthetic:") else OpenCVSource(cfg)
    src.open()
    return src
