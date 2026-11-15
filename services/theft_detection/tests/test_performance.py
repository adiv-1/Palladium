"""Performance guards. Bounds are deliberately loose (shared CI machines); the measured
numbers for the development laptop are in docs/PERFORMANCE.md."""
import os
import time

import numpy as np
import psutil
import pytest

from theft_detection import Config, TheftDetector
from theft_detection.config import load_config
from theft_detection.events.engine import EventEngine
from theft_detection.sim import scenarios
from theft_detection.sim.world import FRAME_H, FRAME_W, ObsParams, observe
from theft_detection.tracking.bytetrack import ByteTracker

from conftest import SAMPLES, WEIGHTS


def test_tracker_and_engine_cost_per_frame_is_small():
    sc = scenarios.build("N07_crowd_brushing_past", 1)       # the busiest scripted scene
    rng = np.random.default_rng(0)
    frames = [observe(sc, k * 0.1, rng, ObsParams(), 0.1) for k in range(int(sc.duration * 10))]
    tracker, engine = ByteTracker(), EventEngine()
    t0 = time.perf_counter()
    for k, (dets, gm) in enumerate(frames):
        engine.update(k * 0.1, tracker.update(dets, k * 0.1), gm, (FRAME_W, FRAME_H))
    per_frame_ms = (time.perf_counter() - t0) * 1000 / len(frames)
    assert per_frame_ms < 15.0, per_frame_ms


def test_long_run_does_not_accumulate_state_or_memory():
    proc = psutil.Process(os.getpid())
    tracker, engine = ByteTracker(), EventEngine()
    rng = np.random.default_rng(0)
    rss_after_warmup = None
    t = 0.0
    for rep in range(40):                                    # ~8 minutes of stream time
        sc = scenarios.build("N07_crowd_brushing_past", rep % 5)
        for k in range(int(sc.duration * 10)):
            dets, gm = observe(sc, k * 0.1, rng, ObsParams(), 0.1)
            engine.update(t, tracker.update(dets, t), gm, (FRAME_W, FRAME_H))
            t += 0.1
        if rep == 5:
            rss_after_warmup = proc.memory_info().rss
    growth_mb = (proc.memory_info().rss - rss_after_warmup) / 2 ** 20
    assert growth_mb < 30, growth_mb
    assert len(engine.hist) < 40 and len(engine.objs) < 20 and len(engine.decisions) <= 200
    assert all(len(h) <= 12.0 * 10 + 5 for h in engine.hist.values())      # history is windowed
    assert len(tracker._tracks) < 20


def test_realtime_pipeline_keeps_up_and_reports_measurements():
    cfg = load_config(None, {"source": {"uri": "synthetic:N07_crowd_brushing_past:1", "pace": "realtime",
                                        "analysis_fps": 10}, "detector": {"backend": "color_blob"}})
    det = TheftDetector(cfg)
    state = det.run(max_seconds=4.0)
    m = det.metrics.snapshot()
    assert state.status == "ended" or m["frames_analysed"] >= 30
    assert 8.0 <= m["analysed_fps"] <= 11.5                   # tracks the requested rate, not the source's 30
    assert m["capture_to_result_ms"]["p95"] < 400             # latency does not accumulate
    assert state.reader["frames_dropped"] <= 2
    assert m["inference_ms"]["p95"] is not None and m["peak_rss_mb"] > 0


@pytest.mark.real_model
@pytest.mark.slow
def test_real_model_pipeline_on_real_footage_cpu_only():
    path = SAMPLES / "people-detection.mp4"
    if not path.exists():
        pytest.skip("sample footage not downloaded (demo/fetch_sample_footage.py)")
    cfg = load_config(None, {"source": {"uri": str(path), "pace": "fast", "analysis_fps": 10},
                             "detector": {"model": "yolox_tiny", "weights_dir": str(WEIGHTS)}})
    det = TheftDetector(cfg)
    ids = set()
    state = det.run(max_seconds=12.0, on_frame=lambda r: ids.update(t.track_id for t in r.tracks))
    m = det.metrics.snapshot()
    assert state.status in ("ended", "running", "stopped") and state.detail == ""
    assert det.detector.providers == ["CPUExecutionProvider"] and det.simulated is False
    assert m["frames_analysed"] >= 60 and m["inference_ms"]["p95"] < 500
    assert 1 <= len(ids) <= 12                                # real people tracked with few ID switches
    assert det.store.list(severity="review") == []            # ordinary walking raises nothing
