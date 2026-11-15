# SafeSC — object-removal event detection module

A standalone, CPU-first video analysis module that watches a camera stream and raises
**reviewable candidates** for:

| Event type | Observed pattern |
|---|---|
| `possible_snatching` | A carried bag leaves its holder abruptly and departs with another person |
| `suspicious_object_removal` | A resting, unattended bag is carried away by a track that never handled it, while whoever set it down is still in view — or from a configured monitored zone |
| `possible_restricted_area_entry` | A person stands inside an explicitly configured polygon (opt-in) |
| `rapid_object_transfer`, `uncertain_interaction` | Info-level context; not alerts |

It reports what moved, not who did wrong. No faces, no identities, no automatic notification
of anyone; a human reviews every candidate.

**Status:** working MVP. The real pipeline runs on CPU and on real footage with measured cost
([PERFORMANCE.md](docs/PERFORMANCE.md)). Event-detection quality is established only on
scripted traces and a handful of real clips — read [EVALUATION.md](docs/EVALUATION.md) before
quoting any number.

## Quick start

Requires Python 3.11 and [uv](https://docs.astral.sh/uv/). Everything runs from this directory
and nothing outside it is modified.

```bash
cd services/theft_detection
uv sync --python 3.11 --extra test
uv run theft-detect fetch-models yolox_tiny        # 20 MB, SHA-256 verified, Apache-2.0
uv run theft-detect make-demo                      # renders synthetic demo clips into data/demo/
uv run theft-detect serve --config configs/demo.yaml
```

Open <http://127.0.0.1:8077/>, pick a source, press **Start**. On Windows with a full system
drive, set `UV_CACHE_DIR` to a folder on another drive before `uv sync`.

### Live webcam

```bash
uv run theft-detect cameras                    # which webcam indices work
uv run theft-detect live --camera 0 --open     # live boxes, track IDs, FPS/CPU/RAM, candidates, trials
```

The page shows the live feed, the engine's current state (who holds which bag, candidates being
verified), rejected candidates with reasons, events with Confirm / Dismiss / Uncertain, and a
**Staged trials** panel for the procedure in [LIVE_TESTING.md](docs/LIVE_TESTING.md). Nothing is
recorded unless `--record-evidence` is given; Ctrl+C releases the camera.

### Inside SafeSC

```bash
# terminal 1 (this folder)
uv run theft-detect live --camera 0 --port 8077 --token change-me --record-evidence
# terminal 2 (repository root; PowerShell: $env:THEFT_WORKER_URL='http://127.0.0.1:8077' etc.)
THEFT_WORKER_URL=http://127.0.0.1:8077 THEFT_WORKER_TOKEN=change-me   uv run uvicorn apps.api.main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>, sign in with the token in `data/app_token`. The **Live monitor**
shows the worker's annotated webcam feed and status; review-level events appear in the existing
**Incident queue** and review page (clip, category, measurements, uncertainty). Decisions made in
SafeSC are mirrored back to the worker. Details: [INTEGRATION.md](docs/INTEGRATION.md).

### Demo script (≈ 5 minutes)

1. **Synthetic, positive** — `synthetic_P01_runby_snatch.mp4`: a `possible_snatching` card
   appears ~0.5 s after the grab with its measured speeds and separation. Mark it *Uncertain*
   or *Confirm*. These scenes are labelled `SIMULATED` everywhere; they show the logic, not
   real-world accuracy.
2. **Synthetic, negative** — `synthetic_N01_handoff_to_friend.mp4`: no alert; the
   *rejected candidates* panel shows `transfer candidate — gradual transfer`.
3. **Unattended removal** — `synthetic_P03_…`: the depositor stays in view while someone else
   walks off with the bag. Compare `synthetic_N02_picks_up_own_bag.mp4`: silent.
4. **Restricted zone** — `synthetic_P05_restricted_zone_entry.mp4` (zone outline drawn; it
   comes from the clip's `.zones.json`).
5. **Real footage, real detector** — `samples/store-aisle-detection.mp4` or a webcam: live
   YOLOX boxes, temporary track numbers, measured FPS / latency / CPU, and no alerts during
   ordinary activity.

### Headless and scripted use

```bash
uv run theft-detect run --source data/samples/people-detection.mp4 --pace fast   # events as JSON lines
uv run theft-detect run --source 0                                               # webcam
uv run theft-detect schema --out docs/event.schema.json
uv run theft-detect purge-evidence --config configs/demo.yaml --all
uv run theft-detect hardware
```

```python
from theft_detection import TheftDetector, load_config

det = TheftDetector(load_config("configs/default.yaml"), on_event=print)
det.run()                                   # or det.start() / det.stop(); or det.process_frame(img, ts)
```

## Supported inputs

| Input | `--source` / `source.uri` | Notes |
|---|---|---|
| Video file | `path/to/clip.mp4` | Replayed at native speed (`pace: realtime`) or as fast as possible (`pace: fast`, deterministic) |
| Webcam | `0`, `1`, … | |
| RTSP / HTTP stream | `rtsp://…` | Reconnects with back-off. Only authorised test sources; refused via the API unless `api.allow_network_sources: true` |
| Synthetic scene | `synthetic:<scenario>[:seed]` | Rendered live; uses the colour-blob detector; events flagged `simulated` |

Supported objects: people, backpacks, handbags, suitcases. **Not** phones, wallets, keys.

## Verification

```bash
uv run pytest                                       # 119 tests; real-model tests skip if weights are absent
uv run python benchmarks/eval_traces.py --seeds 100 --seed-base 90000
uv run python demo/fetch_sample_footage.py
uv run python benchmarks/eval_videos.py evaluation/annotations.json
uv run python benchmarks/bench_pipeline.py --seconds 25
uv run python benchmarks/bench_robustness.py data/samples/*.mp4
```

## Documentation

| | |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Components, data flow, the two-stage cascade |
| [MODEL_SELECTION.md](docs/MODEL_SELECTION.md) | Detector/runtime/tracker comparison, licences (why not Ultralytics) |
| [PERFORMANCE.md](docs/PERFORMANCE.md) | Measured CPU, memory, latency, throughput |
| [EVALUATION.md](docs/EVALUATION.md) | Precision, recall, false alerts/hour, delay — and what they do and do not mean |
| [INTEGRATION.md](docs/INTEGRATION.md) | Event schema 1.0, Python / HTTP / webhook interfaces, mapping to SafeSC |
| [PRIVACY.md](docs/PRIVACY.md) | What is processed and kept, retention, deletion |
| [KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md) | Failure cases |
| [LIVE_TESTING.md](docs/LIVE_TESTING.md) | Webcam set-up, the 8 staged trials, reading a miss |
| [configs/default.yaml](configs/default.yaml) | Every setting, commented |

## Layout

```text
src/theft_detection/
  video/         sources (file, webcam, RTSP, synthetic) and the bounded frame reader
  detection/     detector adapter, YOLOX ONNX backend, pinned weights, synthetic blob detector
  tracking/      ByteTrack-style tracker and assignment solver
  interactions/  geometry, track histories, camera-motion estimate
  events/        temporal engine (stage 1 + 2), schema, store and sinks, evidence capture
  api/           FastAPI app and the review page
  monitoring/    measured metrics
  sim/           scripted scenarios, detection noise model, renderer (test fixtures)
  pipeline.py    TheftDetector — the Python interface
  cli.py         theft-detect command
tests/  benchmarks/  evaluation/  configs/  demo/  docs/
```

Downloaded weights (`weights/`), footage, evidence and logs (`data/`) are ignored by Git.
