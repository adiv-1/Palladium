# Live webcam testing

A repeatable procedure for staged, consented trials with a laptop webcam, two participants and
one bag. The system reports observed object movements for human review; it does not, and cannot,
decide intent.

## Start

```bash
cd services/theft_detection
uv run theft-detect cameras                 # which camera indices open and deliver frames
uv run theft-detect live --camera 0 --open  # review page at http://127.0.0.1:8077/
```

Options: `--fps 10` analysis rate (do not go below 8 for snatch tests), `--width 1280 --height 720`
capture size (the built-in HP camera defaults to 640×480), `--model yolox_nano` on slower laptops,
`--record-evidence` to keep a short clip around each event (off by default), `--port` if 8077 is
taken, `--token` when SafeSC connects (see [INTEGRATION.md](INTEGRATION.md)).

Stop with **Ctrl+C**; the camera is released. Nothing is recorded unless `--record-evidence` is
given, and the server listens on 127.0.0.1 only.

### If the camera does not open

The page shows `status: error` with the reason; the system never substitutes another video.

1. `uv run theft-detect cameras` — lists indices that actually deliver frames.
2. Windows: *Settings → Privacy & security → Camera* — "Camera access" and "Let desktop apps
   access your camera" must be on.
3. Close other apps using the camera (Teams, Zoom, the Camera app, browser tabs).
4. A privacy shutter or function-key camera switch shows up as a black image, not an error:
   check the brightness of the live feed.

## Set-up

* Light the scene from the front; avoid a bright window behind the participants.
* Put the camera 3–5 m away, roughly chest height, so both people are fully in view and the bag
  is at least ~40 px tall in the 640×480 image. Most webcams are wide-angle: closer is better
  than further for the bag.
* Any object works for the snatch trials (phone, wallet, book, bag): the action route judges the
  arms and bodies, not the object. Keep both people's **hands and arms visible to the camera** -
  side-on to the camera is best; a back turned to it hides the hands. For the pick-up and
  unattended-bag trials use a clearly visible backpack, handbag or suitcase.
* Keep the laptop still. Camera movement holds decisions back on purpose.
* Both participants agree to be filmed; no one else is in view. No physical struggle: the
  "snatch" is a quick lift-and-walk, and the holder lets go.

## The trials

For each trial: pick the scenario in **Staged trials**, press **Start trial**, act it out, wait
~3 s after the action ends, press **Stop trial**. The result row and the JSON log
(`data/live_trials.jsonl`, metadata only) record:

| Field | Meaning |
|---|---|
| Result | `detected` / `missed` (expect alert), `correct` / `false alert` (expect none), `not scored` |
| Outcome | `alert`, `info only`, `candidate rejected`, `no candidate` |
| Bag seen | share of analysed frames with a bag actually detected (not predicted) |
| Delay | alert time after the observable onset |
| Why | each candidate and its verifier outcome and reason |
| JSON only | bag/person track IDs, bag detection gaps (track interruptions), features, CPU/latency at stop |

| # | Scenario | Action (A holds the bag, B is the other person) | Correct result |
|---|---|---|---|
| S1 | Normal hand-off | A hands the bag to B at normal speed; both stay or walk off slowly | no alert (`gradual transfer` rejected) |
| S2 | Pick up own bag | A puts the bag down, steps 2 m away, comes back, picks it up, leaves | no alert |
| S3 | Adjust or reposition | A swings the bag from shoulder to hand and back; moves it to a chair | no alert |
| S4 | Walk past | A and B walk past each other closely, A carrying the bag | no alert |
| S5 | Fast with own bag | A jogs through the view with their bag, passing B | no alert |
| S6 | Abrupt snatch | B walks up, takes the object from A's hand and **jogs** away; A reaches after B or follows. Also try a short tug before B leaves. Repeat with a phone | `possible_snatching` |
| S7 | Obscured transfer | B takes the bag while their bodies hide it from the camera, then walks off | either: an info event or an alert, with the "not visible" uncertainty |
| S8 | Bag hidden | A turns so the bag is behind them for 2–3 s, then turns back | no alert |

Repeat each scenario at least 5 times with small variations (side of the body, direction,
speed, distance to camera). Vary who plays A and B. Record honestly: a miss is a result.

## Reading a miss

Open the trial's JSON line:

* **Bag seen < 50 %** — detection is the limit, not the logic. Move closer, improve light, use a
  bigger or higher-contrast bag. This is the main failure seen on real footage
  ([EVALUATION.md](EVALUATION.md)).
* **`gradual transfer`** with high bag coverage — the motion was slower than the snatch thresholds
  (object ≥ 1.3 body heights/s and ≥ 1.4 body heights of separation). A slow, calm take *is*
  indistinguishable from a hand-over in boxes; that is the intended conservative behaviour.
* **`no candidate`** — the bag never became "held" (needs ~1 s with one person first) or the bag
  track broke (see `bag_detection_gaps`).
* **`camera motion`** — the laptop moved.

* **`hand contact then ordinary parting`** — the hands met but nobody fled, tugged or reacted. A calm
  take is, to the camera, a hand-over.
* **No candidate and no hand contact shown** — the wrists were not visible (red dots on the live
  view show detected wrists; yellow lines the arms).

Also try the look-alike on purpose: a tag-and-run game **will** raise an alert. That is expected
and is the reason every event goes to a reviewer.

Do not change thresholds to make one trial pass. Collect all trials first, then look at the
pattern across them.

## Live performance on the development laptop

Measured with `theft-detect live` on the built-in HP Wide Vision HD camera (640×480 at 30 fps),
YOLOX-Tiny, 4 detector threads, 25 s with one person in view: see
[PERFORMANCE.md](PERFORMANCE.md#live-webcam).
