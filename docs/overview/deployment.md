# Deployment and measured verification

## Runtime

The tested configuration uses one NVIDIA A40 with 46,068 MiB addressable VRAM, 9 vCPUs, 50 GB system RAM, and a persistent workspace. The CPU control plane, dashboard, SQLite, media storage, and GPU worker share that machine. Provider administration credentials remain on the development machine.

The authenticated application can run behind a Runpod HTTPS proxy or a configured TLS reverse proxy. The MOSS service is loopback-only on port 18001; some Runpod images reserve port 8001. Configure `PUBLIC_URL` for the actual application origin and keep access tokens in ignored local files. Do not commit an active deployment's credentials or account metadata.

Use `scripts/setup_runpod_models.sh` for the isolated model setup stages. Preserve pinned revisions in `model-lock.json`. `scripts/start_runpod.sh` starts the API. Deployment helpers depend on local metadata under `data/`, which is intentionally not distributed; inspect a helper before using it against a different account. `configure_private_worker.py` is specifically a loopback validation configuration, not a public-deployment configurator.

The default media-retention setting is 24 hours; the hackathon worker was configured for 48 hours. Reviewer metadata remains auditable. The local spend guard requires its development machine awake and online and is not a provider-enforced billing cap. Configure the authorized budget locally; stopped persistent storage can continue billing. No automatic payment-setting changes are performed.

## Actual engineering checks

- MOSS NF4 passed native timestamped frame ingestion, silence behavior, and visual-description tests.
- Qwen3-VL-4B passed constrained benign-frame verification. Its initial measured pass took 8.51 seconds excluding model loading; this is not a calibrated safety score.
- FastLLVE passed the actual CUDA path; YOLO11n/ByteTrack produced anonymous tracks on a public image.
- RelayVSR generated a labeled real enhanced review video. ReLift generated real trajectories and camera data. BVI-Mamba produced an aligned comparison video.
- An uploaded 84-frame public cartwheel clip, explicitly marked by a human, completed all three postprocessors, hash checks, review history, and feedback export in 285.99 seconds. This was not an automatic positive-incident test.
- The final public HTTPS/WSS test received 160 JPEG frames over a 23.83-second source interval: 6.68 received FPS, 139.8 ms mean ACK round trip, 559.6 ms maximum. It sampled 13 inference frames and reported 30 skipped samples. Total runtime including completion of verification was 38.56 seconds.
- Phone-protocol intake also remained available during heavy GPU processing. This does not imply concurrent GPU analysis while those jobs run.
- Seventeen CPU/API/protocol tests and the React production build passed. The user subsequently reported successful physical-device stream telemetry.

Metrics describe these specific engineering tests. Multi-camera support, event recall, false-negative rates, institutional adoption, and production reliability have not been established. A source hash identifies an artifact, not its correctness or trustworthiness.
