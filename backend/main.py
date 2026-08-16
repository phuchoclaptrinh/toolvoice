from __future__ import annotations

import os
import shutil
import uuid
import inspect
import time
import traceback
from functools import lru_cache
from pathlib import Path
from threading import Lock, Semaphore
from typing import Annotated, Any

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
VOICE_DIR = DATA_DIR / "voices"
OUTPUT_DIR = DATA_DIR / "outputs"
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "50")) * 1024 * 1024
MAX_TEXT_CHARS = int(os.getenv("MAX_TEXT_CHARS", "12000"))
CHUNK_TEXT_CHARS = int(os.getenv("CHUNK_TEXT_CHARS", "850"))
SILENCE_SECONDS_BETWEEN_CHUNKS = float(os.getenv("SILENCE_SECONDS_BETWEEN_CHUNKS", "0.18"))
TTS_MAX_WORKERS = max(1, int(os.getenv("TTS_MAX_WORKERS", "1")))
JOB_HISTORY_LIMIT = max(10, int(os.getenv("JOB_HISTORY_LIMIT", "50")))
ALLOWED_SUFFIXES = {".wav", ".mp3", ".m4a", ".flac", ".ogg"}
SUPPORTED_LANGUAGE_IDS = {
    "ar",
    "da",
    "de",
    "el",
    "en",
    "es",
    "fi",
    "fr",
    "he",
    "hi",
    "it",
    "ja",
    "ko",
    "ms",
    "nl",
    "no",
    "pl",
    "pt",
    "ru",
    "sv",
    "sw",
    "tr",
    "zh",
}
MODEL_IDS = {"english", "multilingual", "turbo", "turbo-fast", "nano", "vtts-zeroshot"}

VOICE_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Local Chatterbox Voice Studio")
DEFAULT_CORS_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3001",
    "http://localhost:8787",
    "http://127.0.0.1:8787",
]
configured_origins = [
    origin.strip()
    for origin in os.getenv("CHATTERBOX_CORS_ORIGINS", "").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=configured_origins or DEFAULT_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/audio", StaticFiles(directory=OUTPUT_DIR), name="audio")

JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = Lock()
TTS_SEMAPHORE = Semaphore(TTS_MAX_WORKERS)


def pick_device() -> str:
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return os.getenv("CHATTERBOX_DEVICE", "cpu")


def now_seconds() -> float:
    return time.time()


def estimate_seconds_for(model: str, chunk_count: int, voice_ready: bool = True) -> dict[str, int | str]:
    device = pick_device()
    if model == "vtts-zeroshot":
        seconds_per_chunk = 18 if device == "cuda" else 35
    elif model == "turbo-fast":
        seconds_per_chunk = 18 if device == "cuda" else 65
    elif model in {"turbo", "nano"}:
        seconds_per_chunk = 35 if device == "cuda" else 90
    else:
        seconds_per_chunk = 55 if device == "cuda" else 180
    extraction_seconds = 0 if voice_ready else 8
    finalizing_seconds = max(2, chunk_count)
    return {
        "device": device,
        "seconds_per_chunk": seconds_per_chunk,
        "extraction_seconds": extraction_seconds,
        "finalizing_seconds": finalizing_seconds,
        "estimated_seconds": extraction_seconds + chunk_count * seconds_per_chunk + finalizing_seconds,
    }


def create_job(model: str, chunks: int) -> str:
    job_id = uuid.uuid4().hex
    estimate = estimate_seconds_for(model, chunks)
    with JOBS_LOCK:
        JOBS[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "phase": "queued",
            "message": "Dang xep hang tao voice.",
            "model": model,
            "device": estimate["device"],
            "chunks_total": chunks,
            "chunks_done": 0,
            "estimated_seconds": estimate["estimated_seconds"],
            "seconds_per_chunk": estimate["seconds_per_chunk"],
            "created_at": now_seconds(),
            "started_at": None,
            "finished_at": None,
            "result": None,
            "error": None,
        }
    return job_id


def update_job(job_id: str, **changes: Any) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        job.update(changes)


def read_job(job_id: str) -> dict[str, Any]:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Khong tim thay job tao voice.")
        return dict(job)


def list_recent_jobs() -> list[dict[str, Any]]:
    with JOBS_LOCK:
        jobs = sorted(JOBS.values(), key=lambda item: item.get("created_at") or 0, reverse=True)
        return [dict(job) for job in jobs[:JOB_HISTORY_LIMIT]]


def force_torch_float32():
    try:
        import torch

        torch.set_default_dtype(torch.float32)
    except Exception:
        pass


@lru_cache(maxsize=4)
def load_model(model_name: str):
    device = pick_device()
    try:
        force_torch_float32()
        patch_missing_watermarker()
        if model_name == "multilingual":
            from chatterbox.mtl_tts import ChatterboxMultilingualTTS

            return load_from_pretrained(ChatterboxMultilingualTTS, device)

        if model_name == "turbo":
            from chatterbox.tts_turbo import ChatterboxTurboTTS

            return patch_chatterbox_float32(ChatterboxTurboTTS.from_pretrained(device=device))

        if model_name == "nano":
            from chatterbox.tts_turbo import ChatterboxTurboTTS

            return patch_chatterbox_float32(ChatterboxTurboTTS.from_pretrained(device=device))

        from chatterbox.tts import ChatterboxTTS

        return ChatterboxTTS.from_pretrained(device=device)
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Chua cai chatterbox-tts. Hay chay: "
                "python -m pip install -r backend/requirements.txt"
            ),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Khong load duoc Chatterbox model ({model_name}): {exc}",
        ) from exc


