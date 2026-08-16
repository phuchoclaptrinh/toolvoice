FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    HF_HUB_DISABLE_SYMLINKS_WARNING=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip git curl ffmpeg libsndfile1 build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /app/backend/requirements.txt

RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install --upgrade pip wheel setuptools \
    && /opt/venv/bin/python -m pip install torch==2.5.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cu121 \
    && /opt/venv/bin/python -m pip install -r /app/backend/requirements.txt

COPY backend /app/backend

ENV PATH="/opt/venv/bin:${PATH}"
EXPOSE 7681

CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "7681"]
