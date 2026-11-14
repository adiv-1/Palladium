#!/usr/bin/env bash
# Public Runpod HTTPS proxy access was explicitly approved by the owner.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a
source .env
set +a
export CUDA_HOME=/usr/local/cuda
export TORCH_EXTENSIONS_DIR=/workspace/.cache/torch_extensions
export OMP_NUM_THREADS=4
export TOKENIZERS_PARALLELISM=false
exec .venv/bin/uvicorn apps.api.main:app --host 0.0.0.0 --port 8000 --workers 1 --ws-max-size 2097152 --proxy-headers --forwarded-allow-ips='*'
