#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends python3-venv nodejs npm curl git ffmpeg build-essential libgl1 libglib2.0-0
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/0.8.22/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
fi
cd /opt/safesc
uv sync --python 3.11 --frozen --extra test
npm ci --prefix apps/web --no-audit --no-fund
npm run build --prefix apps/web
if ! nvidia-smi; then
  printf '%s\n' 'GPU driver not operational. Install the Ubuntu-supported NVIDIA driver, reboot, and verify nvidia-smi before setup_gpu.sh.'
  exit 2
fi
printf '%s\n' 'Base application built. Run setup_gpu.sh, configure model revisions, then install the service file.'
