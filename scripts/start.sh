#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
uv sync --frozen --extra test
npm ci --prefix apps/web --no-audit --no-fund
npm run build --prefix apps/web
uv run python -c 'from apps.api.config import settings; print("Workspace token is stored in",settings.data_dir / "app_token")'
exec uv run uvicorn apps.api.main:app --host 0.0.0.0 --port 8000 --workers 1 --ws-max-size 2097152
