#!/usr/bin/env bash
# Tested on the allocated A40 image. Never provisions or changes billing.
set -euo pipefail
cd "$(dirname "$0")/.."
export UV_CACHE_DIR=/workspace/.cache/uv
export HF_HOME=/workspace/.cache/huggingface
export CUDA_HOME=/usr/local/cuda
export PATH=/usr/local/cuda/bin:$PATH
export MAX_JOBS=4
export TORCH_CUDA_ARCH_LIST=8.6
export TORCH_EXTENSIONS_DIR=/workspace/.cache/torch_extensions
set -a
source .env
set +a
case "${1:-}" in
  all)
    for stage in base moss fastllve relayvsr relift verifier bvimamba; do bash scripts/setup_runpod_models.sh "$stage"; done
    ;;
  base)
    nvidia-smi
    uv sync --python 3.11 --frozen --extra test
    uv pip install --python .venv/bin/python -r requirements-gpu-control.lock.txt --index-strategy unsafe-best-match
    ;;
  moss)
    .venv/bin/python scripts/models.py moss --weights
    if ! test -x third_party/MOSS-VL/.venv/bin/python; then uv venv --python 3.12 third_party/MOSS-VL/.venv; fi
    uv pip install --python third_party/MOSS-VL/.venv/bin/python torch==2.8.0+cu128 torchvision==0.23.0+cu128 transformers==4.57.1 accelerate==1.12.0 torchcodec==0.7.0 numpy==2.4.3 pillow==12.1.1 joblib==1.5.2 einops==0.8.2 bitsandbytes==0.49.2 hqq==0.2.8.post1 fastapi==0.115.12 uvicorn==0.34.2 websockets==15.0.1 --extra-index-url https://download.pytorch.org/whl/cu128 --index-strategy unsafe-best-match
    uv pip install --python third_party/MOSS-VL/.venv/bin/python --no-deps 'https://github.com/Dao-AILab/flash-attention/releases/download/v2.8.1/flash_attn-2.8.1%2Bcu12torch2.8cxx11abiTRUE-cp312-cp312-linux_x86_64.whl'
    ;;
  fastllve)
    .venv/bin/python scripts/models.py fastllve
    if git -C third_party/FastLLVE apply --check --ignore-whitespace ../../patches/fastllve-constructor.patch; then
      git -C third_party/FastLLVE apply --ignore-whitespace ../../patches/fastllve-constructor.patch
    else
      git -C third_party/FastLLVE apply --reverse --check --ignore-whitespace ../../patches/fastllve-constructor.patch
    fi
    .venv/bin/python third_party/FastLLVE/models/IALUT/IALUT_transform/setup.py install
    if ! test -f weights/fastllve/FastLLVE_pretrain/FastLLVE_outdoor.pth; then
      .venv/bin/gdown --folder 'https://drive.google.com/drive/folders/1AGgjfo6zriGJkZYLLBT6j55F4vNkdjWO' -O weights/fastllve/
    fi
    ;;
  relayvsr)
    .venv/bin/python scripts/models.py relayvsr --weights
    cd third_party/RelayVSR
    uv sync --locked --extra cu128
    .venv/bin/python - <<'PY'
import json
from pathlib import Path
lock=json.loads(Path('../../model-lock.json').read_text())['relayvsr']
Path('weights/provenance.json').write_text(json.dumps({'repository':lock['weights_repository'],'revision':lock['weights_revision']},indent=2))
PY
    ;;
  relift)
    .venv/bin/python scripts/models.py relift
    cd third_party/ReLift
    if ! test -x .venv/bin/python; then uv venv --python 3.10 .venv; fi
    ../../.venv/bin/python - <<'PY'
import yaml
from pathlib import Path
dependencies=yaml.safe_load(Path('environment.yml').read_text())['dependencies']
Path('requirements-safesc.txt').write_text('\n'.join(next(d['pip'] for d in dependencies if isinstance(d,dict)))+'\n')
PY
    uv pip install --python .venv/bin/python -r requirements-safesc.txt torch==2.11.0+cu128 torchvision==0.26.0+cu128 --index-strategy unsafe-best-match
    uv pip install --python .venv/bin/python -e . --no-deps
    .venv/bin/python scripts/download_weights.py --only relift da3 alltracker
    ;;
  verifier)
    uv pip install --python .venv/bin/python peft==0.17.1 lm-format-enforcer==0.11.3
    .venv/bin/python - <<'PY'
import json
from pathlib import Path
from huggingface_hub import snapshot_download
lock=json.loads(Path('model-lock.json').read_text()).get('verifier',{})
revision=lock.get('weights_revision','ebb281ec70b05090aa6165b016eac8ec08e71b17')
snapshot_download('Qwen/Qwen3-VL-4B-Instruct',revision=revision,local_dir='weights/qwen3-vl-4b')
PY
    ;;
  bvimamba)
    .venv/bin/python scripts/models.py bvimamba
    mkdir -p weights/bvimamba
    if ! test -f weights/bvimamba/BVIMamba_BVI.pth.tar; then
      curl -fL --retry 2 'https://github.com/edhuang1/BVI-Mamba/releases/download/v1.0.0/BVIMamba_BVI.pth.tar' -o weights/bvimamba/BVIMamba_BVI.pth.tar
    fi
    BASICSR_JIT=True .venv/bin/python -c 'import sys;sys.path.insert(0,"third_party/BVI-Mamba");import dcn'
    ;;
  *) echo 'Choose all, base, moss, fastllve, relayvsr, relift, verifier, or bvimamba'; exit 2 ;;
esac
