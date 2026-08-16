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

if python - <<'PY'
import torch
raise SystemExit(0 if torch.cuda.is_available() else 1)
PY
then
  echo "Existing PyTorch CUDA install is usable."
else
  python -m pip uninstall -y torch torchaudio torchvision || true
  python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
fi

grep -Ev '^(torch|torchaudio|chatterbox-tts)$' backend/requirements.txt > /tmp/toolvoice-requirements-gpu.txt
python -m pip install -r /tmp/toolvoice-requirements-gpu.txt
python -m pip install \
  "numpy>=2.0.0" \
  "librosa==0.11.0" \
  "s3tokenizer" \
  "transformers==5.2.0" \
  "diffusers==0.29.0" \
  "resemble-perth>=1.0.0" \
  "conformer==0.3.2" \
  "safetensors==0.5.3" \
  "spacy-pkuseg" \
  "pykakasi==2.3.0" \
  "gradio==6.8.0" \
  "pyloudnorm" \
  "omegaconf"
python -m pip install --no-deps chatterbox-tts

python - <<'PY'
import torch
print("torch", torch.__version__)
print("cuda", torch.version.cuda)
print("available", torch.cuda.is_available())
print("device", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU")
PY

if ps -eo comm=,args= | grep -q '^[[:space:]]*ttyd .* -p 7681'; then
  for run_file in /etc/services.d/ttyd/run /run/s6/legacy-services/ttyd/run; do
    if [ -f "$run_file" ]; then
      sed -i 's/-p "${TTYD_PORT:-768[0-9]}"/-p "7682"/g' "$run_file"
      sed -i 's/-p "${TTYD_PORT}"/-p "7682"/g' "$run_file"
    fi
  done
  /package/admin/s6/command/s6-svc -t /run/service/ttyd 2>/dev/null || true
  sleep 2
  ps -eo pid=,comm= | awk '$2 == "ttyd" {print $1}' | while read -r pid; do
    kill -TERM "$pid" 2>/dev/null || true
  done
  sleep 2
fi

ps -eo pid=,args= | awk '/uvicorn backend.main:app/ && !/awk/ {print $1}' | while read -r pid; do
  kill -TERM "$pid" 2>/dev/null || true
done

PUBLIC_API_ORIGIN=""
if [ -n "${PUBLIC_IPADDR:-}" ] && [ -n "${VAST_TCP_PORT_7681:-}" ]; then
  PUBLIC_API_ORIGIN=",http://${PUBLIC_IPADDR}:${VAST_TCP_PORT_7681}"
fi

CHATTERBOX_CORS_ORIGINS="http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001,http://n2.ckey.vn:2754,https://n2.ckey.vn:2754${PUBLIC_API_ORIGIN}" \
TTS_MAX_WORKERS="${TTS_MAX_WORKERS:-1}" \
nohup backend/.venv/bin/python -m uvicorn backend.main:app --host 0.0.0.0 --port 7681 \
  > /root/Toolvoice/backend/gpu-backend.out.log \
  2> /root/Toolvoice/backend/gpu-backend.err.log &

sleep 4
curl -s http://127.0.0.1:7681/health
