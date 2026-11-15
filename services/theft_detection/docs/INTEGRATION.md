# Integrating with SafeSC

The module is self-contained. SafeSC does not import its internals and the module imports
nothing from SafeSC; the contract between them is the **event schema, version 1.0**.

Three ways to consume events, simplest first:

| Interface | Use when |
|---|---|
| Python callback | SafeSC runs the detector in-process |
| HTTP polling (`GET /api/v1/events`) | SafeSC runs it as a sidecar service (**what SafeSC uses; see below**) |
| Webhook (`events.webhook.url`) | SafeSC wants push delivery |

A WebSocket stream, Kafka or Redis were considered and not added: one camera produces a
handful of events per hour, and polling or a webhook covers that without new infrastructure.

## Event schema 1.0

Machine-readable copy: [`event.schema.json`](event.schema.json) (regenerate with
`theft-detect schema --out docs/event.schema.json`), also served at `GET /api/v1/schema`.

```json
{
  "schema_version": "1.0",
  "event_id": "evt_3fa1c0d2b9e47a10",
  "source_id": "test_camera_01",
  "event_type": "possible_snatching",
  "timestamp_utc": "2026-11-14T23:41:07.512Z",
  "evidence_score": 0.86,
  "score_calibrated": false,
  "severity": "review",
  "observation": "A tracked backpack left the reach of person_1, who had carried it for 4 s, and moved away with person_4 at 2.7 body heights per second, ending 1.7 body heights from person_1 after 0.5 s.",
  "uncertainties": [],
  "evidence": {
    "clip_reference": null,
    "frame_reference": null,
    "onset_stream_s": 4.0,
    "detected_stream_s": 4.5,
    "detection_delay_s": 0.5,
    "processing_lag_s": 0.021,
    "features": {"object_speed_before": 0.29, "object_speed_after": 2.68, "object_holder_separation": 1.75},
    "tracks": [{"ref": "person_1", "role": "prior_holder"}, {"ref": "person_4", "role": "subsequent_holder"}],
    "object_label": "backpack",
    "object_ref": "object_2",
    "zone_id": null
  },
  "model_version": "theft_detection/0.1.0 detector=yolox_tiny@sha256:427cc366d34e tracker=bytetrack-np",
  "review_status": "pending",
  "review": null,
  "simulated": false
}
```

| Field | Meaning |
|---|---|
| `event_id` | Stable. Hash of source, stream session and the event's own key; replaying the same file gives the same ID, so consumers can de-duplicate on it. |
| `event_type` | `possible_snatching`, `suspicious_object_removal`, `rapid_object_transfer`, `possible_restricted_area_entry`, `uncertain_interaction`. Observed patterns, not conclusions. |
| `severity` | `review`: show to a human reviewer. `info`: timeline context only; do not notify. |
| `evidence_score` | Heuristic evidence quality in [0, 1]. `score_calibrated` is always `false`: **not a probability**. Use it for ordering, not for thresholds with consequences. |
| `observation` | What moved, in neutral terms. The schema rejects accusatory or personal vocabulary. |
| `uncertainties` | Reasons the evidence is weaker than it looks. Show them to the reviewer. |
| `evidence.*_stream_s` | Position in the stream (media time for files, seconds since open for live). `detection_delay_s` includes the verification window. |
| `evidence.clip_reference` / `frame_reference` | File names under the evidence directory; `null` unless evidence capture is enabled. Fetch with `GET /api/v1/evidence/{name}`. |
| `evidence.tracks` | Ephemeral, camera-local, anonymous references. They mean nothing outside this stream session. |
| `review_status` | `pending` → `confirmed` / `dismissed` / `uncertain`, set by a human. |
| `simulated` | `true` when the footage or detections were synthetic. Never present these as real detections. |

Compatibility rule: fields may be **added** within 1.x; anything else bumps the major version.
Consumers should ignore unknown fields.

## In-process (Python)

```python
from theft_detection import TheftDetector, load_config

def on_event(event):                       # called once per new event, from the analysis thread
    if event.severity == "review":
        safesc_queue.put(event.model_dump(mode="json"))

cfg = load_config("configs/default.yaml", {"source": {"uri": "rtsp://...", "source_id": "lobby_cam"}})
detector = TheftDetector(cfg, on_event=on_event)
detector.start()                           # background thread; detector.stop() to end
```