@lru_cache(maxsize=1)
def load_vtts_zeroshot():
    try:
        from v_tts import ZeroShotTTS

        return ZeroShotTTS(device=pick_device())
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Chua cai V-TTS. Hay chay: "
                "backend\\.venv\\Scripts\\python.exe -m pip install git+https://github.com/tronghieuit/v-tts.git"
            ),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Khong load duoc V-TTS Fast Vietnamese: {exc}",
        ) from exc


def clean_suffix(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail="Dinh dang audio chua duoc ho tro. Hay dung WAV, MP3, M4A, FLAC hoac OGG.",
        )
    return suffix


def load_from_pretrained(model_class, device: str):
    signature = inspect.signature(model_class.from_pretrained)
    kwargs = {"device": device}
    t3_model = os.getenv("CHATTERBOX_MULTILINGUAL_T3")
    if t3_model and "t3_model" in signature.parameters:
        kwargs["t3_model"] = t3_model
    return model_class.from_pretrained(**kwargs)


def patch_missing_watermarker():
    try:
        import perth

        if getattr(perth, "PerthImplicitWatermarker", None) is None:
            perth.PerthImplicitWatermarker = perth.DummyWatermarker
    except Exception:
        pass


def patch_chatterbox_float32(model):
    try:
        import torch
        import types

        for module in (getattr(model, "t3", None), getattr(model, "s3gen", None), getattr(model, "ve", None)):
            if hasattr(module, "float"):
                module.float()

        tokenizer = getattr(getattr(model, "s3gen", None), "tokenizer", None)
        if tokenizer is not None:
            if hasattr(tokenizer, "_mel_filters") and tokenizer._mel_filters is not None:
                tokenizer._mel_filters = tokenizer._mel_filters.to(dtype=torch.float32)
            if hasattr(tokenizer, "window") and tokenizer.window is not None:
                tokenizer.window = tokenizer.window.to(dtype=torch.float32)
            if hasattr(tokenizer, "float"):
                tokenizer.float()
            if not getattr(tokenizer, "_toolvoice_float32_patched", False):
                original_log_mel = tokenizer.log_mel_spectrogram

                def log_mel_float32(self, audio, padding=0):
                    if not torch.is_tensor(audio):
                        audio = torch.from_numpy(audio)
                    audio = audio.to(device=self.device, dtype=torch.float32)
                    if hasattr(self, "_mel_filters") and self._mel_filters is not None:
                        self._mel_filters = self._mel_filters.to(device=self.device, dtype=torch.float32)
                    if hasattr(self, "window") and self.window is not None:
                        self.window = self.window.to(device=self.device, dtype=torch.float32)
                    return original_log_mel(audio, padding=padding)

                tokenizer.log_mel_spectrogram = types.MethodType(log_mel_float32, tokenizer)
                tokenizer._toolvoice_float32_patched = True
        voice_encoder = getattr(model, "ve", None)
        if voice_encoder is not None and not getattr(voice_encoder, "_toolvoice_float32_patched", False):
            original_embeds_from_mels = voice_encoder.embeds_from_mels

            def embeds_from_mels_float32(self, mels, mel_lens=None, as_spk=False, batch_size=32, **kwargs):
                import numpy as np

                if isinstance(mels, list):
                    mels = [np.asarray(mel, dtype=np.float32) for mel in mels]
                elif torch.is_tensor(mels):
                    mels = mels.to(dtype=torch.float32)
                return original_embeds_from_mels(
                    mels,
                    mel_lens=mel_lens,
                    as_spk=as_spk,
                    batch_size=batch_size,
                    **kwargs,
                )

            voice_encoder.embeds_from_mels = types.MethodType(embeds_from_mels_float32, voice_encoder)
            voice_encoder._toolvoice_float32_patched = True
    except Exception:
        pass
    return model


