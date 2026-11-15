# Live observations and incident review

The original live preview uses an authenticated WebSocket (`/ws/preview`) with one unacknowledged display frame per viewer. After the browser paints that frame, the server selects the newest received camera frame. Slow viewers skip old images; their display queue does not grow. The phone can have at most two capture frames in flight, defaults to a 960px long edge at up to 12 FPS, and offers a 1280px / 8 FPS detail option. Actual rates depend on capture, network and decoding. Dashboard display FPS and its preview acknowledgement round trip are separate from model inference latency.

MOSS's completed native responses form a timestamped live observation feed. Person-category words are normalized to person/people and marked in message metadata; physical-action descriptions are not fabricated. The bounded source telemetry retains the native response for audit. Other unsupported legal or personal descriptions are withheld. A received timestamp is not an exact action timestamp. Silence does not establish that nothing happened. Extra scene prompts wait for the current response to finish. No YOLO/ByteTrack object overlay is enabled in this deployment. Experimental MOSS localization is disabled: the installed NF4 model did not reliably produce valid frame-aligned spatial JSON in the public-image probe. Do not label approximate or malformed output as operational object detection.

The existing Qwen live verifier and temporal agreement gate control incident creation. A gated possible incident highlights the live preview and adds a red review notice. The configured pre-event and post-event context is saved automatically. Clip encoding runs in an asynchronous CPU task so it cannot hold up incoming phone acknowledgements. A notice says **saved** only after the incident is committed; storage failures are reported. A manual bookmark remains a human request, not a model alert.

The review page starts with a takeaway and reviewer state, followed by large synchronized original and derived players, timed observations, and reviewer actions. **Enlarge original** provides a theater view. Every derivative explains its purpose and limits. The original remains the reference for decisions.

A separate `review_brief` GPU job samples up to eight original clip frames and runs base Qwen3-VL-4B without the high-recall research adapter. Constrained output supplies a takeaway, findings tied to supplied source-frame indices, missing context, whether the initial flag is supported, and a suggested reviewer step. The application derives finding times from cited frames rather than asking the model to invent timestamps. Human bookmarks have no AI alert to support or reject. Invalid responses receive at most one correction attempt; raw attempts and failed jobs remain auditable. The suggestion never changes a human decision, dispatches an alert or becomes a training label. This uses the same model family as the live verifier and is not independent confirmation. Sparse sampling can miss actions. It runs before heavyweight enhancement when live analysis is idle; footage can already be reviewed while it waits.

BVI-Mamba now follows its official BGR and [-1,1] input/output convention. The previous RGB/[0,1] adapter was incorrect. Aspect-preserving letterboxing avoids stretching portrait footage. A lighting gate preserves already well-lit frames, and a highlight-clipping guard conservatively blends a candidate. These checks do not prove perceptual improvement. Superseded artifacts remain in transformation history; originals are never overwritten.

ReLift's raw output is dense scene-point motion, not a human body mesh. The new export retains thousands of RGB-colored points, hides invalid or unobserved points, uses the frame-0 camera convention correctly, and supplies a video reprojection so reviewers can understand which scene points they are seeing. The optional orbit view supports camera reset and clip-time scrubbing. New inference jobs sample across the complete incident window instead of only taking its first 84 frames. Re-exporting an existing NPZ is a CPU job and does not rerun or improve the underlying geometry model.

`pytest` covers slow-viewer backpressure, recording isolation, native observation logging, BVI pixel conventions, colored trajectory export and source timing, and clip-review evidence bounds. GPU/runtime and physical-phone measurements must be reported separately; a successful protocol test is not a detection-accuracy benchmark.

Measured on the existing A40, November 14, 2026: the separate eight-frame review of the 2.8-second upstream cartwheel engineering clip took 43.18 seconds including process startup/model loading, with 13.61 seconds in the review routine and 9,661 MiB sampled total device peak memory. It suggested no clear visible basis for follow-up and identified the trigger as a human bookmark. The original SHA-256 remained `e2443a55504e3e3974b0143fbe00fe31845f34debcd9decbf2516c1bf67ebd1e`. Earlier schema/time-alignment failures are retained in job history; one successful benign clip does not establish review accuracy.

The cached ReLift export produced 3,948 colored points and a source-aligned motion video without rerunning inference. Tracker observation coverage fell from 100% to 48% across this clip; that mask is not a geometry-accuracy score. BVI correctly skipped all 84 already well-lit frames. A separate corrected-checkpoint smoke test on an artificially darkened public image ran in 30.88 seconds at 1,186 MiB peak allocated VRAM; it is not a perceptual-quality benchmark.