If SafeSC already decodes frames (the phone pipeline does), skip the module's capture layer:

```python
result = detector.process_frame(bgr_frame, ts=seconds_since_stream_start)
for event in result.events: ...
```

A raising callback is caught and counted (`store.listener_errors`); it cannot stop detection.

## Sidecar (HTTP)

Start with `theft-detect serve --config configs/default.yaml`. Binds to `127.0.0.1:8077`.

| Method and path | Purpose |
|---|---|
| `GET /health` | Liveness, module version, schema version. Never requires a token. |
| `GET /api/v1/status` | Pipeline state, measured FPS/latency/CPU/memory, dropped frames. |
| `GET /api/v1/events?status=&severity=&since=&limit=` | Newest first. `since` is an ISO-8601 UTC timestamp. |
| `GET /api/v1/events/{event_id}` | One event; `404` if unknown. |
| `POST /api/v1/events/{event_id}/review` | Body `{"status": "confirmed" \| "dismissed" \| "uncertain", "note": ""}`. |
| `GET /api/v1/decisions` | Recent stage-2 outcomes including dismissed candidates and why. |
| `GET /api/v1/schema` | JSON Schema of the event. |
| `GET /api/v1/evidence/{name}` | Saved still or clip, when capture is enabled. |
| `POST /api/v1/source`, `POST /api/v1/stop` | Demo control: choose a source. Files must be under `api.demo_dir`; network URLs are refused unless `api.allow_network_sources` is true. |
| `GET /api/v1/stream.mjpg` | Annotated preview for the demo UI. |

Failures are explicit: `401` bad or missing token (when `api.token` is set), `403` network
source disabled, `404` unknown event/video/evidence, `422` body or query that fails validation,
`400` detector weights unavailable. A source that cannot be opened is reported through
`status: "error"` with the reason in `detail`.

Polling loop for SafeSC:

```python
since = None
while True:
    params = {"severity": "review", **({"since": since} if since else {})}
    for ev in reversed(httpx.get(f"{BASE}/api/v1/events", params=params).json()):
        handle(ev)                         # de-duplicate on ev["event_id"]
        since = ev["timestamp_utc"]
    time.sleep(1)
```

## Webhook

```yaml
events:
  webhook:
    url: http://127.0.0.1:8000/internal/theft-events   # nothing is sent unless this is set
    secret: change-me                                  # optional
```

Each event is `POST`ed as the JSON above with `X-SafeSC-Event-Id`, and, when a secret is set,
`X-SafeSC-Signature: sha256=<HMAC-SHA256 of the raw body>`. Delivery is at-least-once with
bounded retries on `5xx`/`429`/network errors; the receiver must de-duplicate on `event_id`.
The queue is bounded; overflow and failures are counted, never raised into detection.

## The SafeSC adapter (implemented)

SafeSC consumes the worker through `apps/api/theft.py`. Run the worker as its own process; SafeSC
polls it. Nothing in SafeSC imports this module, and this module imports nothing from SafeSC.

```text
webcam -> theft-detect live (worker process, :8077)            SafeSC (uvicorn, :8000)
            detection, tracking, verification                     apps/api/theft.py poller (1 s)
            GET /api/v1/status, /api/v1/events  <---------------  validate (local schema 1.x copy)
            GET /api/v1/evidence/<id>.webm|.jpg <---------------  Incident(id = event_id) + evidence
            GET /api/v1/frame.jpg               <---------------  /api/theft/frame -> live monitor
            POST /api/v1/events/<id>/review     <---------------  review decision mirrored back
```

### Enabling it

```bash
# terminal 1 - the worker (webcam)
cd services/theft_detection
uv run theft-detect live --camera 0 --port 8077 --token change-me

# terminal 2 - SafeSC, from the repository root
THEFT_WORKER_URL=http://127.0.0.1:8077 THEFT_WORKER_TOKEN=change-me   uv run uvicorn apps.api.main:app --host 127.0.0.1 --port 8000
```