def vtts_zeroshot_model_dir() -> Path:
    return Path(
        os.getenv(
            "VTTS_ZEROSHOT_MODEL_DIR",
            Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
            / "v_tts"
            / "models"
            / "zeroshot-vietnamese",
        )
    )


def has_vtts_zeroshot_weights() -> bool:
    model_dir = vtts_zeroshot_model_dir()
    return (model_dir / "config.json").exists() and any(model_dir.glob("G_*.pth"))


def has_chatterbox_nano() -> bool:
    try:
        import inspect
        from chatterbox.tts_turbo import ChatterboxTurboTTS

        return "nano" in inspect.signature(ChatterboxTurboTTS.from_pretrained).parameters
    except Exception:
        return False


def split_text(text: str, max_chars: int = CHUNK_TEXT_CHARS) -> list[str]:
    text = " ".join(text.split())
    chunks: list[str] = []
    current = ""

    sentences: list[str] = []
    start = 0
    for index, char in enumerate(text):
        if char in ".!?;:\n":
            sentence = text[start : index + 1].strip()
            if sentence:
                sentences.append(sentence)
            start = index + 1
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)

    if not sentences:
        sentences = [text]

    for sentence in sentences:
        if len(sentence) > max_chars:
            words = sentence.split()
            for word in words:
                if len(current) + len(word) + 1 > max_chars and current:
                    chunks.append(current.strip())
                    current = word
                else:
                    current = f"{current} {word}".strip()
            continue

        if len(current) + len(sentence) + 1 > max_chars and current:
            chunks.append(current.strip())
            current = sentence
        else:
            current = f"{current} {sentence}".strip()

    if current:
        chunks.append(current.strip())
    return chunks


def chunk_size_for_model(model: str) -> int:
    if model == "vtts-zeroshot":
        return 520
    if model == "turbo-fast":
        return 320
    return CHUNK_TEXT_CHARS


def generate_chunk(chatterbox, model: str, text: str, language_id: str, audio_prompt: str, kwargs: dict):
    force_torch_float32()
    if model == "multilingual":
        return chatterbox.generate(text, language_id=language_id, audio_prompt_path=audio_prompt, **kwargs)
    return chatterbox.generate(text, audio_prompt_path=audio_prompt, **kwargs)


