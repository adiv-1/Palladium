# Palladium interface

The interface is built on the current reviewer application. The design reference
is `feature/palladium-ui-polish` at `652f23f`; the implementation starts from main
at `2162cf7`. The gateway still serves one frontend and the existing FastAPI API.
This UI migration does not change the inference pipeline, live model selection,
incident database, archive, retention, or training policy.

## Three demo tabs

1. **Real-time analysis:** choose one of the three uploader-authorized GitHub
   recordings, preview its original footage, and select **Analyze this recording**.
   The original bytes are verified against the checked-in SHA-256 manifest and
   sent through the existing upload endpoint. The page moves to the live monitor.
   **Pair phone camera**, arbitrary video uploads, geolocation permission, source
   telemetry, and manual bookmarks remain available. Descriptions in the clip
   selector are reviewed metadata, not predetermined analysis results.
2. **Ambassador positioning:** choose date, Los Angeles time, report family and
   1–8 ambassadors. Select a numbered recommendation to inspect its marginal
   modeled coverage and location. The existing 400 m network coverage scenarios
   count overlap once; no invented current deployment is used as a comparator.
3. **Get Somewhere:** search the local OSM building/landmark index or select map
   points. Compare the direct connected walk with an alternative within the
   selected detour budget. Replay is illustrative. The index describes modeled
   report activity, not a probability of personal harm or a validated safest path.

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
retain the full supported UPC area. Building data, the favicon and demo clips
are bundled under `/assets/` so direct FastAPI hosting and the Node gateway use
the same paths. OSM attribution and ODbL continue to apply.

Historical records keep their actual dates, categories, source classifications
and dataset links. Missing analysis is shown explicitly. Satellite imagery and
basemap tiles load online; no external routing or geocoding service is called.
Pedestrian survey totals remain separate from route and staffing calculations.

## Event-map illustration

Incident review can open an event map when the saved record contains a usable
permissioned camera fix. It shows accuracy and camera-position provenance;
uploads and unavailable positions receive no inferred location.

The reviewer chooses a **Demo staging point** and starts **Simulate response**.
A synthetic unit moves along the existing connected walking graph. Unsupported
endpoints show an error and retain the camera pin. At the end, the state becomes
**Awaiting demo arrival confirmation**. Only **Mark on scene** ends that state.
**Reset demo** clears it. Reduced motion skips the travel animation.

The illustration is memory-only and independent of real incident decisions,
reviewed labels, learning permissions, and training. It has no dispatch endpoint,
phone bus, external message, officer acknowledgement, or student alert. The
panel states **Demo response — no officers contacted.** Animations stop when
hidden or unmounted; map and geometry modules load only when requested.

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

**Release boundary:** the owner authorized pushing `feature/palladium-design` after local checks. Merge, deployment and hosted-configuration changes remain separate. Paired-camera notifications are included at the owner’s request; SMS and real officer/student dispatch remain unconnected.

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
The new scheduler and confirmation changes are regression-tested locally; their
combined GPU latency has not been measured on a deployed copy of this branch.
