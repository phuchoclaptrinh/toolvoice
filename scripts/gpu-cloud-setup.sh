#!/usr/bin/env bash
set -euo pipefail

cd /root/Toolvoice

export PIP_DISABLE_PIP_VERSION_CHECK=1
export HF_HUB_DISABLE_SYMLINKS_WARNING=1

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  python3-venv python3-pip unzip git curl ffmpeg libsndfile1 build-essential

python3 -m venv backend/.venv
source backend/.venv/bin/activate
python -m pip install --upgrade pip wheel setuptools

python -m pip uninstall -y torch torchaudio torchvision || true
python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
python -m pip install -r backend/requirements.txt

python - <<'PY'
import torch
print("torch", torch.__version__)
print("cuda", torch.version.cuda)
print("available", torch.cuda.is_available())
print("device", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU")
PY

pkill -f "uvicorn backend.main:app" || true
CHATTERBOX_CORS_ORIGINS="http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001,http://n2.ckey.vn:2754,https://n2.ckey.vn:2754" \
TTS_MAX_WORKERS="${TTS_MAX_WORKERS:-1}" \
nohup backend/.venv/bin/python -m uvicorn backend.main:app --host 0.0.0.0 --port 7681 \
  > /root/Toolvoice/backend/gpu-backend.out.log \
  2> /root/Toolvoice/backend/gpu-backend.err.log &

sleep 4
curl -s http://127.0.0.1:7681/health
