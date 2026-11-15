# Palladium interface

The interface is built on the current reviewer application. The design reference
is `feature/palladium-ui-polish` at `652f23f`; the implementation starts from main
at `2162cf7`. The gateway still serves one frontend and the existing FastAPI API.
This UI migration does not change the inference pipeline, live model selection,
incident database, archive, retention, or training policy.

## Three demo tabs

1. **Real-time analysis:** the live monitor appears first, followed by saved videos
   pending human review. **Pair phone camera**, arbitrary video uploads,
   geolocation permission, source telemetry, and manual bookmarks remain available.
   New saved incidents arrive in the same queue through the existing event stream.
   There is no separate demonstration-video selector. The three original public
   recordings remain in Git for reproducible evaluation, without being bundled
   into the frontend or prescribing model results.
2. **Incident response:** choosing **Needs review · Incident response** records the
   human review decision and opens the same incident on the response map. A
   **Past month** button overlays source-linked historical reports for the previous
   calendar month through today in Los Angeles. The selected video has a separate
   red destination marker. Four illustrative responders and pulsing contact rings
   are a browser-session demonstration, clearly labeled **Demo response — no
   officers contacted.** No real responder positions or message deliveries are
   asserted. Resetting the illustration does not change a review decision.
3. **Get Somewhere:** two choices, **Walking routes** and **Ambassador positioning**.
   Routes use local building/landmark search and map picking, connected walking
   paths, departure-time scoring and the existing detour budget. Ambassador posts
   retain 1–8-person scenarios, 400 m network coverage, marginal contributions and
   reasoning. Both presentations use the existing public-data analysis. These
   indices describe report activity, not a probability of personal harm.

The primary tabs remain available while scrolling. **Incidents** is the video
review queue. The **More** menu contains campus exploration/history, learning,
and controls. Existing `#incident=UUID`, `#activity`, `#learning` and `/capture`
links retain their meaning. Reviewer browser notifications remain separate from
source-scoped paired-camera alerts.

## Maps and data

2D and 3D use the same MapLibre map, current public analysis bundle, report-family
taxonomy, routing implementation and staffing scenarios. Perspective and imagery
changes do not select another algorithm or reset the planning inputs. Campus
state survives navigation between tabs and resets when the page reloads.

Named buildings and their tags come from the branch's November 14 OSM snapshot;
coverage is concentrated on central campus. Map picking and the walking network
retain the full supported UPC area. Building data and the favicon
are bundled under `/assets/` so direct FastAPI hosting and the Node gateway use
the same paths. OSM attribution and ODbL continue to apply.

Historical records keep their actual dates, categories, source classifications
and dataset links. Missing analysis is shown explicitly. Satellite imagery and
basemap tiles load online; no external routing or geocoding service is called.
Pedestrian survey totals remain separate from route and staffing calculations.

## Recording locations and response illustration

A phone can save its browser-reported camera position, measurement time and
accuracy when location permission is granted. Location sharing is visibly
selected before starting the camera and can be switched off. A 30-second GPS
watch falls back to browser approximate positioning after timeout, retaining the
actual reported accuracy. Denied permission has a retry control and instructions;
video continues. Existing measurement-age and event-time gates remain in place.
A fix received after an event does not retroactively locate it.

Reviewers can separately assign an approximate area with local place search or
map picking in Incident response. `POST /api/incidents/{id}/location` saves an
audited `reviewer_location`, preserving original camera GPS, predictions and
review decisions. Clearing it reveals the original GPS again, when available.
The three existing recordings were explicitly assigned by the owner to the
Downey Way parking area near Ginsburg Hall. Those assignments live in the incident
database, not in a filename/ID-based frontend rule. Unknown recordings stay unknown.

Only human `needs_review` / `confirmed` classifications enter the response queue.
A model-triggered saved incident can glow red while it awaits that classification;
manual bookmarks and ordinary activity do not acquire an automatic model flag.
The four demonstration positions are nearby real connected-network vertices,
labeled as illustrative ambassador/DPS positions, without representing tracked
people. Selecting **Needs review** starts the local illustration when a usable
location and four positions exist. Missing data disables it. **Mark demo on scene**
and **Reset demo** affect only memory, across views until reload. Dismissed or
trashed incidents leave the response queue. Pulses pause when hidden and respect
reduced motion. Map and geometry modules load only when requested.

## Verification and release boundary

Run `uv run pytest -q`, `node --test apps/gateway/server.test.mjs
apps/web/src/*.test.mjs`, `npm run build --prefix apps/web`, and
`python3 scripts/check_secrets.py --tracked`. The workspace tests use the real
public snapshot to check presentation invariance, route limits, staffing
coverage, camera-location exclusions, response-state transitions and original
demo checksums.

Browser verification must cover all three tabs, camera pairing, upload-to-review,
reviewer labels, Trash/restore, recall, learning controls, 2D/3D state, named-place
search, imagery, historical source links, response illustration, and narrow
layouts. A local fixture backend validates UI/protocol behavior, not real GPU
detection performance or physical-phone connectivity.