def generate_turbo_fast_chunk(chatterbox, text: str, audio_prompt: str, temperature: float):
    import torch
    from chatterbox.tts_turbo import S3GEN_SIL, punc_norm

    force_torch_float32()
    patch_chatterbox_float32(chatterbox)
    chatterbox.prepare_conditionals(audio_prompt, exaggeration=0.0, norm_loudness=True)
    text = punc_norm(text)
    text_tokens = chatterbox.tokenizer(text, return_tensors="pt", padding=True, truncation=True)
    text_tokens = text_tokens.input_ids.to(chatterbox.device)

    speech_tokens = chatterbox.t3.inference_turbo(
        t3_cond=chatterbox.conds.t3,
        text_tokens=text_tokens,
        temperature=temperature,
        top_k=400,
        top_p=0.9,
        repetition_penalty=1.15,
        max_gen_len=420,
    )
    speech_tokens = speech_tokens[speech_tokens < 6561].to(chatterbox.device)
    silence = torch.tensor([S3GEN_SIL, S3GEN_SIL, S3GEN_SIL]).long().to(chatterbox.device)
    speech_tokens = torch.cat([speech_tokens, silence])

    wav, _ = chatterbox.s3gen.inference(
        speech_tokens=speech_tokens,
        ref_dict=chatterbox.conds.gen,
        n_cfm_timesteps=2,
    )
    wav = wav.squeeze(0).detach().cpu().numpy()
    watermarked_wav = chatterbox.watermarker.apply_watermark(wav, sample_rate=chatterbox.sr)
    return torch.from_numpy(watermarked_wav).unsqueeze(0)


def ensure_float32_wav(wav):
    try:
        import torch

        if isinstance(wav, torch.Tensor):
            return wav.detach().cpu().float()
    except Exception:
        pass
    return wav


def is_dtype_mismatch(error: Exception) -> bool:
    message = str(error).lower()
    return "expected scalar type double but found float" in message or "expected scalar type float but found double" in message


def generate_vtts_chunks(text: str, audio_prompt: str, temperature: float, cfg_weight: float):
    import numpy as np

    if not has_vtts_zeroshot_weights():
        raise RuntimeError(
            "V-TTS da cai code nhung chua co weight local. "
            "Repo auto-download hien dang bi Hugging Face tra 401. "
            "Hay dat config.json va G_*.pth vao VTTS_ZEROSHOT_MODEL_DIR, "
            "hoac dung Chatterbox Nano/Turbo."
        )

    vtts = load_vtts_zeroshot()
    chunks = split_text(text, max_chars=520)
    wavs = []
    sample_rate = 22050
    for chunk in chunks:
        audio, sample_rate = vtts.synthesize(
            text=chunk,
            reference_audio=audio_prompt,
            noise_scale=max(0.1, min(1.2, temperature)),
            noise_scale_w=max(0.1, min(1.2, cfg_weight + 0.3)),
            length_scale=1.0,
        )
        wavs.append(audio)

    if len(wavs) == 1:
        return wavs[0], sample_rate, len(chunks)

    silence = np.zeros(int(sample_rate * SILENCE_SECONDS_BETWEEN_CHUNKS), dtype=wavs[0].dtype)
    merged = []
    for index, wav in enumerate(wavs):
        merged.append(wav)
        if index != len(wavs) - 1:
            merged.append(silence)
    return np.concatenate(merged), sample_rate, len(chunks)


def concat_wavs(wavs: list, sample_rate: int):
    import torch

    if len(wavs) == 1:
        return wavs[0].detach().cpu()

    normalized = []
    silence_samples = int(sample_rate * SILENCE_SECONDS_BETWEEN_CHUNKS)
    for index, wav in enumerate(wavs):
        wav = wav.detach().cpu()
        normalized.append(wav)
        if index != len(wavs) - 1:
            normalized.append(torch.zeros((wav.shape[0], silence_samples), dtype=wav.dtype))
    return torch.cat(normalized, dim=-1)


