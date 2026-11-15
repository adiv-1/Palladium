# Architecture

A standalone module. It imports nothing from the SafeSC application and can be run, tested and
benchmarked on its own. SafeSC consumes its output through the versioned event schema
([INTEGRATION.md](INTEGRATION.md)).

## Data flow

```text
 file / webcam / RTSP / synthetic scene
            |
   video/sources.py      grab() without decoding -> skip frames the sampler will not use
   video/reader.py       capture thread: sample to analysis_fps, downscale, bounded queue (drop oldest)
            |
   detection/            Detector adapter:  yolox_onnx (real)  |  blob (synthetic scenes)
            |               person, backpack, handbag, suitcase -> categories "person" / "bag"
   interactions/motion   global camera shift by phase correlation on a 160 px thumbnail
            |
   tracking/bytetrack    ByteTrack-style association, time-aware Kalman filter, motion gate
            |               ephemeral, camera-local track IDs; no appearance features
   events/engine.py      STAGE 1: per-object holder / resting state -> candidates
            |             STAGE 2: verify candidates over a short window -> emit / downgrade / dismiss
   pipeline.py           wrap in schema 1.0, stable ID, de-duplicate, store
            |
   events/store.py  -->  Python callback | JSON-lines log | webhook (only if configured)
   events/evidence.py    optional short clip around the event (off by default)
   api/app.py            FastAPI: events, review, status, MJPEG preview, minimal review UI
```

## Components

| Path | Responsibility |
|---|---|
| `video/sources.py` | One interface for files, webcams, network streams and rendered scenes. Reconnection with back-off for live sources, clean end-of-stream for files, frame validation. |
| `video/reader.py` | Capture thread. `FrameSampler` hits the requested average analysis rate; the queue is bounded and drops the *oldest* frame in real-time mode, so latency cannot accumulate. `fast` mode blocks instead (deterministic evaluation). |
| `detection/base.py` | `Detector` protocol (`detect(image) -> list[Detection]`). Swap the backend without touching anything else. |
| `detection/yolox_onnx.py` | YOLOX in ONNX Runtime, CPU by default, thread-capped. Bag classes share NMS so one bag does not fire twice. |
| `detection/models.py` | Pinned download URL, SHA-256 and licence for each weight file; a file that fails the hash is refused. |
| `tracking/bytetrack.py` | High-score association, then low-score ("BYTE") association, then a motion gate for objects that move further than their own size between analysed frames. |
| `interactions/` | Geometry helpers, per-track history with camera-motion-compensated positions, global motion estimate. |
| `events/engine.py` | The temporal logic (below). Pure Python over tracks; never sees pixels. |
| `events/schema.py` | Public contract. Rejects accusatory or personal vocabulary in observations. |
| `events/store.py` | Bounded in-memory store, de-duplication by event ID, review state, JSON-lines and webhook sinks. |
| `events/evidence.py` | Optional in-memory ring of a few seconds of JPEGs; writes one still and one clip per event; retention purge. |
| `monitoring/metrics.py` | Measured FPS, latency percentiles, CPU and peak memory. |
| `sim/` | Scripted scenarios, a detection-noise model and a renderer: controlled test fixtures, clearly labelled as such. |

## The two-stage cascade

All speeds and distances are in **body heights** (the pixel height of the person involved), so
one set of thresholds applies to near and far subjects.

### Stage 1 — runs on every analysed frame, costs well under a millisecond

For each tracked object the engine keeps a small state:

```text
            assoc >= 1 s                    out of reach, with another person
  (new) ───────────────► HELD(by A) ──────────────────────────────► transfer candidate (A → B)
    │                        │  out of reach, nobody near
    │ still, alone >= 3 s    ▼
    └──────────────────► RESTING(set down by A, or origin unknown)
                             │ lifted and moving with P            ► pickup candidate (P)
                             │ no longer detected, spot in clear view ► disappearance check
```

* "With a person" means at least 40 % of the object's box lies inside that person's box widened
  by arm's reach; an established holder keeps the object down to 25 % (hysteresis), so someone
  brushing past cannot take over the association.
* A person becomes the *holder* only after one continuous second. Proximity alone never produces
  a candidate, and neither does a single frame: leaving reach must persist for two frames.
* The engine remembers which tracks have *handled* an object. Those tracks can pick it up again
  without any event.

### Stage 2 — runs only while a candidate is open (0.5–2.0 s)

For a transfer candidate the verifier measures, from the track histories:

| Evidence | Why it matters |
|---|---|
| object speed before vs. after, and the increase | separates a grab from a hand-over; also immune to *person* ID swaps, because it is about the object |
| object and taker separation from the prior holder | a hand-over between companions usually ends with them still together |
| prior holder accelerating in the same direction | a chase; allows a decision at a smaller separation |
| fraction of frames in which the object was really detected | a mostly-predicted trajectory is not evidence |
| a freshly detected object still with the prior holder — as a new track, or in the raw detections of ≥ 2 of the frames after the transfer | the "transfer" was an occlusion plus an identity mix-up between two similar bags; the raw-detection check matters because an intermittently detected bag may never form a confirmed track. When raw detections are available, a review alert waits until 4 such frames have been examined |
| the taker's own object stopped being detected at the same moment | the two objects' track identities may have been confused; blocks a review alert |
| camera instability during the window | shake produces apparent motion |
| time the two people spent together beforehand, overlap of their boxes | lowers the score; reported to the reviewer |

Outcomes: `possible_snatching` (review) when the transfer was abrupt, the object departed and
nothing blocks; `uncertain_interaction` (info) when it looked like that but the evidence was
incomplete; `rapid_object_transfer` (info) when it was quick but nobody left; otherwise the
candidate is **dismissed with a recorded reason**, which the UI shows under "rejected".

A resting object that is lifted becomes `suspicious_object_removal` only when it is carried at
least one body height by a track that never handled it **and** either the track that set it
down is still in view and is not the destination, or it rested inside a configured monitored
zone. Without either, the system has no basis to say anything and stays silent.

`possible_restricted_area_entry` needs an explicitly configured polygon; the person's foot point
must stay inside for a dwell time.

Whether stage 2 earns its latency is measured, not assumed: see
[EVALUATION.md](EVALUATION.md#does-stage-2-pay-for-itself).

## Duplicate suppression

* Per object and per pair of people: a cooldown (10 s by default) after an event.
* Per zone: a cooldown, and each track is reported at most once per zone.
* Event IDs are a hash of source, stream session and the event's own key, so replaying the
  same file yields the same IDs and the store (or any consumer) drops repeats.

## Threads and bounded resources

| Resource | Bound |
|---|---|
| Frame queue | `source.queue_size` frames (default 2); oldest dropped when full |
| Detector CPU | `detector.intra_op_threads` (default 4) |
| Track history | `engine.history_s` seconds per track (default 12 s) |
| Event store | `events.max_stored` (default 500) |
| Decision log | last 200 |
| Evidence ring | `evidence.pre_s` seconds of JPEGs, only when capture is enabled |
| Webhook queue | `events.webhook.queue_size`; overflow is counted and dropped |

Two threads per stream: capture and analysis. The API adds the server's own threads; the MJPEG
preview only re-sends the last already-encoded frame.

## Where the suggested layout was adapted

Stage 2 lives in `events/engine.py` next to stage 1 rather than in a separate `verification/`
package: both operate on the same track histories and splitting them only added indirection.
