# Model and stack selection

Two different questions, kept separate:

1. **Object detection accuracy**: does the model find people and bags in a frame? Published
   COCO mAP answers this, roughly.
2. **Event detection accuracy**: does the system alert on a snatch and stay quiet on a hand-over?
   No detector benchmark answers this. It depends on tracking, temporal logic and verification,
   and is measured separately ([EVALUATION.md](EVALUATION.md)).

## Decision

| | Choice | Licence |
|---|---|---|
| **Primary detector** | YOLOX-Tiny, 416×416, ONNX | Apache-2.0 (code and released weights) |
| **Runtime** | ONNX Runtime, CPU execution provider, thread-capped | MIT |
| **Tracker** | ByteTrack association scheme, re-implemented in NumPy | Original method/code MIT; this implementation is part of the module |
| **Temporal reasoning** | Explainable two-stage state machine over tracks | — |
| **Fallback (low power)** | YOLOX-Nano at the same settings (one config line) | Apache-2.0 |
| **Fallback (no model at all)** | Rule engine on mock detections / synthetic scenes; also accepts detections from any other model through `process_detections()` | — |

## Measured on the development laptop

AMD Ryzen 5 5600H (6 cores / 12 threads), 31 GB RAM, Windows 11, ONNX Runtime 1.32 on CPU,
4 intra-op threads, 1080p classroom clip downscaled to 960 px, real-time pacing at 10 analysed
FPS. Full tables: [PERFORMANCE.md](PERFORMANCE.md).

| Model | COCO mAP (published) | Detector mean / p95 | Keeps 10 FPS? | Max FPS (fast mode) | Peak RSS |
|---|---|---|---|---|---|
| YOLOX-Nano | 25.8 | 12.4 / 14.6 ms | yes | 50.0 | 231 MB |
| **YOLOX-Tiny** | 32.8 | 38.2 / 43.2 ms | yes | 21.6 | 253 MB |
| YOLOX-S | 40.5 | 154.4 / 168.5 ms | **no** (6.3 FPS, 89 stale frames dropped) | 6.1 | 314 MB |

Why Tiny: it is the largest model that holds the 10 FPS analysis rate on this CPU with room to
spare (28% of the machine), and bags are its weakest class, so its extra accuracy over Nano
matters. On the staged snatch clip, before the grab, YOLOX-S scored the handbag 0.65–0.86 in
sampled frames where Tiny scored 0.12–0.76 (see [EVALUATION.md](EVALUATION.md)), so S is the candidate for a GPU or for
candidate-only re-checking, not for continuous CPU monitoring.

## Licensing — why not Ultralytics YOLO

The existing SafeSC tracking path uses Ultralytics YOLO11n (`ml/adapters.py`). Ultralytics
offers its models under **AGPL-3.0 or a paid Enterprise licence**. Ultralytics' own licence
page states that AGPL compliance means publishing the full source of the derivative work,
including the larger application, and that projects not wishing to open-source need an
Enterprise licence; it also states that models trained with its code are covered by AGPL-3.0
by default (a claim disputed in community discussion, but it is the licensor's position).
AGPL's network clause means serving users over a network can trigger the obligation without
distributing binaries.

SafeSC has not chosen a licence yet, and a university safety tool may well end up proprietary
or vendor-hosted. Choosing an Apache-2.0 detector here removes that question for this module
instead of deferring it. Nothing in this module imports `ultralytics`.

Sources checked (November 2026): ultralytics.com/license; ultralytics.com/legal/agpl-3-0-software-license;
github.com/Megvii-BaseDetection/YOLOX (LICENSE: Apache-2.0); github.com/microsoft/onnxruntime
(LICENSE: MIT); github.com/ifzhang/ByteTrack (LICENSE: MIT); github.com/roboflow/supervision
(MIT); github.com/openvinotoolkit/openvino (Apache-2.0); OpenCV 4.x (Apache-2.0);
RF-DETR and D-FINE (Apache-2.0 per their repositories).

This is an engineering summary, not legal advice. Before distribution, have the final
dependency list reviewed.

## Candidates considered

| Candidate | Why not chosen (for now) |
|---|---|
| Ultralytics YOLO11n / YOLOv8n | Excellent tooling and accuracy per FLOP; AGPL/Enterprise licensing (above). |
| YOLOX-S / M | Better bag recall, but S already misses the 10 FPS budget on this CPU (measured). |
| RF-DETR Nano, D-FINE Nano | Apache-2.0 and markedly higher published COCO AP (≈48 and ≈43) than YOLOX-Tiny; published latencies are T4/TensorRT only. No CPU/ONNX numbers were found and none were measured here. **Strongest upgrade path** — benchmark them through the same `Detector` adapter. |
| MobileNet-SSD / NanoDet | Permissive and fast; lower accuracy on small objects such as bags than YOLOX-Tiny at similar cost. |
| OpenVINO runtime | Apache-2.0 and typically faster on Intel CPUs; this laptop is AMD, and ONNX Runtime already meets the budget. Swapping the execution provider is a config change. |
| GPU execution | The laptop has a GTX 1650, but this ONNX Runtime build only offers CPU (and Azure) providers. Not needed for one stream at 10 FPS. |
| DeepSORT / BoT-SORT with ReID | Appearance embeddings re-identify people; deliberately excluded ([PRIVACY.md](PRIVACY.md)). |
| `supervision.ByteTrack` | MIT and convenient, but it assumes a fixed frame rate, associates all classes together and uses IoU only. Bags grabbed at 10 FPS move several box widths between frames (IoU = 0), which this implementation handles with a motion gate. |
| Pose estimation (wrist–object contact) | Would sharpen "who touched it"; adds a second model per person per frame. Left as a candidate-only stage-2 upgrade. |
| Action recognition (X3D, MoViNet, SlowFast) | Needs labelled snatch clips to fine-tune; Kinetics has no snatching class; runs at clip level. Revisit once staged footage exists. |
| Dense optical flow | Costs as much as the detector at full resolution. Only the global (camera) motion is estimated, by phase correlation on a 160-px thumbnail (< 1 ms). |
| Vision-language model on every frame | Explicitly out of scope; seconds per frame. SafeSC's Qwen verifier could be triggered *by* this module's candidates instead. |

## Pose model (action route)

| | Choice | Licence |
|---|---|---|
| Body keypoints | RTMPose-t, 256×192, official ONNX export from MMPose (`rtmpose_t`, SHA-256 pinned) | Code and released weights Apache-2.0 |

Chosen because it is top-down (runs only on the people that matter), small (13 MB), fast on CPU
(measured 8 ms per person, 16–39 ms per frame when people are close together) and permissively
licensed. Not chosen: Ultralytics YOLO-pose (AGPL, as above); MediaPipe Pose (single person);
MoveNet MultiPose (needs TFLite conversion; lower accuracy on partly hidden arms); OpenPose
(non-commercial licence). `rtmpose_s` is registered as a drop-in for more accuracy.

Caveat: the weights were trained by the MMPose authors on several public pose datasets
("body7"). The weights are published under Apache-2.0, but some of those datasets carry their
own terms; have this reviewed before commercial distribution.

## Supported objects

The **action route needs no object class at all**: it works from the two people's arms.

The object route tracks `backpack`, `handbag`, `suitcase`, `laptop`, `cell phone`, `book` and
`umbrella` (COCO classes) as one "carried object" category, because the detector often switches
labels for the same object. Small items (phones, wallets, keys) are a few pixels across at camera
distance and are rarely detected; when they are not, only the action route can report the event,
and it then says that the object was not identified.
