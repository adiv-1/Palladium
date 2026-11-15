# Performance (measured)

Every number here comes from `benchmarks/bench_pipeline.py` on the machine below and is stored
in [`benchmarks/results/pipeline.json`](../benchmarks/results/pipeline.json). Nothing is
estimated. Re-run on your own hardware before relying on it.

## Setup

| | |
|---|---|
| CPU | AMD Ryzen 5 5600H, 6 cores / 12 threads |
| RAM | 31.3 GB (but see *memory headroom* below) |
| GPU | GTX 1650 present, **not used**: the installed ONNX Runtime build exposes only the CPU provider |
| OS / runtime | Windows 11, Python 3.11, ONNX Runtime 1.32.0, OpenCV 4.14 |
| Input | `classroom.mp4`, 1920×1080 at 30 FPS, downscaled to 960 px, looped |
| Pacing | Real time: frames delivered at source speed, stale frames dropped as in a live camera |
| Per run | 25 s (single-stream rows), 20 s (multi-stream rows), each configuration in its own process |
| Date | 2026-11-14 |

Definitions:

* **detector** — ONNX inference including pre/post-processing, per analysed frame.
* **frame total** — detector + camera-motion estimate + tracking + event engine.
* **capture → result** — wall time from the frame leaving the decoder to its analysis finishing,
  i.e. queueing + processing. This is the processing part of alert latency; the
  verification window (0.5–2.0 s of stream time) comes on top and is reported in
  [EVALUATION.md](EVALUATION.md) as time to detection.
* **CPU, machine** — process CPU time as a share of all 12 logical cores.

## Live webcam

`theft-detect live --camera 0`, built-in HP Wide Vision HD camera (DirectShow, 640×480 at 30 fps),
YOLOX-Tiny, 4 detector threads, 10 analysed FPS, one person in view, 25 s, measured 2026-11-14:

| | Value |
|---|---|
| Camera frames delivered | 29.2 / s |
| Analysed FPS | 10.03 (target 10) |
| Detector mean / p95 / max | 23.8 / 34.7 / 69.6 ms |
| Capture → result mean / p95 | 25.0 / 36.3 ms |
| CPU | 18.2 % of the machine (2.2 cores) |
| Memory | 160 MB resident, peak 161 MB |
| Stale frames dropped / queue depth | 0 / 0 |

The 640×480 webcam frames are cheaper than the 1080p clip used below (less decoding and resizing).

**Capture bug found and fixed here.** With this driver, `grab()` without a following `retrieve()`
returns immediately without waiting for a new frame (measured: ~1,000,000 calls/s vs 30.5 frames/s
with `read()`). The first live run therefore spun the capture thread: 12 million "frames read" in
20 s and 27.7 % CPU. Webcams are now read frame by frame; the same run uses 18.2 %.

**With SafeSC attached** (worker `live` + SafeSC `uvicorn` as separate processes): the worker held
9.99 FPS at 40.7 ms p95 capture → result; SafeSC's `/api/theft/frame` live-monitor proxy answered
in 28 ms median (34 ms max); in the end-to-end run the SafeSC incident existed 6.6 s after the clip
was started, for an event the worker raised at 5.6 s of stream time — about 1 s for the poll
interval (1 s) and transfer (an estimate: the two clocks were not synchronised). SafeSC's own API
kept answering in 4 ms.

The candidate-only possession checks add no measurable cost: they reuse detections the detector
already produced (no second model, no extra inference).

## Detector size (10 analysed FPS, detector capped at 4 threads)

| Model | Analysed FPS | Detector mean / p95 | Frame total mean | Capture → result p95 | CPU (machine) | Peak RSS | Stale frames dropped |
|---|---|---|---|---|---|---|---|
| YOLOX-Nano | 10.0 | 12.4 / 14.6 ms | 14.3 ms | 18 ms | 20 % | 231 MB | 0 |
| **YOLOX-Tiny** (default) | 10.0 | 38.2 / 43.2 ms | 40.3 ms | 46 ms | 28 % | 253 MB | 0 |
| YOLOX-S | **6.3** | 154.4 / 168.5 ms | 156.7 ms | 364 ms | 35 % | 314 MB | **89** |

YOLOX-S cannot hold 10 FPS on this CPU. The reader drops stale frames rather than queueing
them, so its latency stays bounded (p95 364 ms) instead of growing without limit.

