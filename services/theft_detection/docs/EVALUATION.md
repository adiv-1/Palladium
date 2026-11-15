# Evaluation

**Read this first.** Two kinds of evidence exist, and they answer different questions:

| Evidence | Detector | What it shows | What it does **not** show |
|---|---|---|---|
| Scripted traces (5,500+ runs) | Noise model, not a neural network | Whether the tracking + temporal logic separates staged-style positives from 16 kinds of ordinary interaction, under controlled noise | Real-world accuracy of anything |
| Real footage (9 clips, 8 min) | Real YOLOX on real video | That the real pipeline runs end to end, stays silent on ordinary activity, and where it fails | Precision or recall with any statistical meaning |

There is **no validated real-world detection rate**. Producing one needs consented staged
recordings in campus-like settings plus hours of ordinary footage (see *Next steps*).

## Update 2026-11-15 (later) — action route: classify the act, not the object

**Why.** The bag could not be detected during the real grab under any setting, and "bag" is only
one kind of object. Body keypoints on the same clip (RTMPose-t, ~8 ms per person) showed the act
itself clearly: hands joined, both arms stretched, torsos pulling apart, then one person leaving.
The action route (see [ARCHITECTURE.md](ARCHITECTURE.md#the-action-route-object-agnostic)) uses
only that.

### Real footage (real YOLOX-Tiny + real RTMPose-t, 10 FPS; `benchmarks/results/action_videos.json`)

| Clip | Before | With the action route |
|---|---|---|
| Mixkit 31359 staged handbag snatch | info event only | **`possible_snatching` review alert**, 5.9 s (contact from 3.2 s to 5.2 s; alert 0.7 s after the hands parted). Evidence: 1.8 s of contact, 11 frames of both arms stretched towards each other while the torsos moved 0.72 body heights apart, the other person kept reaching for 3 frames afterwards |
| Mixkit 31395 phone taken at night | nothing | nothing — still missed (dark, brief, one hand barely visible) |
| Mixkit 31354 phone lifted from an open bag | nothing | nothing — still missed (no hand contact between the two people) |
| 6 ordinary-activity clips (7.6 min) | 0 alerts | **0 alerts, 0 info events**; 14 real hand contacts were examined and dismissed, including a 3 s mutual-reach contact (handshake-like, torsos moved 0.02 apart) |

**This is not independent evidence.** The staged clip is the only real positive and the action
rules were designed while looking at it (the tug rule in particular). The independent test is the
staged webcam trials.

### Scripted scenarios, held out (100 runs each, 27 scenarios, mock detections and mock keypoints)

New scenarios, defined before results: P07 grab of an object the detector never sees, then run
(positive); N19 handshake then one walks away; N21 brief tap then walks on (negatives).
Wrist keypoints are missed 10 % of the time and jittered by 2 % of body height.

| | Object route only | Object + action routes |
|---|---|---|
| P07 undetectable object grabbed | 0 / 100 | **100 / 100** |
| P01 run-by snatch | 93 | **100** |
| P02 walk up and pull away | 91 | **99** |
| P06 bag hidden during the grab | 92 (alert ≈ 2.4 s) | **100** (≈ 2.0 s) |
| P03, P04, P05 | 100 each | 100 each |
| False review alerts, 20 negative scenarios (7.3 h) | 0 | **0** |
| Overall recall | 0.823 | **0.999** |
| Time to detection median / p90 | 1.11 / 2.23 s | 1.12 / 1.95 s |

Robustness with both routes (25 runs per scenario per point; snatch = P01+P02+P06+P07, 100 events):

| Condition | Snatch detected | False review alerts |
|---|---|---|
| Bag detections missed 0 / 10 / 25 / 40 / 55 % | 100 / 100 / 100 / 100 / 99 | 0 / 0 / 1 / 0 / 2 |
| Analysis rate 3 / 5 / 8 / 10 / 15 FPS | 55 / 94 / 99 / 100 / 99 | 0 / 0 / 0 / 0 / 1 |
| People missed 2 / 10 / 20 / 35 % | 100 / 100 / 95 / 77 | 0 / 1 / 0 / 0 |
| Box jitter 0 / 3 / 6 / 10 % | 100 / 100 / 99 / 98 | 1 / 0 / 3 / **9** |

The action route makes snatch detection almost independent of bag detection (the earlier
dominant failure), at the price of depending on *person* detection and keypoints: recall falls
when people are missed 35 % of the time, and heavy jitter now produces false alerts in five
scenarios instead of one.

### The look-alike it cannot separate

X01, a game of tag (touch, sprint off, the other often chases; nothing is taken):
**100 of 100 runs raise `possible_snatching`.** The motion is the same as a grab-and-run. This
is inherent to judging the act without seeing an object, it is stated in every such event's
uncertainties, and it is why these are review candidates and not findings. Expect the same from
playful grabs between friends.

### Cost (real clips, real-time pacing, 25 s each)

| Scene | Frame total mean / p95 | Capture → result p95 | Pose | CPU | Peak memory | Dropped |
|---|---|---|---|---|---|---|
| Store aisle, 3–5 people close together, pose on | 52.5 / 75.5 ms | 73 ms | 22.5 ms mean, 30.6 p95, on 133 of 251 frames | 22 % | 204 MB | 0 |
| Same, pose off | 36.8 / 41.5 ms | 41 ms | — | 23 % | 204 MB | 0 |
| Two people, pose on | 43.2 / 67.8 ms | 68 ms | 18.6 ms mean on 49 of 251 frames | 22 % | 204 MB | 0 |

10 analysed FPS is held in all three. Pose runs only when people are within 1.8 body heights of
each other and costs nothing otherwise.

## Update 2026-11-15 — hidden exchanges, possession checks, tracker fix

**Why.** Frame-by-frame analysis of the missed staged snatch (Mixkit 31359, every 24 fps frame,
3.4–5.1 s) showed the bag is effectively invisible during the grab under every detector setting
tried (bag score ≥ 0.30 in: 0 % full-frame YOLOX-Tiny, 0 % YOLOX-S, 0 % Tiny on a crop around
the two people, 7 % S on the crop). Image sharpness did not drop: it is occlusion and deformation
between two bodies, not blur. A bigger detector, cropping or a lower threshold does not fix that.
The trace also showed the verifier had only 0.13 s to decide, because the 2 s window started at
the grab while the bag only reappeared 1.87 s later.

**What changed** (each switchable for A/B, `benchmarks/ab_previous.yaml` restores the old behaviour):

1. *Hidden exchange → possession before/after.* If the bag was out of sight ≥ 0.4 s while the two
   people were in contact, the verifier waits (≤ 5 s) for them to separate, then looks at the
   prior holder for 8 frames. A bag detected with them → dismissed. None → possession changed:
   `possible_snatching` only with a fast departure (≥ 1.3 body heights/s), otherwise an
   info-level `uncertain_interaction`. The vanished-bag path uses the same test.
2. *Veto evidence needs score ≥ 0.30* (the tracker's bar for an observation). Found on the real
   clip: a 0.25 "handbag" on the holder's jeans had cancelled the candidate.
3. *Tracker:* a bag unseen for > 0.3 s may only be re-linked far away if the detection is next to a
   person. Found in traces: one spurious detection captured a hidden bag's track.

**Scenarios added for this, defined before results were seen:** P06 snatch with the bag hidden
0.8–2 s during the grab (positive); N17 hidden hand-over then the receiver walks away; N18 hidden
hand-over, both stay (negatives).

### A/B on held-out seeds (100 runs per scenario, 21 scenarios, mock detections)

| | Previous | New |
|---|---|---|
| P06 hidden-grab snatch | 25 / 100 | **92 / 100** |
| P01 run-by snatch | 91 | 93 |
| P02, P03, P04, P05 | 91, 100, 100, 100 | unchanged |
| False review alerts, 18 negative scenarios (6.58 h) | 0 | **0** |
| Overall recall | 0.845 | **0.960** |
| Time to detection median / p90 | 1.07 / 1.77 s | 1.11 / 2.23 s |
| Info-level events | 25 | 107 (93 are N17 hidden hand-overs, by design) |

P06 alerts come ≈ 2.4 s after onset (waiting for separation plus 8 frames of the holder).

### A/B under bag-detection misses (25 runs per scenario per point)

| Bag detections missed | Snatch P01+P02+P06 previous | new | False review alerts previous | new |
|---|---|---|---|---|
| 0 % | 62 / 75 | **72** | 0 | 0 |
| 10 % | 52 | **67** | 0 | 0 |
| 25 % | 45 | **63** | 1 (N17) | 1 (N17) |
| 40 % | 35 | **62** | 3 (N10 ×2, N17) | 3 (same) |
| 55 % | 14 | **46** | 0 | **1 (N07) — regression** |

### Real footage (YOLOX-Tiny / YOLOX-S, 10 FPS)

| Clip | Previous | New |
|---|---|---|
| Mixkit 31359 staged handbag snatch | missed (`gradual transfer`) | **still not an alert** — now an info-level `uncertain_interaction`: "left person_1 during contact, then seen with person_3; exchange not visible; person_3 left at walking pace". Possession check: 8 frames of the holder, 0 with a bag. The measured departure speed was 0.7 body heights/s (< 1.3): the runner is visible only briefly, partly out of frame, while the camera pans. YOLOX-S: still no candidate. |
| 2 phone clips | nothing (unsupported) | nothing |
| 6 ordinary-activity clips (7.6 min) | 0 alerts | 0 alerts, 0 info events |

**Honesty note:** this clip was used to find fixes 2 and the timing problem, so it is no longer an
independent test. Real-video recall remains unvalidated; the staged webcam trials in
[LIVE_TESTING.md](LIVE_TESTING.md) are the next evidence, and their log is designed for exactly
this scoring.

## Provisional MVP acceptance targets

Set before results were final; adjusted only where noted.

| Target | Scripted traces (held out) | Real footage |
|---|---|---|
| False review alerts on ordinary interactions: 0 in ≥ 5 h of negative traces | **met** — 0 in 5.9 h | 0 in 7.6 min (far too little to claim a rate) |
| Snatch recall ≥ 85 % at 10 FPS with 10 % bag misses | **met** — 91 % (P01), 91 % (P02) | **not met** — 0 of 1 |
| Removal / zone recall ≥ 95 % | **met** — 100 % | not tested on real footage |
| Alert within 2 s of onset (p90) | **met** — 1.65 s overall; snatch median 0.8 s | — |
| Holds 10 analysed FPS on CPU, bounded latency | **met** — [PERFORMANCE.md](PERFORMANCE.md) | **met** |

## Scripted traces

Each scenario is randomised from a seed (subject size and position, speeds, timing, mirroring)
and passed through the **production tracker, engine, verifier and de-duplication**; only the
detector is replaced by a noise model (2 % person misses, 10 % bag misses plus 15 % extra while
a bag moves fast, 3 % box jitter, 2 % spurious one-frame bag detections, occlusion by people in
front). Analysis at 10 FPS. Scoring: a label is detected by an alert of the same type raised
between 0.75 s before and 6 s after onset; every other `review` alert is a false alert.
Info-level events are not alerts and are counted separately.

Thresholds were tuned while looking at seeds 1000+. The **held-out** run uses seeds 90000+,
which were never looked at during tuning. Same generator, so held-out means unseen
randomisation, not unseen kinds of behaviour.

### Held-out results (100 runs per scenario; `benchmarks/results/traces_heldout.json`)

| Scenario | Runs | Detected | Missed | False review alerts | Info events | Median delay |
|---|---|---|---|---|---|---|
| N01 hand a backpack to a friend | 100 | — | — | 0 | 0 | — |
| N02 pick up own bag | 100 | — | — | 0 | 0 | — |
| N03 adjust backpack strap | 100 | — | — | 0 | 0 | — |
| N04 exchange belongings | 100 | — | — | 0 | 0 | — |
| N05 help carry luggage | 100 | — | — | 0 | 0 | — |
| N06 move bag to another seat | 100 | — | — | 0 | 0 | — |
| N07 crowd brushing past | 100 | — | — | 0 | 0 | — |
| N08 object hidden by passer-by | 100 | — | — | 0 | 1 | — |
| N09 dropped item picked up and returned | 100 | — | — | 0 | 0 | — |
| N10 run with own bag past a bystander | 100 | — | — | 0 | 0 | — |
| N11 camera shake | 100 | — | — | 0 | 0 | — |
| N12 collect bag, no access rule configured | 100 | — | — | 0 | 0 | — |
| N13 two walkers cross | 100 | — | — | 0 | 0 | — |
| N14 stop at a restricted zone's edge | 100 | — | — | 0 | 0 | — |
| N15 owner returns from out of view | 100 | — | — | 0 | 0 | — |
| N16 passed closely, then runs off with own bag | 100 | — | — | 0 | 0 | — |
| **P01 run-by snatch** | 100 | 91 | 9 | 0 | 8 | 0.80 s |
| **P02 walk up and pull away** | 100 | 91 | 9 | 0 | 2 | 0.80 s |
| **P03 unattended bag taken, depositor in view** | 100 | 100 | 0 | 0 | 0 | 1.21 s |
| **P04 removal from monitored zone** | 100 | 100 | 0 | 0 | 0 | 1.65 s |
| **P05 restricted zone entry** | 100 | 100 | 0 | 0 | 0 | 1.05 s |

N01–N12 are the twelve false-positive situations listed in the project brief, in order.

| | Held out (seeds 90000+) | Tuning seeds (1000+, 60 per scenario) |
|---|---|---|
| Precision | 1.000 (482 / 482) | 1.000 (286 / 286) |
| Recall | 0.964 (482 / 500) | 0.953 (286 / 300) |
| False review alerts in negative traces | 0 in 5.92 h | 0 in 3.61 h |
| Time to detection, median / p90 | 1.06 s / 1.65 s | 1.08 s / 1.59 s |

Zero false alerts in 5.9 h bounds the rate at about 0.5 per hour (95 %, rule of three) **for
this scenario generator**. That is a statement about the logic, not about a campus.

Time to detection includes the verification window by design: a snatch alert is raised after
the bag has visibly left (0.5–0.8 s); removals wait until the object has been carried a body
height; zone entries wait for a 1 s dwell.

### Does stage 2 pay for itself?

Same held-out runs, with every stage-1 candidate emitted unverified:

| | Stage 1 only | Stage 1 + stage 2 |
|---|---|---|
| False review alerts per hour of negative traces | **180** | **0** |
| Precision | 0.32 | 1.00 |
| Recall | 0.984 | 0.964 |
| Median time to detection | 0.49 s | 1.06 s |

Verification removes all 1,065 false candidates for a loss of 10 true ones and about 0.6 s of
delay. Most dismissals: `gradual_transfer` (403), `prior_holder_still_has_an_object` (203),
`no_other_handler_in_view_and_not_a_monitored_zone` (200),
`picked_up_by_a_track_that_handled_it_before` (149), `camera_motion` (58).

### Robustness sweeps (25 runs per scenario, held-out seeds, 1.49 h of negative traces per point)

Snatch recall is P01 + P02 combined (50 events per point). One parameter varies at a time.

#### Analysis rate

| FPS | Snatch recall | Snatch median delay | False alerts | All-event recall |
|---|---|---|---|---|
| 3 | 0.64 | 1.24–1.32 s | 0 | 0.856 |
| 5 | 0.84 | 1.08–1.09 s | 0 | 0.936 |
| 8 | 0.90 | 0.84–0.87 s | 1 (N07) | 0.960 |
| **10** (default) | 0.88 | 0.80–0.81 s | 0 | 0.952 |
| 15 | 0.92 | 0.65–0.67 s | 0 | 0.968 |

Below 5 FPS a run-by grab is often only one or two frames long and recall falls sharply.
8–15 FPS perform alike; 10 FPS is the default because it costs 28 % of this laptop's CPU
([PERFORMANCE.md](PERFORMANCE.md)). Do not run below 8 FPS when snatching matters.

#### Bag detection reliability (the dominant factor)

| Bag detections missed | Snatch recall | False alerts |
|---|---|---|
| 0 % | 0.98 | 0 |
| 10 % (default noise) | 0.88 | 0 |
| 25 % | 0.82 | 0 |
| 40 % | 0.64 | 2 (N10) |
| 55 % | 0.28 | 0 |

At 40 % misses two similar bags passing each other can swap track identities and look like a
transfer. A verifier check that looks for a bag still detected with the prior holder in the raw
detections was added for this case. On 40 runs each of 8 related scenarios at 40 % misses it
halved false alerts (6 → 3) for 1 lost detection in 80 and +0.17 s median delay; waiting
longer (6 or 8 frames instead of 4) removed at most two more false alerts and cost 6–15
detections, so 4 frames was kept. The remaining cases are those where the bystander's bag is
not detected at all for ~0.7 s.

#### Other noise

| Parameter | Values | Snatch recall | False alerts |
|---|---|---|---|
| Whole frames lost | 0 / 10 / 25 / 40 % | 0.88 / 0.88 / 0.88 / 0.86 | 0 / 0 / 0 / 1 (N16) |
| Box jitter (σ, share of box size) | 0 / 3 / 6 / 10 % | 0.90 / 0.88 / 0.88 / 0.90 | 0 / 0 / 2 / 4 (all N16) |
| People missed | 2 / 10 / 20 / 35 % | 0.88 / 0.86 / 0.92 / 0.90 | 0 / 0 / 0 / 0 |

Frame loss and missed people are absorbed by the tracker's coasting. Heavy box jitter is the
one condition that produces repeated false alerts, all in N16 (a bag holder is passed at close
range and immediately runs off with their own bag): noisy boxes swap the two *person* tracks.
It is listed in [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md).

## Real footage

`benchmarks/eval_videos.py evaluation/annotations.json` — real decoding, real YOLOX, real
tracker and engine at 10 FPS. Results in `benchmarks/results/videos_*.json`; SafeSC-format
predictions are written next to them so `scripts/evaluate.py` in the main repository can
score the same output.

| Clip | Content | Label | YOLOX-Tiny | YOLOX-S |
|---|---|---|---|---|
| Mixkit 31359 | Staged: runner pulls a handbag from a standing person; handheld, panning camera | `possible_snatching` (supported) | **missed** — candidate opened, dismissed as `gradual_transfer` | **missed** — no candidate (the bag track was lost during the grab) |
| Mixkit 31395 | Staged: phone taken from a hand at night | phone — unsupported | no event (expected) | no event (expected) |
| Mixkit 31354 | Staged: phone lifted out of an open bag | phone — unsupported | no event (expected) | no event (expected) |
| Intel `classroom` | Students at desks, 33 s | none | no event | no event |
| Intel `face-demographics-walking-and-pause` | People walking and pausing, 91 s | none | no event | no event |
| Intel `one-by-one-person-detection` | People entering one at a time, 139 s | none | no event | no event |
| Intel `people-detection` | People crossing a room, 50 s | none | no event | no event |
| Intel `store-aisle-detection` | Shoppers in an aisle, one handbag tracked, 65 s | none | no event | no event |
| Intel `worker-zone-detection` | Workers on a floor, one handbag tracked, 76 s | none | no event | no event |

Summary with YOLOX-Tiny: **0 of 1** supported staged event detected; **0 false alerts in
454 s** of ordinary footage; 2 unsupported (phone) events correctly produce nothing.

Note on YOLOX-S: the first run hit the machine's memory limit (see
[PERFORMANCE.md](PERFORMANCE.md#memory-headroom-and-a-fix-it-led-to)) on two clips; the
pipeline reported `status: error` with the `MemoryError`, as intended. The table shows the
re-run, which completed on all nine clips. YOLOX-S therefore does not rescue the staged
snatch either, and is too slow for continuous CPU use.

### Why the staged snatch was missed

Inspection of the clip and the detector output (YOLOX-Tiny, 10 FPS, final code):

1. Before the grab the handbag is detected in every analysed frame from 0.1 s to 3.3 s
   (score 0.12–0.76; YOLOX-S scored it 0.65–0.86 in sampled frames).
2. From 3.4 s, as the runner reaches it, the bag is between two bodies and is **not detected
   again until 5.1 s**. Over the verification window it was observed in 26 % of frames; the
   speed measured from those few observations is 1.0 body heights/s, below the 1.3 needed, and
   the object ended only 0.7 body heights from the prior holder.
3. The handheld camera pans to follow the action; it was flagged unstable in 15 % of the
   window — below the 30 % that blocks a decision, so camera motion was not the reason.

Stage 2 therefore saw a slow, poorly observed object and dismissed it — the conservative rule
doing what it was designed to do, at the cost of a miss. The same signature (bag lost exactly
during the grab) is visible in the bag-miss sweep above. Real-world snatch recall will be
limited first by bag detection during body contact, not by the temporal logic.

No threshold was changed to make this clip pass: tuning to the only real positive would
produce a number with no meaning.

### Detector robustness on real footage

`benchmarks/bench_robustness.py` — YOLOX-Tiny on 786 sampled frames of five real clips:
detections (people ≥ 0.45, bags ≥ 0.30) on the original frame that are still found
(IoU ≥ 0.5) after degrading it. A proxy for robustness, not accuracy.

| Condition | People kept (of 1,465) | Bags kept (of 23) |
|---|---|---|
| Brightness × 0.5 | 99 % | 91 % |
| Brightness × 0.25 | 97 % | 65 % |
| Brightness × 0.1 + sensor noise | 59 % | 0 % |
| JPEG quality 20 | 97 % | 65 % |
| JPEG quality 8 | 85 % | 9 % |
| Half resolution | 99 % | 87 % |
| Quarter resolution | 97 % | 74 % |
| Sensor noise σ = 25 | 91 % | 39 % |
| Horizontal motion blur 15 px | 89 % | 48 % |

People survive most degradations; bags do not. With only 23 reference bag detections the bag
column is rough. Combined with the bag-miss sweep this means: **in low light, heavy
compression or fast motion, expect snatch recall to collapse**, while false alerts stay low
because the verifier requires the object to be observed.

## Not evaluated

Crowd density beyond ~7 people, camera height and angle, outdoor lighting changes, rain,
long-term operation, more than one real positive event, real RTSP cameras.

## Next steps toward a meaningful number

1. Record consented, staged clips on campus-like locations: at least 30 snatch / removal
   events across 3+ camera angles, day and night, plus 30 matched hand-overs, pick-ups and
   crowd passes. Label them in `evaluation/annotations.json` format.
2. Record or obtain several hours of ordinary footage from the intended camera positions to
   estimate false alerts per hour.
3. Improve bag detection under contact: fine-tune the detector on those clips, or try
   RF-DETR / D-FINE Nano through the same adapter (both Apache-2.0).
4. Re-run `eval_videos.py`, then calibrate `evidence_score` against reviewer decisions.
