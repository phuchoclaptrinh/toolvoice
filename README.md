# Toolvoice

React frontend + FastAPI backend để upload mẫu giọng, clone giọng bằng model local
và chuyển text thành file WAV. Backend có chế độ job chạy nền để UI theo dõi
được từng bước: lưu mẫu giọng, xếp hàng, tạo voice theo chunk, ghép file.

## Kiến trúc

- `app/`: frontend React/Vinext.
- `backend/`: FastAPI, lưu voice profile và audio output trong `backend/data`.
- `backend/Dockerfile`: image backend GPU dùng CUDA 12.1 + PyTorch cu121.
- `.env.example`: cấu hình frontend trỏ tới backend.
- `backend/.env.example`: cấu hình CORS và giới hạn runtime.

## Chạy local

Frontend:

```bash
npm install
npm run dev
```

Backend CPU/GPU local:

```bash
python -m venv backend/.venv
backend\.venv\Scripts\activate
python -m pip install --upgrade pip wheel setuptools
python -m pip install -r backend/requirements.txt
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Tạo `.env.local` từ `.env.example`:

```bash
NEXT_PUBLIC_CHATTERBOX_API=http://127.0.0.1:8000
```

## Deploy frontend

Deploy frontend lên Vercel, Cloudflare Pages, Netlify hoặc server Node đều được.
Biến môi trường bắt buộc:

```bash
NEXT_PUBLIC_CHATTERBOX_API=https://your-backend-domain
```

Nếu backend chạy bằng HTTP hoặc port riêng trên GPU cloud, dùng URL public của
GPU server, ví dụ:

```bash
NEXT_PUBLIC_CHATTERBOX_API=http://n2.ckey.vn:2714
```

## Deploy backend GPU

Khuyến nghị dùng GPU NVIDIA có CUDA, tối thiểu 12 GB VRAM cho trải nghiệm ổn hơn.

Chạy trực tiếp trên Ubuntu GPU:

```bash
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip git curl ffmpeg libsndfile1 build-essential
python3 -m venv backend/.venv
source backend/.venv/bin/activate
python -m pip install --upgrade pip wheel setuptools
python -m pip install torch==2.5.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cu121
python -m pip install -r backend/requirements.txt
python -m uvicorn backend.main:app --host 0.0.0.0 --port 7681
```

Hoặc build Docker image:

```bash
docker build -f backend/Dockerfile -t toolvoice-backend .
docker run --gpus all -p 7681:7681 -v toolvoice-data:/app/backend/data toolvoice-backend
```

Biến môi trường backend nên đặt khi deploy:

```bash
CHATTERBOX_CORS_ORIGINS=https://your-frontend-domain
MAX_UPLOAD_MB=50
MAX_TEXT_CHARS=12000
CHUNK_TEXT_CHARS=850
TTS_MAX_WORKERS=1
```

`TTS_MAX_WORKERS=1` là mặc định an toàn cho GPU 12 GB. Chỉ tăng khi đã kiểm tra
VRAM thực tế, vì mỗi job tạo voice có thể giữ model và tensor lớn trên GPU.

## API chính

- `GET /health`: kiểm tra backend, thiết bị CPU/GPU.
- `GET /api/models`: danh sách model khả dụng.
- `POST /api/voices`: upload mẫu giọng.
- `POST /api/estimate`: backend dự đoán chunk và thời gian.
- `POST /api/tts/jobs`: tạo job TTS chạy nền.
- `GET /api/tts/jobs/{job_id}`: đọc tiến độ job.
- `POST /api/tts`: endpoint đồng bộ cũ, vẫn giữ để tương thích.

## Ghi chú model

- Chatterbox Turbo Fast Cut nhanh hơn nhưng phù hợp preview hơn bản cuối.
- Chatterbox Multilingual hiện không hỗ trợ `vi` trong package đang dùng.
- V-TTS được tách thành optional trong `backend/requirements-vtts.txt`; chỉ cài
  khi bạn có weight local hợp lệ.

Chỉ clone giọng khi bạn có quyền sử dụng mẫu giọng đó.
