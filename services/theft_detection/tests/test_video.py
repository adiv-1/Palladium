import time

import numpy as np
import pytest

from theft_detection.config import SourceConfig
from theft_detection.sim import scenarios
from theft_detection.sim.render import write_video
from theft_detection.video.reader import FrameReader, FrameSampler
from theft_detection.video.sources import OpenCVSource, SourceError, VideoSource, is_valid_frame, open_source


class FakeSource(VideoSource):
    """Scripted source: yields ``n`` frames at ``fps``; selected frames can be invalid."""

    def __init__(self, n, fps=30.0, invalid=(), live=False, delay=0.0):
        self.n, self.fps, self.invalid, self.is_live, self.delay = n, fps, set(invalid), live, delay
        self.i = -1
        self.closed = False

    def open(self): ...

    def grab(self):
        self.i += 1
        if self.delay:
            time.sleep(self.delay)
        return (self.i, self.i / self.fps) if self.i < self.n else None

    def retrieve(self):
        return None if self.i in self.invalid else np.zeros((120, 160, 3), np.uint8)

    def close(self):
        self.closed = True


def drain(reader, per_frame_sleep=0.0):
    frames = []
    while not reader.finished:
        f = reader.get(timeout=0.2)
        if f is not None:
            frames.append(f)
            if per_frame_sleep:
                time.sleep(per_frame_sleep)
    reader.stop()
    return frames


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    sc = scenarios.build("N01_handoff_to_friend", 1)
    sc.duration = 2.0
    return write_video(sc, tmp_path_factory.mktemp("video") / "clip.mp4")


def test_valid_file_is_read_with_monotonic_timestamps_and_ends_cleanly(clip):
    cfg = SourceConfig(uri=str(clip), pace="fast", analysis_fps=10)
    reader = FrameReader(open_source(cfg), cfg).start()
    frames = drain(reader)
    ts = [f.ts for f in frames]
    assert 18 <= len(frames) <= 21                       # 2 s at 10 analysed fps
    assert ts == sorted(ts) and len(set(ts)) == len(ts)
    assert all(is_valid_frame(f.image) for f in frames)
    assert reader.stats.ended and reader.stats.error is None
    assert reader.stats.frames_read == 60 and reader.stats.frames_dropped == 0


def test_frames_are_downscaled_to_the_configured_width(clip):
    cfg = SourceConfig(uri=str(clip), pace="fast", analysis_fps=5, max_width=320)
    frames = drain(FrameReader(open_source(cfg), cfg).start())
    assert frames and all(f.image.shape[1] == 320 for f in frames)


def test_missing_file_and_unknown_scenario_raise_clear_errors(tmp_path):
    with pytest.raises(SourceError, match="not found"):
        open_source(SourceConfig(uri=str(tmp_path / "nope.mp4")))
    with pytest.raises(SourceError, match="unknown synthetic scenario"):
        open_source(SourceConfig(uri="synthetic:does_not_exist"))


def test_corrupt_file_is_rejected(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"this is not a video")
    with pytest.raises(SourceError, match="could not open"):
        open_source(SourceConfig(uri=str(bad)))


def test_empty_stream_ends_immediately_without_error():
    cfg = SourceConfig(pace="fast")
    reader = FrameReader(FakeSource(0), cfg).start()
    assert drain(reader) == []
    assert reader.stats.ended and reader.stats.frames_read == 0 and reader.source.closed


def test_invalid_frames_are_counted_and_skipped():
    cfg = SourceConfig(pace="fast", analysis_fps=30)
    reader = FrameReader(FakeSource(30, invalid={3, 4, 10}), cfg).start()
    frames = drain(reader)
    assert len(frames) == 27 and reader.stats.frames_invalid == 3


def test_sampler_hits_the_requested_average_rate():
    for src_fps, target, expected in ((30, 10, 10), (30, 5, 5), (12, 10, 10), (10, 30, 10), (60, 8, 8)):
        s = FrameSampler(target)
        taken = sum(s.accept(i / src_fps) for i in range(src_fps * 10))
        assert abs(taken / 10 - expected) <= 0.6, (src_fps, target, taken)


def test_realtime_queue_is_bounded_and_drops_the_oldest_frames():
    cfg = SourceConfig(pace="realtime", analysis_fps=30, queue_size=2)
    reader = FrameReader(FakeSource(90, live=True, delay=0.004), cfg).start()
    max_queue, frames = 0, []
    while not reader.finished:
        max_queue = max(max_queue, len(reader._queue))
        f = reader.get(timeout=0.2)
        if f is not None:
            frames.append(f)
            time.sleep(0.03)                               # analysis slower than capture
    reader.stop()
    assert max_queue <= 2
    assert reader.stats.frames_dropped > 0
    assert reader.stats.frames_sampled == len(frames) + reader.stats.frames_dropped
    assert [f.index for f in frames] == sorted(f.index for f in frames)


