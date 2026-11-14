# Detector cascade (`STREAMING_MODEL=cascade`)

Cheap work runs on every sampled frame; expensive models run only when something is flagged.

| Tier | What runs | When |
|---|---|---|
| 0 | YOLO11n + ByteTrack (people, bicycles, motorbikes) | Every sampled frame |
| Rules | `ml/cascade.py`: zones, dwell timers, track events | Every sampled frame, CPU only |
| 1 | YOLO11n-pose on crops | Only tracks that look down or crouched, are at a rack, or are close to someone while moving fast |
| 2 | Qwen3-VL-4B verifier, one narrow question per incident type | Only rule candidates (`OBSERVATION_VERIFIER_MODEL=qwen3vl`) |
| 3 | Human reviewer | Every incident; nothing is dispatched automatically |

Incident types: `person_down` (horizontal and still for `PERSON_DOWN_SECONDS`), `altercation` (two close people both moving fast for at least 3 samples in 2 s) and `rack_activity` (dwell in a `RACK_ZONES` polygon for `RACK_DWELL_SECONDS`, then a parked bicycle leaves or disappears). Rack activity is a low-priority WATCH, not a theft claim; ownership cannot be judged from video.

Privacy: track IDs are temporary and per camera, with no appearance re-identification, faces, plates or audio.

The live telemetry panel shows a measured per-camera cost meter (Tier 0 ms per frame, pose calls, VLM calls per hour, and how many cameras this device could run at the current sample rate). On a laptop CPU, Tier 0 measured roughly 95 to 215 ms per frame; GPU numbers still need measuring.

Not yet done: tuning thresholds on real campus footage, a dispatcher approve/dismiss card, and GPU measurements.
