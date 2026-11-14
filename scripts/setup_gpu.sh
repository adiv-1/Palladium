#!/usr/bin/env bash
# Run on the CUDA worker after system dependencies are installed.
set -euo pipefail
cd "$(dirname "$0")/.."
exec bash scripts/setup_runpod_models.sh all