def synthesize_with_chatterbox_fallback(
    text: str,
    audio_prompt: str,
    language_id: str,
    temperature: float,
    exaggeration: float,
    cfg_weight: float,
    failed_model: str,
):
    fallback_model = "english"
    chatterbox = load_model(fallback_model)
    chunks = split_text(text)
    wavs = [
        generate_chunk(
            chatterbox=chatterbox,
            model=fallback_model,
            text=chunk,
            language_id=language_id,
            audio_prompt=audio_prompt,
            kwargs={
                "temperature": temperature,
                "exaggeration": max(0.0, exaggeration),
                "cfg_weight": max(0.0, cfg_weight),
            },
        )
        for chunk in chunks
    ]
    wav = ensure_float32_wav(concat_wavs(wavs, chatterbox.sr))
    output_name = f"{uuid.uuid4().hex}.wav"
    output_path = OUTPUT_DIR / output_name

    import torchaudio as ta

    ta.save(str(output_path), wav, chatterbox.sr)
    return {
        "audio_url": f"/audio/{output_name}",
        "sample_rate": chatterbox.sr,
        "model": fallback_model,
        "fallback_from": failed_model,
        "language_id": None,
        "chunks": len(chunks),
    }


def synthesize_with_turbo_fast(text: str, audio_prompt: str, temperature: float):
    chatterbox = load_model("turbo")
    chunks = split_text(text, max_chars=320)
    wavs = []
    for chunk in chunks:
        try:
            wavs.append(
                generate_chunk(
                    chatterbox=chatterbox,
                    model="turbo",
                    text=chunk,
                    language_id="en",
                    audio_prompt=audio_prompt,
                    kwargs={
                        "temperature": float(temperature),
                        "exaggeration": 0.5,
                        "cfg_weight": 0.5,
                    },
                )
            )
        except TypeError:
            wavs.append(
                generate_chunk(
                    chatterbox=chatterbox,
                    model="turbo",
                    text=chunk,
                    language_id="en",
                    audio_prompt=audio_prompt,
                    kwargs={},
                )
            )
        except Exception as exc:
            if not is_dtype_mismatch(exc):
                raise
            wavs.append(
                generate_chunk(
                    chatterbox=load_model("english"),
                    model="english",
                    text=chunk,
                    language_id="en",
                    audio_prompt=audio_prompt,
                    kwargs={
                        "temperature": float(temperature),
                        "exaggeration": 0.5,
                        "cfg_weight": 0.5,
                    },
                )
            )

    wav = ensure_float32_wav(concat_wavs(wavs, chatterbox.sr))
    output_name = f"{uuid.uuid4().hex}.wav"
    output_path = OUTPUT_DIR / output_name

    import torchaudio as ta

    ta.save(str(output_path), wav, chatterbox.sr)
    return {
        "audio_url": f"/audio/{output_name}",
        "sample_rate": chatterbox.sr,
        "model": "turbo-fast",
        "language_id": None,
        "chunks": len(chunks),
        "fast_cut": True,
    }


@app.get("/health")
def health():
    device = pick_device()
    gpu_name = None
    try:
        import torch

        if device == "cuda":
            gpu_name = torch.cuda.get_device_name(0)
    except Exception:
        pass
    return {
        "ok": True,
        "device": device,
        "gpu_name": gpu_name,
        "max_text_chars": MAX_TEXT_CHARS,
        "chunk_text_chars": CHUNK_TEXT_CHARS,
    }


@app.post("/api/estimate")
async def estimate_tts(
    text: Annotated[str, Form()],
    voice_ready: Annotated[bool, Form()] = False,
    model: Annotated[str, Form()] = "multilingual",
):
    text = text.strip()
    chunk_size = chunk_size_for_model(model)
    chunks = split_text(text, max_chars=chunk_size) if text else [""]
    estimate = estimate_seconds_for(model, len(chunks), voice_ready=voice_ready)
    return {
        "device": estimate["device"],
        "characters": len(text),
        "chunks": len(chunks),
        "seconds_per_chunk": estimate["seconds_per_chunk"],
        "extraction_seconds": estimate["extraction_seconds"],
        "finalizing_seconds": estimate["finalizing_seconds"],
        "estimated_seconds": estimate["estimated_seconds"],
        "max_text_chars": MAX_TEXT_CHARS,
        "chunk_text_chars": chunk_size,
    }


