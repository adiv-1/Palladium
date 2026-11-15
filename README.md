# Palladium

A review and planning layer for USC’s existing cameras, incident records and campus teams. Built at **AI@USC — HackSC × the USC AI Committee** to help reviewers spot events sooner and make walking-route and ambassador decisions with better context.

- **Real-time analysis:** phone camera pairing or video upload, AI observations, and preserved clips for human review.
- **Incident response:** event locations, recent public reports, and a simulated four-responder notification flow.
- **Get Somewhere:** departure-time walking routes and ambassador positioning, with reasoning for each recommendation.

[Working prototype](https://xhogd16ggxiijr-8000.proxy.runpod.net/#live) · Workspace access token required.

## Infrastructure

- **Web:** React 19, TypeScript, Vite, MapLibre/OSM campus maps and Three.js motion visualization.
- **API:** Python 3.11, FastAPI and Uvicorn. WebRTC carries live dashboard video; acknowledged JPEG frames over WebSockets feed analysis separately. PyAV/OpenCV handle video timing and evidence windows.
- **GPU:** Runpod NVIDIA A40, 48 GB VRAM. The API and inference workers currently share one machine; upstream models have isolated Python/CUDA environments. Live inference takes priority over queued enhancement and training.
- **Storage:** SQLModel/SQLite for incidents, jobs and review history; SHA-256 hashes for originals; private Supabase Storage archives with retention and recall controls.
- **Hosting:** Runpod serves the current preview. An optional [Replit Node gateway](docs/overview/replit.md) proxies authenticated API/WebSocket traffic to the same backend.

Planning runs separately: the bundled LAPD history and OSM walking network drive recency-weighted seasonal estimates, connected routes and 400 m ambassador-coverage scenarios. [Data and methodology](docs/overview/activity-forecasting.md).

## AI stack

```text
Phone / upload → timestamped frames → MOSS observations + Qwen verification
              → evidence checks → preserved clip → human decision
```

- **[MOSS-VL-Realtime NF4](https://github.com/OpenMOSS/MOSS-VL):** streaming visual observations. Its narrative alone does not trigger an incident.
- **[Qwen3-VL-4B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct):** checks original frames and produces constrained event JSON. Schema, frame-reference and temporal-agreement checks gate saved alerts. A separate original-clip pass supplies a secondary review brief.
- **[FastLLVE](https://github.com/Wenhao-Li-777/FastLLVE):** enhancement when footage is dark. **[BVI-Mamba](https://github.com/edhuang1/BVI-Mamba):** optional offline low-light comparison.
- **[RelayVSR](https://github.com/kopperx/RelayVSR):** a separate super-resolution review copy. **[ReLift](https://github.com/ReLift3D/ReLift)** with Depth Anything 3/AllTracker estimates scene-point motion in 3D. Generated detail and tracks never replace original evidence.

A Qwen LoRA research adapter was trained on approved examples; MOSS uses pretrained weights. Evaluation and activation remain separate. [Model setup and licenses](docs/overview/models.md) · [Pinned revisions](model-lock.json) · [Training results](docs/overview/learning.md).

## Run locally

Requires **Python 3.11, Node.js 22 and [uv](https://docs.astral.sh/uv/)**. From a fresh clone:

```bash
git clone https://github.com/adiv-1/SafeSC.git
cd SafeSC
cp .env.example .env
uv sync --python 3.11 --frozen --extra test
npm ci --prefix apps/web
npm run build --prefix apps/web
uv run python -m scripts.data.activity_snapshot restore
uv run python -m scripts.make_demo
STREAMING_MODEL=fixture uv run uvicorn apps.api.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Open [localhost:8000](http://localhost:8000), enter the generated token from `data/app_token`, and upload `data/demo-fixture.mp4`. This runs without a GPU; detector results are visibly simulated.

For real AI, follow [Linux/CUDA model setup](docs/overview/models.md), configure MOSS and Qwen in `.env`, and use `STREAMING_MODEL=moss`. For a phone, serve HTTPS, set `PUBLIC_URL`, scan **Pair phone camera**, and allow camera/location access. [TURN setup](docs/overview/private-turn.md) covers restrictive networks.

Checks: `uv run pytest -q`; `node --test apps/gateway/server.test.mjs apps/web/src/*.test.mjs`; `npm run build --prefix apps/web`; `python3 scripts/check_secrets.py --tracked`.

## Next backend steps

For a campus pilot:

- Separate a CPU API from a warm GPU worker. Add Postgres and a shared job queue before API replicas; sessions and buffers currently belong to one process. [SQLite constraints](https://www.sqlite.org/wal.html).
- Use [durable GPU storage](https://docs.runpod.io/pods/storage/types), retain cloud backups, and monitor inference lag, failed jobs, false alerts and spend. Keep [Supabase service keys](https://supabase.com/docs/guides/storage/security/access-control) on the backend.
- Add USC SSO, reviewer roles and representative camera evaluation before operational DPS integration.

The prototype supports **one active source**. Detection can lag, miss or mislabel actions; dispatch is simulated; routes estimate reported activity rather than personal safety. No identity recognition is implemented. Credentials, private footage, databases and weights stay outside Git. [Public data](public-data/activity/README.md) and [authorized staged recordings](demo/videos/README.md) are included for reproducibility.

Built by Aditya, Sarath, Shraddha and Ojas at USC.