Tracking, camera-motion estimation and the event engine together add about 2 ms per frame
(frame total minus detector). Measured separately with the final code on scripted scenes
(up to 7 people): the tracker takes 0.25–0.39 ms mean (p95 ≤ 0.56 ms) and the event engine
0.02–0.04 ms mean (p95 ≤ 0.06 ms); the rest is the camera-motion estimate on the full frame.
The single-stream rows above were measured before the final verification change (raw-detection
check), which this shows to be negligible in cost.

## Analysis rate (YOLOX-Tiny, 4 threads)

| Target FPS | Achieved | Detector p95 | Capture → result p95 | CPU (machine) | Source frames never decoded |
|---|---|---|---|---|---|
| 5 | 4.99 | 44.0 ms | 49 ms | 15 % | 626 of 753 |
| 8 | 8.00 | 44.5 ms | 49 ms | 24 % | 551 of 753 |
| 10 | 9.99 | 43.2 ms | 46 ms | 28 % | 501 of 753 |
| 15 | 15.0 | 44.4 ms | 48 ms | 33 % | 375 of 753 |

CPU scales with the analysis rate because skipped frames are grabbed but never decoded. What
the rate costs in *missed events* is measured separately ([EVALUATION.md](EVALUATION.md#analysis-rate)).

## Detector thread cap (YOLOX-Tiny, 10 FPS)

| Threads | Detector mean / p95 | Capture → result p95 | CPU (machine) |
|---|---|---|---|
| 1 | 86.4 / 102.4 ms | 108 ms | 10 % |
| 2 | 53.1 / 64.5 ms | 66 ms | 15 % |
| 4 | 38.2 / 43.2 ms | 46 ms | 28 % |

With a single thread the module still meets 10 FPS on one core: use
`detector.intra_op_threads: 1` when it must coexist with other work.

## Concurrent streams (YOLOX-Tiny, 10 FPS, 2 threads per stream, one process per stream)

| Streams | Analysed FPS per stream | Detector mean / p95 | Capture → result p95 | CPU (machine, all streams) | Dropped per stream (≈200 analysed) | Peak RSS per process |
|---|---|---|---|---|---|---|
| 1 | 10.0 | 53.1 / 64.5 ms | 66 ms | 15 % | 0 | 253 MB |
| 2 | 10.0, 10.0 | 67 / 81 ms | 83 ms | 31 % | 0 | 253 MB |
| 4 | 10.0–10.2 | 97 / 113–123 ms | 231–286 ms | 74 % | 2–5 | 256 MB |

Four streams fit on this laptop with bounded latency; contention shows up as slower inference
and a few dropped stale frames, not as backlog. An earlier two-stream run measured 143–146 ms
p95 capture → result (other activity on the machine) — the variance is real.

## Maximum throughput (no pacing; every sampled frame analysed)

| Model | Analysed FPS | Detector mean |
|---|---|---|
| YOLOX-Nano | 50.0 (decoding 1080p frames becomes a large share of the cost) | 16.3 ms |
| YOLOX-Tiny | 21.6 | 42.5 ms |
| YOLOX-S | 6.1 | 156.9 ms |

## Memory headroom and a fix it led to

The development machine's system drive was full (59 MB free), so the page file could not grow
and only ~1.6 GB of commit was available despite 9 GB of free RAM. In the first 4-stream run
three of four processes opened the video but analysed nothing. Starting six processes at once
reproduced the cause: NumPy's OpenBLAS reserves a work buffer for each of 12 threads, and the
allocation was refused ("Memory allocation still failed after 10 retries").

Two changes followed, both tested:

* The package caps BLAS/OpenMP threads at 1 unless the operator sets them (the module only
  multiplies tiny matrices). Measured committed memory per process: **494 MB → 140 MB**; threads
  18 → 7; resident memory unchanged (120 MB); detector speed unchanged.
* A source that opens but delivers no frames now reports `status: error`. Before, the failed
  workers reported `ended` — indistinguishable from an empty clip.

After the fix, one 4-stream run still had two workers exit without producing results (their
error output was not captured in that run; the benchmark now records it); the next run,
reported above, succeeded for all four. On a machine with normal memory headroom this should not occur, but it
was not verified elsewhere.

The single-stream rows above were measured before the BLAS cap; the cap does not change
inference time, and resident memory was the same in the comparison above.

## Not measured

* GPU utilisation — no GPU execution provider was available.
* Behaviour over hours — the longest continuous checks were ~2.5 min of real footage and
  ~8 min of stream time in the memory-growth test (`test_long_run_does_not_accumulate_state_or_memory`).
* Live RTSP cameras — reconnection is tested with a simulated failing capture, not a real camera.