@app.get("/api/models")
def list_models():
    vtts_available = has_vtts_zeroshot_weights()
    nano_available = has_chatterbox_nano()
    return {
        "models": [
            {
                "id": "vtts-zeroshot",
                "label": "V-TTS Fast Vietnamese",
                "engine": "V-TTS",
                "languages": ["vi"],
                "fast": True,
                "voice_cloning": True,
                "available": vtts_available,
                "note": (
                    "Ready with local weights"
                    if vtts_available
                    else "Can weight local: config.json va G_*.pth"
                ),
            },
            {
                "id": "turbo-fast",
                "label": "Chatterbox Turbo Fast Cut",
                "engine": "Chatterbox",
                "languages": ["en"],
                "fast": True,
                "voice_cloning": True,
                "available": True,
                "note": "Gioi han token/chunk de render nhanh hon, hop preview",
            },
            {
                "id": "nano",
                "label": "Chatterbox Nano",
                "engine": "Chatterbox",
                "languages": ["en"],
                "fast": True,
                "voice_cloning": True,
                "available": nano_available,
                "note": (
                    "Ready"
                    if nano_available
                    else "Package Chatterbox hien tai chua co Nano rieng; dung Turbo"
                ),
            },
            {
                "id": "turbo",
                "label": "Chatterbox Turbo",
                "engine": "Chatterbox",
                "languages": ["en"],
                "fast": True,
                "voice_cloning": True,
                "available": True,
            },
            {
                "id": "multilingual",
                "label": "Chatterbox Multilingual V3",
                "engine": "Chatterbox",
                "languages": sorted(SUPPORTED_LANGUAGE_IDS),
                "fast": False,
                "voice_cloning": True,
                "available": True,
            },
            {
                "id": "english",
                "label": "Chatterbox English",
                "engine": "Chatterbox",
                "languages": ["en"],
                "fast": False,
                "voice_cloning": True,
                "available": True,
            },
        ]
    }


@app.post("/api/voices")
async def create_voice(file: UploadFile = File(...)):
    suffix = clean_suffix(file.filename or "")
    voice_id = uuid.uuid4().hex
    target = VOICE_DIR / f"{voice_id}{suffix}"

    size = 0
    with target.open("wb") as buffer:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                target.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="File giong mau qua lon.")
            buffer.write(chunk)

    return {
        "voice_id": voice_id,
        "filename": file.filename or target.name,
        "size": size,
    }


def validate_tts_request(text: str, voice_id: str, language_id: str, model: str) -> str:
    text = text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Noi dung text dang trong.")
    if len(text) > MAX_TEXT_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"Text qua dai. Toi da hien tai la {MAX_TEXT_CHARS} ky tu.",
        )
    if model not in MODEL_IDS:
        raise HTTPException(status_code=400, detail="Model khong hop le.")
    if model == "multilingual" and language_id not in SUPPORTED_LANGUAGE_IDS:
        supported = ", ".join(sorted(SUPPORTED_LANGUAGE_IDS))
        raise HTTPException(
            status_code=400,
            detail=(
                f"Chatterbox ban nay chua ho tro language_id '{language_id}'. "
                f"Cac ngon ngu ho tro: {supported}."
            ),
        )

    matches = list(VOICE_DIR.glob(f"{voice_id}.*"))
    if not matches:
        raise HTTPException(status_code=404, detail="Khong tim thay voice profile.")
    return str(matches[0])


