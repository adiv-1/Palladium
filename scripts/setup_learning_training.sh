#!/usr/bin/env bash
set -euo pipefail
cd /workspace/safesc
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python torch==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cu128
uv pip install --python .venv/bin/python transformers==4.57.1 peft==0.17.1 accelerate==1.11.0 bitsandbytes==0.49.2 pillow==11.2.1 av==16.0.1 pydantic==2.11.4 pydantic-settings==2.9.1 lm-format-enforcer==0.11.3 huggingface-hub==0.36.0 sentencepiece
.venv/bin/python - <<'PY'
from dotenv import dotenv_values
from huggingface_hub import snapshot_download
snapshot_download('Qwen/Qwen3-VL-4B-Instruct',revision='ebb281ec70b05090aa6165b016eac8ec08e71b17',local_dir='weights/qwen3vl-4b',token=dotenv_values('.env')['HF_TOKEN'])
PY
