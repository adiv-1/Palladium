"""Event-level evaluation of the REAL pipeline (real detector, real decoding) on labelled videos.

Annotation format extends SafeSC's ``evaluation/example.json`` (fields there are kept as-is):

    {"videos": [
      {"video": "data/stock/clip.mp4", "duration": 7.6, "lighting": "well_lit",
       "zones": [...optional ZoneConfig dicts...],
       "events": [{"start": 3.8, "end": 5.5, "type": "possible_snatching",
                   "object": "handbag", "supported": true}]}]}

``supported: false`` marks an event involving an object the detector is not configured for
(e.g. a phone). Those are reported separately instead of silently counted as misses.

A labelled event is detected when a review-severity alert of the same type is raised between
``start - 0.75`` and ``end + 3.0`` seconds. Every other review alert is a false alert. The script
also writes predictions in SafeSC's own format so ``scripts/evaluate.py`` can score them.

    python benchmarks/eval_videos.py evaluation/annotations.json --model yolox_tiny
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from theft_detection import TheftDetector  # first import: also caps BLAS threads
from theft_detection.config import ZoneConfig, load_config

EARLY, LATE = 0.75, 3.0


def run_video(entry: dict, model: str, fps: float, config: str | None = None) -> dict:
    cfg = load_config(config, {"source": {"uri": entry["video"], "pace": "fast", "analysis_fps": fps},
                             "detector": {"model": model},
                             "zones": [ZoneConfig.model_validate(z).model_dump() for z in entry.get("zones", [])]})
    det = TheftDetector(cfg)
    t0 = time.perf_counter()
    state = det.run()
    wall = time.perf_counter() - t0
    events = det.store.list(limit=500)[::-1]
    alerts = [e for e in events if e.severity == "review"]
    used, matched, missed, unsupported = set(), [], [], []
    for lab in entry.get("events", []):
        hit = next((i for i, e in enumerate(alerts) if i not in used and e.event_type == lab["type"]
                    and lab["start"] - EARLY <= e.evidence.detected_stream_s <= lab["end"] + LATE), None)
        if hit is not None:
            used.add(hit)
            matched.append({"label": lab, "delay_s": round(alerts[hit].evidence.detected_stream_s - lab["start"], 2)})
        elif lab.get("supported", True):
            missed.append(lab)
        else:
            unsupported.append(lab)
    false_alerts = [alerts[i] for i in range(len(alerts)) if i not in used]
    return {
        "video": entry["video"], "status": state.status, "detail": state.detail, "wall_s": round(wall, 1),
        "frames_analysed": det.metrics.frames_analysed,
        "matched": matched, "missed": missed, "unsupported_missed": unsupported,
        "false_alerts": [{"type": e.event_type, "at_s": e.evidence.detected_stream_s, "observation": e.observation}
                         for e in false_alerts],
        "info_events": [{"type": e.event_type, "at_s": e.evidence.detected_stream_s} for e in events if e.severity == "info"],
        "dismissed": [{"kind": d.kind, "reason": d.reason, "at_s": round(d.ts, 2)}
                      for d in det.engine.decisions if d.outcome == "dismissed"],
        "safesc_predictions": [{"start": e.evidence.onset_stream_s, "end": e.evidence.detected_stream_s,
                                "detected_at": e.evidence.detected_stream_s} for e in alerts],
        "model_version": det.model_version,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("annotations")
    ap.add_argument("--model", default="yolox_tiny")
    ap.add_argument("--config", default=None, help="YAML overrides (e.g. benchmarks/ab_previous.yaml)")
    ap.add_argument("--fps", type=float, default=10.0)
    ap.add_argument("--json", default="benchmarks/results/videos.json")
    ap.add_argument("--safesc-predictions", default="benchmarks/results/videos.safesc_predictions.json")
    args = ap.parse_args()
    truth = json.loads(Path(args.annotations).read_text(encoding="utf-8"))

    rows = []
    for entry in truth["videos"]:
        if not Path(entry["video"]).is_file():
            print(f"skip (not downloaded): {entry['video']}")
            continue
        r = run_video(entry, args.model, args.fps, args.config)
        rows.append({**r, "duration": entry["duration"], "had_events": bool(entry.get("events"))})
        print(f"{Path(r['video']).name:42s} {r['status']:6s} matched {len(r['matched'])} missed {len(r['missed'])} "
              f"unsupported {len(r['unsupported_missed'])} false {len(r['false_alerts'])} info {len(r['info_events'])} "
              f"| dismissed {[d['reason'] for d in r['dismissed']]}")

    tp = sum(len(r["matched"]) for r in rows)
    fn = sum(len(r["missed"]) for r in rows)
    fp = sum(len(r["false_alerts"]) for r in rows)
    neg_s = sum(r["duration"] for r in rows if not r["had_events"])
    fp_neg = sum(len(r["false_alerts"]) for r in rows if not r["had_events"])
    delays = sorted(m["delay_s"] for r in rows for m in r["matched"])
    summary = {
        "videos": len(rows), "annotated_seconds": round(sum(r["duration"] for r in rows), 1),
        "true_positives": tp, "false_negatives": fn, "false_positives": fp,
        "unsupported_object_events": sum(len(r["unsupported_missed"]) for r in rows),
        "precision": round(tp / (tp + fp), 3) if tp + fp else None,
        "recall": round(tp / (tp + fn), 3) if tp + fn else None,
        "negative_seconds": round(neg_s, 1), "false_alerts_in_negative_videos": fp_neg,
        "median_delay_s": delays[len(delays) // 2] if delays else None,
        "model": args.model, "analysis_fps": args.fps,
    }
    print("\n" + json.dumps(summary, indent=2))
    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json).write_text(json.dumps({"summary": summary, "videos": rows}, indent=2, default=str), encoding="utf-8")
    Path(args.safesc_predictions).write_text(json.dumps({
        "label": f"theft_detection {args.model} @ {args.fps} fps",
        "videos": [{"video": r["video"], "events": r["safesc_predictions"]} for r in rows]}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