def render_tts_audio(
    text: str,
    audio_prompt: str,
    language_id: str,
    model: str,
    temperature: float,
    exaggeration: float,
    cfg_weight: float,
    job_id: str | None = None,
):
    text = text.strip()
    if job_id:
        update_job(
            job_id,
            status="running",
            phase="generating",
            started_at=now_seconds(),
            message="Dang tao audio tu text.",
        )

    if model == "turbo-fast":
        try:
            return synthesize_with_turbo_fast(
                text=text,
                audio_prompt=audio_prompt,
                temperature=float(temperature),
            )
        except Exception as exc:
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"Chatterbox Turbo Fast tao audio that bai: {exc}") from exc

    if model == "vtts-zeroshot":
        try:
            wav, sample_rate, chunk_count = generate_vtts_chunks(
                text=text,
                audio_prompt=audio_prompt,
                temperature=float(temperature),
                cfg_weight=float(cfg_weight),
            )
            output_name = f"{uuid.uuid4().hex}.wav"
            output_path = OUTPUT_DIR / output_name

            import soundfile as sf

            sf.write(str(output_path), wav, sample_rate)
            return {
                "audio_url": f"/audio/{output_name}",
                "sample_rate": sample_rate,
                "model": model,
                "language_id": "vi",
                "chunks": chunk_count,
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"V-TTS tao audio that bai: {exc}") from exc

    chatterbox = load_model(model)

    try:
        chunks = split_text(text)
        wavs = []
        for index, chunk in enumerate(chunks, start=1):
            if job_id:
                update_job(
                    job_id,
                    phase="generating",
                    chunks_done=index - 1,
                    message=f"Dang tao chunk {index}/{len(chunks)}.",
                )
            wavs.append(
                generate_chunk(
                chatterbox=chatterbox,
                model=model,
                text=chunk,
                language_id=language_id,
                audio_prompt=audio_prompt,
                kwargs={
                    "temperature": float(temperature),
                    "exaggeration": float(exaggeration),
                    "cfg_weight": float(cfg_weight),
                },
            )
            )
        if job_id:
            update_job(job_id, phase="finalizing", chunks_done=len(chunks), message="Dang ghep audio.")
        wav = ensure_float32_wav(concat_wavs(wavs, chatterbox.sr))
        output_name = f"{uuid.uuid4().hex}.wav"
        output_path = OUTPUT_DIR / output_name

        import torchaudio as ta

        ta.save(str(output_path), wav, chatterbox.sr)
        return {
            "audio_url": f"/audio/{output_name}",
            "sample_rate": chatterbox.sr,
            "model": model,
            "language_id": language_id if model == "multilingual" else None,
            "chunks": len(chunks),
        }
    except TypeError:
        # Older Chatterbox variants expose fewer sampling controls.
        chunks = split_text(text)
        wavs = []
        for index, chunk in enumerate(chunks, start=1):
            if job_id:
                update_job(
                    job_id,
                    phase="generating",
                    chunks_done=index - 1,
                    message=f"Dang tao chunk {index}/{len(chunks)}.",
                )
            wavs.append(
                generate_chunk(
                chatterbox=chatterbox,
                model=model,
                text=chunk,
                language_id=language_id,
                audio_prompt=audio_prompt,
                kwargs={},
            )
            )
        if job_id:
            update_job(job_id, phase="finalizing", chunks_done=len(chunks), message="Dang ghep audio.")
        wav = ensure_float32_wav(concat_wavs(wavs, chatterbox.sr))
        output_name = f"{uuid.uuid4().hex}.wav"
        output_path = OUTPUT_DIR / output_name

        import torchaudio as ta

        ta.save(str(output_path), wav, chatterbox.sr)
        return {
            "audio_url": f"/audio/{output_name}",
            "sample_rate": chatterbox.sr,
            "model": model,
            "language_id": language_id if model == "multilingual" else None,
            "chunks": len(chunks),
        }
    except Exception as exc:
        traceback.print_exc()
        if model in {"turbo", "nano"} and is_dtype_mismatch(exc):
            return synthesize_with_chatterbox_fallback(
                text=text,
                audio_prompt=audio_prompt,
                language_id=language_id,
                temperature=float(temperature),
                exaggeration=float(exaggeration),
                cfg_weight=float(cfg_weight),
                failed_model=model,
            )
        raise HTTPException(status_code=500, detail=f"Chatterbox tao audio that bai: {exc}") from exc