(Windows PowerShell: `$env:THEFT_WORKER_URL='http://127.0.0.1:8077'; $env:THEFT_WORKER_TOKEN='change-me'`.)
With `THEFT_WORKER_URL` empty - the default - SafeSC behaves exactly as before: no task is started
and `/api/theft/*` answers 404.

| Setting | Default | Meaning |
|---|---|---|
| `THEFT_WORKER_URL` | empty (off) | Worker base URL |
| `THEFT_WORKER_TOKEN` | empty | Sent as `Authorization: Bearer`; must match the worker's `--token` |
| `THEFT_POLL_SECONDS` | 1 | Poll interval |
| `THEFT_TIMEOUT_SECONDS` | 2 | Per-request timeout for every worker call |

### What SafeSC does with an event

| Worker event | SafeSC |
|---|---|
| valid, `severity: review` | new `Incident` with `id = event_id` (so a duplicate is impossible), `severity = REVIEW_REQUIRED`, `created_at = timestamp_utc`, `start/end_time = onset/detected stream seconds`, `confidence = evidence_score`, `simulated` kept |
| `event_type` | `record.category` (shown on the queue card and the review page) |
| `observation`, `uncertainties`, person track refs | `record.observable_events`, `record.uncertainties`, `actors` |
| `model_version` | `record.model.revision`; `record.confidence_note` states the score is uncalibrated |
| `clip_reference`, `frame_reference` (written a few seconds after the event) | fetched from the worker when they appear and attached to the *same* incident as `theft_evidence.webm` and `thumbnail.jpg`, with SHA-256 |
| `review_status` | `pending→pending`, `confirmed→confirmed`, `dismissed→dismissed`, `uncertain→needs_review` |
| valid, `severity: info` | not an incident; listed in the worker telemetry (`/api/health → theft_worker.recent_info_events`) |
| invalid (schema, vocabulary, naive timestamp, schema 2.x, …) | rejected, counted, last reason kept in `theft_worker.last_rejection` |
| already stored | ignored (`duplicates_ignored`) |

A reviewer's decision in SafeSC is mirrored to the worker (`confirmed→confirmed`,
`dismissed→dismissed`, `needs_review→uncertain`). SafeSC's own `ReviewAction` history stays the
record of truth; the mirror is best effort.

`HIGH_PRIORITY_REVIEW` is never produced: a tracker-based heuristic should not self-escalate.

### Worker health and failure isolation

`GET /api/health` (and `/api/theft/status`) report `theft_worker`: `reachable`, `pipeline`
(`running`, `ended`, `error`, ...), `detail` (e.g. "could not open webcam 0 ..."), detector,
analysed FPS, p95 latency, and counters. The dashboard's live monitor shows the worker's
annotated feed when no phone or upload source is active, and a status line under telemetry.

Every worker call runs in SafeSC's event loop with a 2 s timeout and all errors caught. Tested
(`tests/test_theft_integration.py`): worker returning 500s, worker absent, worker hanging for 5 s
per request, recovery afterwards - SafeSC's own endpoints keep answering in well under a second.
The worker is equally indifferent to SafeSC: it runs, records trials and keeps events whether or
not anything polls it.

### Push instead of poll

`POST /api/theft/events` (behind SafeSC sign-in) accepts the same JSON. Point the worker's
webhook at it with `events.webhook.url` and `events.webhook.bearer_token` set to SafeSC's app
token. Polling is the default because it also fetches evidence, health and the live frame.

### SafeSC files changed

| File | Change |
|---|---|
| `apps/api/theft.py` | new: schema copy, adapter, poller, routes |
| `apps/api/main.py` | +5 lines: router, poller start when enabled, review mirror, health field |
| `apps/api/config.py` | 4 settings (off by default) |
| `apps/web/src/main.tsx` | review page shows the worker clip, category banner and measurements; queue card shows the category; live monitor shows the worker feed and status |
| `.env.example` | the 4 settings |
| `tests/test_theft_integration.py`, `tests/fixtures/theft_events_v1.json` | new tests; fixtures generated by this module's real event code |

## What the module will not do

It does not notify anyone by itself, contact any external service unless a webhook URL is
configured, identify people, or keep video. Those decisions belong to SafeSC and its human
reviewers.
