# Privacy, retention and responsible use

## What is processed

* Frames are decoded, downscaled and analysed **in memory**. By default nothing is written to
  disk and no frame leaves the process.
* The detector outputs boxes for `person`, `backpack`, `handbag`, `suitcase`. The tracker uses
  box geometry and motion only.
* The event engine sees boxes and timestamps, never pixels.

## What is deliberately absent

| Not implemented | Why |
|---|---|
| Face detection or recognition | Not needed to see an object change hands. |
| Appearance embeddings / re-identification | Track IDs die with the track; a person who leaves and returns is a new, unrelated number. Trackers that rely on ReID features (DeepSORT, BoT-SORT-ReID) were rejected for this reason. |
| Cross-camera association | Each stream is independent; IDs are camera-local. |
| Demographic or identity attributes | Not computed, and the event schema rejects such words in an observation. |
| Statements of ownership, intent or guilt | Event types are hedged (`possible_…`, `suspicious_…`); the schema rejects accusatory vocabulary; `evidence_score` is flagged uncalibrated. |
| Automatic notification of anyone | Events go to a store, an optional local log, and an optional webhook that SafeSC configures. A human reviews before anything else happens. |

## Evidence capture (optional, off by default)

`evidence.enabled: false` is the default. When switched on:

* a ring buffer holds only the last `pre_s` seconds (default 3) of analysed frames, as JPEG
  bytes in memory;
* when an event is emitted, those frames plus `post_s` seconds (default 2) are written as one
  still (`<event_id>.jpg`) and one clip (`<event_id>.webm`) in `evidence.directory`;
* nothing continuous is recorded, and frames with no event are discarded as the ring advances;
* files older than `retention_hours` (default 24) are deleted at start-up and every ten minutes
  while running.

Saved frames are the *annotated* preview (boxes and temporary track numbers), downscaled to
`evidence.max_width`.

### Deleting evidence

```bash
theft-detect purge-evidence --config configs/demo.yaml          # delete expired files
theft-detect purge-evidence --config configs/demo.yaml --all    # delete everything now
```

Only files this module created (`evt_<id>.jpg` / `.webm`) are touched.

### Keeping evidence out of the repository

`data/` (videos, evidence, event logs) and `weights/` are ignored by Git at both the
repository root and in this module. Do not move evidence elsewhere in the tree.

## Logs

The optional JSON-lines log (`events.jsonl_path`) contains event metadata only: types, times,
scores, measured features and anonymous track references. No pixels, no names.

## Access

The API binds to `127.0.0.1`. Set `api.token` before exposing it to anything else; evidence,
events and review endpoints then require the token. Starting network video sources through the
API is refused unless `api.allow_network_sources` is explicitly enabled.

## Footage used during development

| Footage | Source and terms | Stored |
|---|---|---|
| Synthetic scenes | Rendered by `sim/render.py`; flat-colour figures, watermarked "SYNTHETIC" | Generated locally into `data/demo/` |
| Six real clips of people walking, shopping, working | Intel IoT DevKit `sample-videos`, CC-BY-4.0 | Downloaded locally into `data/samples/`, not committed |
| Three staged stock clips (actors) | Mixkit, "Stock Video Free License"; downloaded for local evaluation only, not redistributed. Read the licence at mixkit.co/license before any other use. | `data/stock/`, not committed |

No USC camera, no real surveillance feed and no footage of non-consenting people was used.

## Before any real deployment

This is a hackathon module. Operating it on real campus cameras would additionally require
USC authorisation and camera-access governance, a privacy review, signage and policy decisions
about notice, a retention schedule approved by the institution, access control and audit logs
for reviewers, a documented process for people to contest a review, and validation on
representative footage ([EVALUATION.md](EVALUATION.md) explains why current results do not
provide that).
