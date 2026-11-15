"""Trace-level evaluation of the tracker + event engine on scripted scenarios.

    python benchmarks/eval_traces.py --seeds 40
    python benchmarks/eval_traces.py --seeds 40 --sweep fps
    python benchmarks/eval_traces.py --seeds 40 --json benchmarks/results/traces.json

Detections here come from a noise model, NOT from a neural network, so these numbers measure
the temporal logic only. They are not real-world detection accuracy.
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path

import numpy as np

from theft_detection.config import Config, load_config
from theft_detection.sim import scenarios
from theft_detection.sim.runner import Score, TraceResult, run_trace, score_result
from theft_detection.sim.world import ObsParams
from theft_detection.types import RawEvent

STAGE1_TYPE = {"transfer": scenarios.SNATCH, "pickup": scenarios.REMOVAL, "removal": scenarios.REMOVAL}


def stage1_only(res: TraceResult) -> TraceResult:
    """What the system would alert on if every stage-1 candidate were emitted unverified."""
    events = [e for e in res.events if e.event_type == scenarios.ZONE_ENTRY]
    for d in res.decisions:
        if d.kind in STAGE1_TYPE and d.reason != "duplicate_within_cooldown":
            onset = d.ts - float(d.features.get("elapsed_s", 0.0))
            events.append(RawEvent(event_type=STAGE1_TYPE[d.kind], severity="review", onset_ts=onset,
                                   detected_ts=onset + 0.3, score=0.5, observation=""))
    return TraceResult(res.scenario, events, res.decisions, res.frames)


def evaluate(cfg: Config, seeds: int, fps: float, params: ObsParams, only: str | None = None,
             verbose: bool = False, seed_base: int = 1000):
    total, total_s1 = Score(), Score()
    rows, reasons = [], Counter()
    for fn in scenarios.ALL:
        per, per_s1 = Score(), Score()
        name = ""
        for seed in range(seeds):
            sc = fn(np.random.default_rng(seed_base + seed))
            name = sc.name
            if only and only not in name:
                break
            res = run_trace(sc, cfg, fps=fps, params=params, seed=seed_base + seed)
            sc_score = score_result(res)
            per.add(sc_score)
            per_s1.add(score_result(stage1_only(res)))
            for d in res.decisions:
                if d.outcome == "dismissed":
                    reasons[d.reason] += 1
            if verbose and (sc_score.fp or sc_score.fn):
                print(f"  ! {name} seed={seed} fp={sc_score.false_alerts} fn={sc_score.missed}")
                for e in res.events:
                    print("      event", e.event_type, round(e.detected_ts, 2), e.features)
                for d in res.decisions:
                    print("      decision", d.kind, d.outcome, d.reason, round(d.ts, 2), d.features)
        if only and only not in name:
            continue
        total.add(per)
        total_s1.add(per_s1)
        d = sorted(per.delays)
        rows.append({"scenario": name, "runs": seeds, "labelled_events": per.tp + per.fn,
                     "detected": per.tp, "missed": per.fn, "false_alerts": per.fp,
                     "info_events": per.info_events,
                     "median_delay_s": round(d[len(d) // 2], 2) if d else None,
                     "stage1_only_false_alerts": per_s1.fp})
    return total, total_s1, rows, reasons


def print_rows(rows) -> None:
    hdr = f"{'scenario':52s} {'runs':>4s} {'events':>6s} {'hit':>4s} {'miss':>4s} {'FA':>3s} {'info':>4s} {'delay':>6s} {'S1-FA':>5s}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(f"{r['scenario']:52s} {r['runs']:4d} {r['labelled_events']:6d} {r['detected']:4d} "
              f"{r['missed']:4d} {r['false_alerts']:3d} {r['info_events']:4d} "
              f"{'' if r['median_delay_s'] is None else r['median_delay_s']:>6} {r['stage1_only_false_alerts']:5d}")


SWEEPS = {
    "fps": ("fps", [3, 5, 8, 10, 15]),
    "bag_miss": ("bag_miss", [0.0, 0.1, 0.25, 0.4, 0.55]),
    "jitter": ("jitter", [0.0, 0.03, 0.06, 0.10]),
    "frame_loss": ("frame_loss", [0.0, 0.1, 0.25, 0.4]),
    "person_miss": ("person_miss", [0.02, 0.1, 0.2, 0.35]),
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=None)
    ap.add_argument("--seeds", type=int, default=40)
    ap.add_argument("--fps", type=float, default=10.0)
    ap.add_argument("--only", default=None, help="substring of a scenario name")
    ap.add_argument("--seed-base", type=int, default=1000,
                    help="1000 = seeds used while tuning; use e.g. 90000 for a held-out run")
    ap.add_argument("--sweep", choices=sorted(SWEEPS), default=None)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()
    cfg = load_config(args.config)
    out: dict = {"seeds_per_scenario": args.seeds, "seed_base": args.seed_base,
                 "note": "mock detections; temporal logic only"}
    t0 = time.perf_counter()

    if args.sweep:
        field, values = SWEEPS[args.sweep]
        out["sweep"] = {"parameter": field, "points": []}
        print(f"sweep over {field} ({args.seeds} seeds per scenario)")
        print(f"{field:>12s} {'precision':>9s} {'recall':>7s} {'FA/h(neg)':>10s} {'median delay':>12s} {'p90 delay':>10s}")
        for v in values:
            fps = float(v) if field == "fps" else args.fps
            params = ObsParams() if field == "fps" else replace(ObsParams(), **{field: v})
            total, _, rows, _ = evaluate(cfg, args.seeds, fps, params, args.only, seed_base=args.seed_base)
            sm = total.summary()
            out["sweep"]["points"].append({"value": v, **sm, "per_scenario": rows})
            print(f"{v:>12} {sm['precision']!s:>9} {sm['recall']!s:>7} {sm['false_alerts_per_hour_negative']!s:>10} "
                  f"{sm['median_time_to_detection_s']!s:>12} {sm['p90_time_to_detection_s']!s:>10}")
    else:
        total, total_s1, rows, reasons = evaluate(cfg, args.seeds, args.fps, ObsParams(), args.only, args.verbose,
                                                  seed_base=args.seed_base)
        print_rows(rows)
        print("\nwith stage-2 verification :", json.dumps(total.summary()))
        print("stage 1 only (unverified) :", json.dumps(total_s1.summary()))
        print("\ndismissal reasons:", dict(reasons.most_common()))
        out.update({"analysis_fps": args.fps, "per_scenario": rows, "overall": total.summary(),
                    "stage1_only": total_s1.summary(), "dismissal_reasons": dict(reasons)})
    print(f"\n({time.perf_counter() - t0:.1f} s)")
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(out, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
