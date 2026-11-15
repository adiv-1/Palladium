"""Measure the real pipeline (real detector, real video) in real-time pacing.

Each configuration runs in its own process so CPU and peak memory are attributable.

    python benchmarks/bench_pipeline.py --video data/samples/classroom.mp4 --seconds 25
"""
from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
from pathlib import Path


def worker(args) -> None:
    from theft_detection import TheftDetector  # first import: also caps BLAS threads
    from theft_detection.config import load_config

    cfg = load_config(None, {
        "source": {"uri": args.video, "pace": args.pace, "analysis_fps": args.fps, "loop": True},
        "detector": {"model": args.model, "intra_op_threads": args.threads},
    })
    det = TheftDetector(cfg)
    state = det.run(max_seconds=args.seconds)
    m = det.metrics.snapshot()
    r = state.reader
    print(json.dumps({
        "model": args.model, "target_fps": args.fps, "threads": args.threads, "pace": args.pace,
        "status": state.status, "detail": state.detail, "provider": det.detector.providers[0],
        "source_fps": round(state.source_fps, 2), "analysed_fps": m["analysed_fps"],
        "frames_analysed": m["frames_analysed"],
        "inference_ms_mean": m["inference_ms"]["mean"], "inference_ms_p95": m["inference_ms"]["p95"],
        "frame_total_ms_mean": m["frame_total_ms"]["mean"], "frame_total_ms_p95": m["frame_total_ms"]["p95"],
        "capture_to_result_ms_p95": m["capture_to_result_ms"]["p95"],
        "cpu_pct_one_core": m["cpu_percent_of_one_core"]["mean"], "cpu_pct_machine": m["cpu_percent_of_machine"],
        "peak_rss_mb": m["peak_rss_mb"],
        "frames_read": r.get("frames_read"), "frames_dropped": r.get("frames_dropped"),
        "frames_skipped_by_sampling": r.get("frames_skipped"), "events": len(det.store),
    }))


def launch(args, model, fps, threads, pace="realtime"):
    cmd = [sys.executable, __file__, "--worker", "--video", args.video, "--seconds", str(args.seconds),
           "--model", model, "--fps", str(fps), "--threads", str(threads), "--pace", pace]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def collect(proc) -> dict:
    out, err = proc.communicate()
    lines = [ln for ln in out.splitlines() if ln.startswith("{")]
    if lines:
        return json.loads(lines[-1])
    noise = ("OpenCV:", "Failed to load OpenH264", "Please check")
    tail = [ln for ln in err.splitlines() if ln.strip() and not ln.startswith(noise)][-6:]
    return {"status": "worker failed", "exit_code": proc.returncode, "stderr_tail": tail}


COLS = ["model", "target_fps", "threads", "analysed_fps", "inference_ms_mean", "inference_ms_p95",
        "capture_to_result_ms_p95", "cpu_pct_machine", "peak_rss_mb", "frames_dropped"]


def show(rows) -> None:
    print(" | ".join(f"{c:>24s}" if c == "capture_to_result_ms_p95" else f"{c:>17s}" for c in COLS))
    for r in rows:
        print(" | ".join((f"{r.get(c)!s:>24s}" if c == "capture_to_result_ms_p95" else f"{r.get(c)!s:>17s}") for c in COLS))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default="data/samples/classroom.mp4")
    ap.add_argument("--seconds", type=float, default=25.0)
    ap.add_argument("--model", default="yolox_tiny")
    ap.add_argument("--fps", type=float, default=10.0)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--pace", default="realtime")
    ap.add_argument("--worker", action="store_true")
    ap.add_argument("--sections", default="model,fps,threads,multi,max",
                    help="comma-separated subset of: model,fps,threads,multi,max")
    ap.add_argument("--json", default="benchmarks/results/pipeline.json")
    args = ap.parse_args()
    if args.worker:
        worker(args)
        return

    import psutil
    results: dict = {"video": args.video, "seconds_per_run": args.seconds, "date": time.strftime("%Y-%m-%d"),
                     "machine": {"platform": platform.platform(), "processor": platform.processor(),
                                 "physical_cores": psutil.cpu_count(logical=False),
                                 "logical_cores": psutil.cpu_count(logical=True),
                                 "ram_gb": round(psutil.virtual_memory().total / 2 ** 30, 1)}}

    sections = set(args.sections.split(","))
    out_path = Path(args.json)
    if out_path.exists():                       # update only the sections that were re-run
        previous = json.loads(out_path.read_text(encoding="utf-8"))
        results = {**previous, **{k: v for k, v in results.items() if k != "date"}, "date": results["date"]}

    if "model" in sections:
        print("== detector size (10 analysed fps, 4 threads, real-time pacing) ==")
        results["by_model"] = [collect(launch(args, m, 10, 4)) for m in ("yolox_nano", "yolox_tiny", "yolox_s")]
        show(results["by_model"])

    if "fps" in sections:
        print("\n== analysis rate (yolox_tiny, 4 threads) ==")
        results["by_fps"] = [collect(launch(args, "yolox_tiny", f, 4)) for f in (5, 8, 15)]
        show(results["by_fps"])

    if "threads" in sections:
        print("\n== detector thread cap (yolox_tiny, 10 fps) ==")
        results["by_threads"] = [collect(launch(args, "yolox_tiny", 10, t)) for t in (1, 2)]
        show(results["by_threads"])

    if "multi" in sections:
        print("\n== concurrent streams (yolox_tiny, 10 fps, 2 threads each) ==")
        results["multi_stream"] = []
        for n in (2, 4):
            procs = [launch(args, "yolox_tiny", 10, 2) for _ in range(n)]
            rows = [collect(p) for p in procs]
            results["multi_stream"].append({"streams": n, "per_stream": rows})
            print(f"-- {n} streams --")
            show(rows)

    if "max" in sections:
        print("\n== maximum throughput (fast pacing: every sampled frame, no sleeping) ==")
        results["max_throughput"] = [collect(launch(args, m, 60, 4, pace="fast"))
                                     for m in ("yolox_nano", "yolox_tiny", "yolox_s")]
        show(results["max_throughput"])

    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json).write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nwritten to {args.json}")


if __name__ == "__main__":
    main()