After correcting native round boundaries, a 45-second direct-Runpod public-image probe produced three complete MOSS scene sentences, received at stream seconds 11.9, 23.5 and 40.4. Partial/restarted captions are withheld rather than concatenated. This demonstrates actual model-to-chat plumbing, not action-detection accuracy. Model response lag is materially longer than the live preview's network delay. The separate 80-second Replit-path preview measurement was 2.96 FPS, 491 ms median sender-to-preview time and 1,450 ms p95; browser paint and a physical phone were not included in that reproducible protocol measurement.

## Automatic event acceptance

The current profile keeps four source images per primary verification to match the stronger trained adapter. Real motion transitions guide image selection, and a second pass uses other original support frames. Motion scores never classify an action. New native MOSS work yields while Qwen verifies; both models remain resident, and incoming camera frames continue. Raw model-generated decimal timestamps are retained in audit output, but actual cited source-frame times define the evidence interval. Temporal agreement uses source event times rather than the variable completion times of GPU calls. Identical JPEGs with different frame numbers cannot count as distinct real visual evidence.

The rolling buffer allows up to 60 seconds of model lag, bounded by its 128 MiB memory cap. A threshold-passing, corroborated observation creates a red live/chat notice with its action, score, configured threshold and recording state. Notices remain in the source history, and saved notices link to the actual incident. Originals are saved with ten seconds of lead-up and ten seconds of follow-through for contact, or thirty seconds of follow-through for object-taking so a reviewer can see slower removal/departure context. Clips remain bounded by the available stream start/end and buffer capacity. A short upload receives a final retained-context pass instead of dropping its tail.

[Actual diagnostic results](../../evaluation/event-live-diagnostic.json): full previously evaluated academic videos were replayed through the real phone protocol, using the selected research adapter, with no manual bookmark or simulated detector. Contact produced an automatic 19.849-second original; the final object-taking check produced a 39.004-second original. Both originals included their annotated activity, were downloadable, and matched stored SHA-256 values. Replit displayed the red notices, 0.80 score / 0.75 threshold and incident-review links. The diagnostic stream requested original-first review, so the separate Qwen clip job ran while optional RelayVSR/ReLift jobs were not repeated for these test clips; normal phone sources still request those copies by default. FastLLVE remained enabled for dark input.

This was not perfect detection: one of two ordinary test videos also produced a review alert. The successful notices arrived approximately 27 and 37 seconds after their last cited frames. This reused diagnostic is neither an independent accuracy estimate nor a physical-phone evaluation. Initial failures and clipped follow-through were preserved and fixed rather than counted as success. The two newly trained follow-ups were weaker, so the previously owner-approved research weights remain selected. A fresh phone angle, lighting and staged gesture still need their own test. Leave capture running for at least 45 seconds after a staged action; act without contact and keep all participants visible. Never treat absence of an alert as evidence that nothing happened.

## Observation latency and short-upload corrections

The November 14 follow-up separates capture completion from analysis completion.
When camera input ends, the UI shows that received frames are still being
processed instead of continuing to say it is listening. The circular indicator
uses actual model phases and the verifier pass start time; it does not imply a
percentage or a completed decision.

The Qwen constrained decoder now caches its tokenizer vocabulary during model
warmup. Evidence schemas, parser state and valid frame-ID constraints remain
fresh for each pass. On the same A40 and approved adapter, one warm synthetic
ordinary-activity check measured 5.25 seconds before caching and 1.62 seconds
afterward; approximately 3.74 seconds was repeated CPU vocabulary setup. These
measurements do not establish the same reduction for positive events or the
complete two-pass alert path. Details are in
[`evaluation/verifier-latency-benchmark.json`](../../evaluation/verifier-latency-benchmark.json).

The streaming adapter preserves all validated completed sentences and exact
completed-round text, and gives a fresh in-flight caption a bounded opportunity
to finish when capture ends. It does not turn unfinished fragments into facts.
The next check retains the original context since the preceding dispatch, within
the existing memory/lag bounds, rather than discarding everything older than ten
seconds. A final upload check uses the actual retained stream end and includes
its original endpoint, preventing an early motion peak from suppressing review
of the tail. Four-frame sparse sampling can still miss actions.

Uploaded phone videos are analyzed in their container display orientation;
source bytes and their hashes remain unchanged. Review styling is neutral slate
with amber follow-up emphasis. Previously recorded low-frame-rate evidence is
not relabeled or interpolated into high-frame-rate original footage.

Native encoded recording is experimental and disabled by default until its full
browser-to-incident test passes. `ENABLE_NATIVE_RECORDING=false` preserves the
existing sampled-evidence path while the live WebRTC stream remains independent.
A standalone 60 FPS recorder result alone is insufficient to enable this path.