def test_fast_mode_never_drops_frames():
    cfg = SourceConfig(pace="fast", analysis_fps=30, queue_size=2)
    reader = FrameReader(FakeSource(40), cfg).start()
    frames = drain(reader, per_frame_sleep=0.005)
    assert len(frames) == 40 and reader.stats.frames_dropped == 0


def test_stop_releases_the_source_midstream():
    cfg = SourceConfig(pace="realtime", analysis_fps=30)
    src = FakeSource(10_000, live=True, delay=0.002)
    reader = FrameReader(src, cfg).start()
    assert reader.get(timeout=1.0) is not None
    reader.stop()
    assert src.closed and not reader._thread.is_alive()


class _FlakyCapture:
    """Stands in for cv2.VideoCapture: the stream dies after ``ok_frames`` grabs."""

    def __init__(self, ok_frames, opened=True):
        self.ok_frames, self.opened, self.released = ok_frames, opened, False

    def isOpened(self):
        return self.opened

    def grab(self):
        self.ok_frames -= 1
        return self.ok_frames >= 0

    def retrieve(self):
        return True, np.zeros((120, 160, 3), np.uint8)

    def get(self, _):
        return 30.0

    def set(self, *_):
        return True

    def release(self):
        self.released = True


def test_live_source_reconnects_after_a_dropped_connection(monkeypatch):
    caps = [_FlakyCapture(3), _FlakyCapture(0, opened=False), _FlakyCapture(5)]
    src = OpenCVSource(SourceConfig(uri="rtsp://camera.invalid/stream", reconnect_attempts=3, reconnect_backoff_s=0.0))
    monkeypatch.setattr(src, "_open_capture", lambda: caps.pop(0))
    src.open()
    grabbed = [src.grab() for _ in range(7)]
    assert all(g is not None for g in grabbed) and src.reconnects == 1
    assert [g[0] for g in grabbed] == list(range(7))
    assert [g[1] for g in grabbed] == sorted(g[1] for g in grabbed)


def test_live_source_gives_up_after_the_configured_attempts(monkeypatch):
    src = OpenCVSource(SourceConfig(uri="rtsp://camera.invalid/stream", reconnect_attempts=2, reconnect_backoff_s=0.0))
    caps = [_FlakyCapture(1)] + [_FlakyCapture(0, opened=False) for _ in range(5)]
    monkeypatch.setattr(src, "_open_capture", lambda: caps.pop(0))
    src.open()
    assert src.grab() is not None and src.grab() is None and src.reconnects == 0
    assert len(caps) == 3                                   # exactly two reconnection attempts were made


def test_file_source_does_not_reconnect_at_end_of_stream(clip):
    src = open_source(SourceConfig(uri=str(clip)))
    n = 0
    while src.grab() is not None:
        n += 1
    assert n == 60 and src.grab() is None
    src.close()


def test_a_source_that_delivers_no_frames_is_reported_as_an_error():
    from theft_detection import TheftDetector
    from theft_detection.config import load_config

    det = TheftDetector(load_config(None, {"source": {"pace": "fast"}, "detector": {"backend": "none"}}))
    state = det.run(source=FakeSource(0))
    assert state.status == "error" and "no frames" in state.detail


def test_blas_threads_are_capped_for_the_module():
    import os
    import theft_detection  # noqa: F401

    assert os.environ.get("OPENBLAS_NUM_THREADS") and os.environ.get("OMP_NUM_THREADS")


class _SpinningWebcam(_FlakyCapture):
    """Like the DirectShow driver measured on the development laptop: grab() returns at once
    without waiting for a frame. Only read() may be used for webcams."""

    def grab(self):
        raise AssertionError("grab() must not be used for webcams: it spins without new frames")

    def read(self):
        self.ok_frames -= 1
        return (self.ok_frames >= 0), np.full((120, 160, 3), 7, np.uint8)


def test_webcam_frames_are_read_not_grabbed(monkeypatch):
    src = OpenCVSource(SourceConfig(uri="0", reconnect_attempts=0))
    monkeypatch.setattr(src, "_open_capture", lambda: _SpinningWebcam(4))
    src.open()
    got = []
    while (g := src.grab()) is not None:
        got.append((g[0], src.retrieve()))
    assert [i for i, _ in got] == [0, 1, 2, 3] and all(img is not None and img[0, 0, 0] == 7 for _, img in got)


def test_webcam_failure_message_explains_how_to_check_permissions(monkeypatch):
    src = OpenCVSource(SourceConfig(uri="7"))
    monkeypatch.setattr(src, "_open_capture", lambda: _FlakyCapture(0, opened=False))
    with pytest.raises(SourceError, match="Privacy & security"):
        src.open()
