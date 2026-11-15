# Known limitations

## What video can and cannot tell this system

It **can** report that a tracked object moved from near one tracked person to near another,
how fast, and how far apart they ended up; that a resting object was lifted by a track that had
not handled it; that a person stood inside a configured polygon.

It **cannot** determine who owns an object, whether a transfer was consensual, what anyone
intended, whether an entry was permitted, or who anyone is. Every event is a prompt for a human
to look, not a finding.

## Failure cases observed or expected

| Situation | What happens | Seen in |
|---|---|---|
| The bag is hidden by the bodies during the grab | Judged by possession before/after the contact. An alert needs the bag seen with the other person afterwards, a fast departure (≥ 1.3 body heights/s) and the holder observed without a bag for 8 frames; otherwise it is only an info-level event. Alerts come ~2.4 s after onset | P06 traces 92 %; real clip Mixkit 31359: info event only (departure measured at 0.7) |
| A calm hand-over hidden between two bodies | Indistinguishable from a slow, hidden take in boxes: logged at info level, never an alert. A real slow, hidden take is therefore missed by design | N17 / N18 traces |
| Info-level events in SafeSC | Not turned into incidents; they appear only in the worker's timeline and SafeSC's worker telemetry | by design |
| Low light, heavy compression, motion blur | People are still detected; **bags drop out much earlier** (0 % retained at 10 % brightness, 9 % at JPEG quality 8) | `bench_robustness.py` |
| Phone, wallet or key snatching | Not detected at all — not a supported class | Mixkit 31395, 31354 |
| Item taken *out of* a bag | Not detected — the bag itself never moves | Mixkit 31354 |
| Handheld or panning camera | Smooth pans are compensated; shake marks the window unstable and candidates are dismissed as `camera_motion`, which can also suppress a real event | N11 traces |
| Heavy box jitter while a bag holder is passed at close range and then runs off | Person tracks swap and a false `possible_snatching` can result (2–4 per 1.5 h of negative traces at 6–10 % jitter) | N16 jitter sweep |
| Two people overlap heavily during the exchange | Track identities may swap; reported as an uncertainty and the score is lowered | P01 traces |
| Someone carrying a bag runs past a person holding a similar bag, while bag detection is unreliable | The two bag tracks can swap. If the bystander's bag is detected again within ~0.7 s the candidate is dismissed; if it stays undetected for that long, a **false `possible_snatching` is raised** | N10 at 40 % bag misses (2 of 3 seeds before the fix still alert) |
| Very fast run-by at low analysis rate | Recall falls below ~8 FPS ([EVALUATION.md](EVALUATION.md)) | FPS sweep |
| Someone collects a bag left by a person who has left the view | **Deliberately silent** unless the spot is a configured monitored zone: the system cannot tell an owner returning from anyone else | N12, N15 |
| A companion hands back a dropped item / picks up the owner's bag *while the owner is still standing there* | Silent if carried toward or kept near the owner; would be reported if carried away from them | N06, N09 |
| Crowds | Not evaluated beyond ~7 people in view | — |
| Top-down or very oblique cameras | Thresholds are in body heights from the box height; a steep angle distorts that scale | Not evaluated |
| Multiple cameras | Each stream is independent; an event that spans two views is not linked (by design) | — |

## Measurement caveats

* **Trace-level results use mock detections** from a noise model, not a neural network. They
  test the temporal logic under controlled noise, and say nothing about real-world accuracy.
* The real-footage set is nine clips: one staged supported positive, two unsupported (phone)
  staged clips and six ordinary-activity clips. That is a smoke test, not an accuracy estimate.
* `evidence_score` is a heuristic weighted sum. It is not calibrated and must not be read as a
  probability.
* Thresholds were tuned on scripted scenarios with seeds starting at 1000; a held-out set
  (seeds from 90000) is reported separately. Both come from the same scenario generator, so
  "held out" means unseen randomisation, not unseen kinds of behaviour.
* Performance was measured on one laptop whose system drive was full, leaving ~1.6 GB of commit
  headroom; concurrent-stream runs had workers fail under that pressure
  ([PERFORMANCE.md](PERFORMANCE.md#memory-headroom-and-a-fix-it-led-to)).

## Engineering limits

* One process analyses one stream. The API server runs one stream at a time.
* The webhook delivers at least once; receivers must de-duplicate by `event_id`.
* The event store is in memory; restart loses unreviewed events (use `events.jsonl_path` or
  the webhook for durability).
* Evidence clips are written with the VP8 codec; on systems without it the clip is skipped and
  only the still is kept.
