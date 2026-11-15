"""Command line entry point: ``theft-detect <command>`` (or ``python -m theft_detection.cli``)."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import __version__
from .config import Config, load_config


def _load(args) -> Config:
    overrides: dict = {}
    if getattr(args, "source", None):
        overrides.setdefault("source", {})["uri"] = args.source
    if getattr(args, "fps", None):
        overrides.setdefault("source", {})["analysis_fps"] = args.fps
    if getattr(args, "pace", None):
        overrides.setdefault("source", {})["pace"] = args.pace
    if getattr(args, "model", None):
        overrides.setdefault("detector", {})["model"] = args.model
    if getattr(args, "backend", None):
        overrides.setdefault("detector", {})["backend"] = args.backend
    uri = overrides.get("source", {}).get("uri", "")
    if uri.startswith("synthetic:") or Path(uri).name.startswith("synthetic_"):
        overrides.setdefault("detector", {}).setdefault("backend", "color_blob")
    return load_config(args.config, overrides)


def cmd_serve(args) -> int:
    import uvicorn

    from .api.app import create_app

    cfg = _load(args)
    if args.token:
        cfg = cfg.model_copy(update={"api": cfg.api.model_copy(update={"token": args.token})})
    host, port = args.host or cfg.api.host, args.port or cfg.api.port
    print(f"SafeSC theft-detection {__version__}: review UI on http://{host}:{port}/"
          + (f"?token={cfg.api.token}" if cfg.api.token else ""))
    uvicorn.run(create_app(cfg), host=host, port=port, log_level="warning")
    return 0


def cmd_live(args) -> int:
    """Webcam mode: start the review UI with the chosen camera already running."""
    import uvicorn

    from .api.app import SourceRequest, create_app

    overrides: dict = {"source": {"uri": str(args.camera), "pace": "realtime", "source_id": args.source_id},
                       "events": {"trials_path": args.trials_log}}
    if args.fps:
        overrides["source"]["analysis_fps"] = args.fps
    if args.width and args.height:
        overrides["source"].update({"capture_width": args.width, "capture_height": args.height})
    if args.model:
        overrides.setdefault("detector", {})["model"] = args.model
    if args.record_evidence:
        overrides["evidence"] = {"enabled": True}
    if args.token:
        overrides["api"] = {"token": args.token}
    cfg = load_config(args.config, overrides)
    host, port = args.host or cfg.api.host, args.port or cfg.api.port
    url = f"http://{host}:{port}/" + (f"?token={cfg.api.token}" if cfg.api.token else "")
    print(f"SafeSC theft-detection {__version__}: webcam {args.camera} -> {url}")
    print("  evidence recording: " + ("ON (short clips around events only)" if cfg.evidence.enabled else "off"))
    print("  stop with Ctrl+C; the camera is released on exit")
    if args.open:
        import threading
        import webbrowser
        threading.Timer(2.0, lambda: webbrowser.open(url)).start()
    app = create_app(cfg, autostart=SourceRequest(uri=str(args.camera), source_id=args.source_id))
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


def cmd_cameras(args) -> int:
    """List webcam indices that can be opened and deliver frames (nothing is saved)."""
    import time

    from .config import SourceConfig
    from .video.sources import SourceError, open_source

    import cv2
    cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_SILENT)   # probing absent indices is noisy
    found = 0
    for idx in range(args.max_index + 1):
        try:
            src = open_source(SourceConfig(uri=str(idx)))
        except SourceError:
            continue
        n, t0, shape = 0, time.perf_counter(), None
        while time.perf_counter() - t0 < 1.5:
            if src.grab() is None:
                break
            img = src.retrieve()
            if img is not None:
                n, shape = n + 1, img.shape
        src.close()
        dt = time.perf_counter() - t0
        found += 1
        print(f"camera {idx}: {'%dx%d' % (shape[1], shape[0]) if shape else 'no frames'}, "
              f"{n / dt:.1f} fps delivered")
    if not found:
        print("no camera could be opened. Check Settings > Privacy & security > Camera "
              "(allow desktop apps) and close other apps using the camera.", file=sys.stderr)
        return 1
    return 0


def cmd_run(args) -> int:
    """Headless: analyse one source, print every event as a JSON line, then a metrics summary."""
    from .pipeline import TheftDetector

    cfg = _load(args)
    det = TheftDetector(cfg, on_event=lambda e: print(e.model_dump_json(), flush=True))
    started = time.perf_counter()
    state = det.run(max_seconds=args.max_seconds)
    summary = {"status": state.status, "detail": state.detail, "wall_seconds": round(time.perf_counter() - started, 2),
               "events": len(det.store), **det.status()}
    det.close()
    print(json.dumps({"summary": summary}, default=str), file=sys.stderr)
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps(
            {"summary": summary, "events": [e.model_dump(mode="json") for e in det.store.list(limit=500)][::-1],
             "decisions": [vars(d) for d in det.engine.decisions]}, indent=2, default=str), encoding="utf-8")
    return 0 if state.status in ("ended", "stopped") else 1


def cmd_fetch_models(args) -> int:
    from .detection.models import MODEL_REGISTRY, ensure_model

    for name in args.names or ["yolox_tiny"]:
        if name not in MODEL_REGISTRY:
            print(f"unknown model '{name}'. Known: {', '.join(MODEL_REGISTRY)}", file=sys.stderr)
            return 2
        path = ensure_model(name, args.weights_dir)
        info = MODEL_REGISTRY[name]
        print(f"{name}: {path} (sha256 verified, licence {info.license})")
    return 0


def cmd_make_demo(args) -> int:
    from .sim import scenarios
    from .sim.render import write_video

    names = args.names or ["P01_runby_snatch", "P02_walkup_and_pull_away",
                           "P03_unattended_bag_taken_while_depositor_in_view",
                           "P04_removal_from_monitored_zone", "P05_restricted_zone_entry",
                           "N01_handoff_to_friend", "N02_picks_up_own_bag", "N07_crowd_brushing_past",
                           "N10_runs_with_own_bag", "N11_camera_shake"]
    for name in names:
        if name not in scenarios.BY_NAME:
            print(f"unknown scenario '{name}'. Known: {', '.join(scenarios.BY_NAME)}", file=sys.stderr)
            return 2
        sc = scenarios.build(name, args.seed)
        out = write_video(sc, Path(args.out) / f"synthetic_{name}.mp4")
        if sc.zones:   # zones travel with the clip as a sidecar file
            out.with_suffix(".zones.json").write_text(
                json.dumps([z.model_dump(mode="json") for z in sc.zones], indent=2), encoding="utf-8")
        print(f"{out}  ({sc.duration:.1f} s, {sc.kind}) {sc.description}")
    return 0


def cmd_schema(args) -> int:
    from .events.schema import event_json_schema

    text = json.dumps(event_json_schema(), indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


def cmd_purge(args) -> int:
    from .events.evidence import EvidenceRecorder

    cfg = _load(args)
    rec = EvidenceRecorder(cfg.evidence.model_copy(update={"enabled": False}))
    print(f"removed {rec.purge(everything=args.all)} evidence file(s) from {rec.dir}")
    return 0


def cmd_hardware(args) -> int:
    import platform

    import onnxruntime as ort
    import psutil

    print(json.dumps({
        "platform": platform.platform(), "processor": platform.processor(),
        "physical_cores": psutil.cpu_count(logical=False), "logical_cores": psutil.cpu_count(logical=True),
        "ram_gb": round(psutil.virtual_memory().total / 2 ** 30, 1),
        "onnxruntime": ort.__version__, "available_providers": ort.get_available_providers(),
    }, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="theft-detect", description="SafeSC object-removal event detection module")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--config", default=None, help="YAML config file")
        p.add_argument("--source", default=None, help="video file, webcam index, rtsp:// URL or synthetic:<scenario>")
        p.add_argument("--fps", type=float, default=None, help="analysis frames per second")
        p.add_argument("--model", default=None, help="yolox_nano | yolox_tiny | yolox_s | path to .onnx")
        p.add_argument("--backend", default=None, choices=["yolox_onnx", "color_blob"])

    p = sub.add_parser("serve", help="start the local API and review UI")
    common(p); p.add_argument("--host", default=None); p.add_argument("--port", type=int, default=None)
    p.add_argument("--token", default=None, help="require this API token (needed when SafeSC connects)")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("live", help="webcam mode: review UI with a local camera already running")
    p.add_argument("--camera", type=int, default=0, help="webcam index (see `theft-detect cameras`)")
    p.add_argument("--config", default="configs/live.yaml")
    p.add_argument("--fps", type=float, default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--width", type=int, default=None, help="requested capture width, e.g. 1280")
    p.add_argument("--height", type=int, default=None, help="requested capture height, e.g. 720")
    p.add_argument("--source-id", default="laptop_webcam")
    p.add_argument("--trials-log", default="data/live_trials.jsonl")
    p.add_argument("--record-evidence", action="store_true", help="save short clips around events (off by default)")
    p.add_argument("--token", default=None, help="require this API token (needed when SafeSC connects)")
    p.add_argument("--host", default=None); p.add_argument("--port", type=int, default=None)
    p.add_argument("--open", action="store_true", help="open the review page in a browser")
    p.set_defaults(func=cmd_live)

    p = sub.add_parser("cameras", help="list webcams that can be opened")
    p.add_argument("--max-index", type=int, default=4)
    p.set_defaults(func=cmd_cameras)

    p = sub.add_parser("run", help="analyse one source headlessly and print events as JSON lines")
    common(p)
    p.add_argument("--pace", choices=["realtime", "fast"], default=None)
    p.add_argument("--max-seconds", type=float, default=None)
    p.add_argument("--json-out", default=None)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("fetch-models", help="download and verify detector weights")
    p.add_argument("names", nargs="*"); p.add_argument("--weights-dir", default="weights")
    p.set_defaults(func=cmd_fetch_models)

    p = sub.add_parser("make-demo", help="render synthetic demo videos")
    p.add_argument("names", nargs="*"); p.add_argument("--out", default="data/demo"); p.add_argument("--seed", type=int, default=3)
    p.set_defaults(func=cmd_make_demo)

    p = sub.add_parser("schema", help="print the event JSON Schema")
    p.add_argument("--out", default=None); p.set_defaults(func=cmd_schema)

    p = sub.add_parser("purge-evidence", help="delete expired (or all) saved evidence")
    p.add_argument("--config", default=None); p.add_argument("--all", action="store_true")
    p.set_defaults(func=cmd_purge)

    p = sub.add_parser("hardware", help="show CPU, RAM and available ONNX Runtime providers")
    p.set_defaults(func=cmd_hardware)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