**Release boundary:** the owner authorized publishing the Replit UI and then explicitly approved the matching Runpod backend update on November 15, 2026. The code was released from `0103e03`; merge and unrelated hosted-configuration changes remain separate. SMS and real officer/student dispatch remain unconnected.

## Paired phone notifications

The QR capture page displays real gated incident flags from its own camera over
the existing authenticated capture socket. It receives a minimal notification
payload, never another feed, video, coordinates, or review controls. Repeated
source updates do not repeat the notification; saving/saved/failure updates
refresh the card. In-page alerts are always on while capture is open. Vibration
is attempted where supported, and system notifications require an explicit
permission click and browser support. No background push or SMS is configured.

**Test phone alert** is available immediately after scanning. It is clearly marked
as a test and does not create an incident or contact anyone. Real alerts remain
subject to existing evidence gates; fixture alerts are labeled simulated.
Stopping/disconnecting the camera removes the server subscription.

## Live readiness and measured acceptance

Pairing starts the existing live-model warmup in parallel with the QR dialog.
Controls also offers **Prepare live models**. Warmup reserves the GPU against
background jobs and releases that reservation on success or failure. After real
phone or upload analysis finishes, queued GPU jobs wait for 60 idle seconds
(`LIVE_IDLE_GRACE_SECONDS`, 0 disables this grace). CPU geometry exports continue.
A new recording extends the period; existing queued jobs are retained. Running
background jobs are allowed to finish. The existing evidence thresholds, two-pass
confirmation, model weights, and automatic training permissions are unchanged.

`scripts/smoke_demo_phone.py` hashes the three checked-in originals, waits for
live readiness, streams the original frames over the paired-phone protocol in
real time, measures ACKs and flags, and verifies hashes of saved clips. Its local
report excludes credentials but remains under ignored `data/`. It is a diagnostic
on these known demo recordings, not an independent model accuracy evaluation or
a substitute for a physical Safari/Chrome camera test.

The confirmation pass now includes original camera frames received during the
primary verification, restricted to the existing temporal agreement window and
excluding the primary supporting frame IDs. A stale dispatch-time-only pool could
otherwise miss a short action and require another primary pass. Regression tests
cover new-frame inclusion, independent support, and rejection of out-of-window
frames. This does not establish a measured speedup or new accuracy on real users.

Real GPU acceptance on November 15 used the existing deployed backend without
deploying this branch: quiet footage produced no saved incident; contact flagged
at 31.51 seconds into its stream; a readiness-controlled jacket retest flagged
at 37.46 seconds. Each stream used an 8-second static original-frame lead-in.
Saved originals passed SHA-256 checks. Mean frame ACKs were approximately
211–239 ms in those runs. An initial jacket run collided with GPU background
work, processed no live inference frames, and did not save an alert; that failure
motivated the idle grace. These timings remain material limits for a short speech.
Those pre-release checks did not measure the combined scheduler and confirmation
changes on a deployed copy. The approved deployment checks below record that
separately.

## Approved deployment — November 15, 2026

The Palladium UI is published at https://safe-sc.replit.app from source `0103e03`
through the `replit-ui` artifact `546c456`. The same six backend source files
were installed on the existing idle Runpod API after checking deployed hashes,
backing up code and SQLite, and verifying a healthy restart. The 18 existing
incidents, 23 review actions, 37 sources and four learning runs survived the
restart. Model settings, weights, stored media and the database were not replaced.

[Deployment acceptance](../../evaluation/palladium-release-acceptance.json)
records real, non-fixture replays through Replit's public camera WebSocket. All
1,438 sent frames were acknowledged, camera-scoped `saving_context` and `saved`
notifications arrived, and saved clip hashes passed. Existing review decisions,
labels and holds were preserved. The three verification incidents were moved
to recoverable Trash after checks; their original media remains available.

**Model acceptance failed:** the quiet clip produced a physical-contact false
alert, and the jacket-taking clip was labeled physical contact rather than
object taking. The contact-and-fall clip matched its expected action. First
phone alerts arrived approximately 31.6, 25.5 and 51.3 seconds from test start,
respectively, with an eight-second scripted lead-in target. The test clock starts
before the camera handshake; these are not action-onset response times. These
are diagnostic known recordings, not an independent accuracy study or proof of a latency improvement.
Phone notification transport passed; model accuracy and response time remain
material limitations. A physical phone and native browser recording were not
used in these replays. Use a fresh QR after the API restart.

## Pending-video assessments

Pending cards show stored native MOSS observations, or the initial verifier
assessment if native observations are unavailable. Provenance distinguishes the
observer from the visual verifier and secondary clip review. Reviewer labels and
review decisions remain separate; existing reviewed context stays in incident
review and the audit. No prediction is selected from an upload filename or ID.

On the data host, `python -m scripts.analyze_saved_moss INCIDENT_ID --save` can
request a native MOSS pass over a completed uploaded original. This verifies the
source hash, requires idle capture/jobs and an idle prepared MOSS service, saves
the prior record and raw inference under ignored `data/moss-analysis`, and attaches
only a completed result. It never creates an alert or changes human decisions.
Incomplete passes remain unavailable, not classified as ordinary activity.

The current working UI is previewed directly on Runpod. Replit publication is
held at the owner's request while further design changes are reviewed.