@app.post("/api/tts")
async def text_to_speech(
    text: Annotated[str, Form()],
    voice_id: Annotated[str, Form()],
    language_id: Annotated[str, Form()] = "en",
    model: Annotated[str, Form()] = "multilingual",
    temperature: Annotated[float, Form()] = 0.8,
    exaggeration: Annotated[float, Form()] = 0.5,
    cfg_weight: Annotated[float, Form()] = 0.5,
):
    audio_prompt = validate_tts_request(text, voice_id, language_id, model)
    return render_tts_audio(
        text=text,
        audio_prompt=audio_prompt,
        language_id=language_id,
        model=model,
        temperature=float(temperature),
        exaggeration=float(exaggeration),
        cfg_weight=float(cfg_weight),
    )


def run_tts_job(
    job_id: str,
    text: str,
    audio_prompt: str,
    language_id: str,
    model: str,
    temperature: float,
    exaggeration: float,
    cfg_weight: float,
):
    try:
        update_job(job_id, status="queued", phase="queued", message="Dang cho GPU worker.")
        with TTS_SEMAPHORE:
            result = render_tts_audio(
                text=text,
                audio_prompt=audio_prompt,
                language_id=language_id,
                model=model,
                temperature=temperature,
                exaggeration=exaggeration,
                cfg_weight=cfg_weight,
                job_id=job_id,
            )
        update_job(
            job_id,
            status="done",
            phase="audio_ready",
            chunks_done=result.get("chunks", read_job(job_id)["chunks_total"]),
            finished_at=now_seconds(),
            message="Audio da san sang.",
            result=result,
        )
    except HTTPException as exc:
        update_job(
            job_id,
            status="error",
            phase="error",
            finished_at=now_seconds(),
            message=str(exc.detail),
            error=str(exc.detail),
        )
    except Exception as exc:
        traceback.print_exc()
        update_job(
            job_id,
            status="error",
            phase="error",
            finished_at=now_seconds(),
            message=f"Tao audio that bai: {exc}",
            error=str(exc),
        )


@app.post("/api/tts/jobs")
async def create_tts_job(
    background_tasks: BackgroundTasks,
    text: Annotated[str, Form()],
    voice_id: Annotated[str, Form()],
    language_id: Annotated[str, Form()] = "en",
    model: Annotated[str, Form()] = "multilingual",
    temperature: Annotated[float, Form()] = 0.8,
    exaggeration: Annotated[float, Form()] = 0.5,
    cfg_weight: Annotated[float, Form()] = 0.5,
):
    audio_prompt = validate_tts_request(text, voice_id, language_id, model)
    chunks = split_text(text.strip(), max_chars=chunk_size_for_model(model))
    job_id = create_job(model=model, chunks=len(chunks))
    background_tasks.add_task(
        run_tts_job,
        job_id,
        text.strip(),
        audio_prompt,
        language_id,
        model,
        float(temperature),
        float(exaggeration),
        float(cfg_weight),
    )
    return read_job(job_id)


@app.get("/api/tts/jobs")
def get_tts_jobs():
    return {"jobs": list_recent_jobs()}


@app.get("/api/tts/jobs/{job_id}")
def get_tts_job(job_id: str):
    return read_job(job_id)


@app.delete("/api/voices/{voice_id}")
def delete_voice(voice_id: str):
    deleted = False
    for path in VOICE_DIR.glob(f"{voice_id}.*"):
        path.unlink(missing_ok=True)
        deleted = True
    if not deleted:
        raise HTTPException(status_code=404, detail="Khong tim thay voice profile.")
    return {"ok": True}


def reset_generated_audio():
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
