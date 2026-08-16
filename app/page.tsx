"use client";

import { ChangeEvent, FormEvent, useEffect, useMemo, useRef, useState } from "react";

type VoiceProfile = { voice_id: string; filename: string; size: number };
type Health = { ok: boolean; device: string; gpu_name?: string | null; max_text_chars?: number; chunk_text_chars?: number };
type Estimate = { device: string; chunks: number; estimated_seconds: number; seconds_per_chunk: number; max_text_chars: number; chunk_text_chars: number };
type ModelOption = { id: string; label: string; engine: string; languages: string[]; fast: boolean; voice_cloning: boolean; available?: boolean; note?: string };
type JobResponse = {
  job_id: string;
  status: "queued" | "running" | "done" | "error";
  phase: "queued" | "generating" | "finalizing" | "audio_ready" | "error";
  message: string;
  model: string;
  device: string;
  chunks_total: number;
  chunks_done: number;
  estimated_seconds: number;
  seconds_per_chunk: number;
  created_at: number;
  started_at?: number | null;
  finished_at?: number | null;
  result?: { audio_url: string; sample_rate: number; model: string; chunks: number; fallback_from?: string } | null;
  error?: string | null;
};
type StudioPhase = "idle" | "checking" | "extracting" | "voice_ready" | "queued" | "generating" | "finalizing" | "audio_ready" | "error";

const API_BASE = process.env.NEXT_PUBLIC_CHATTERBOX_API?.replace(/\/$/, "") || "http://127.0.0.1:8000";

const fallbackModels: ModelOption[] = [
  { id: "turbo-fast", label: "Chatterbox Turbo Fast Cut", engine: "Chatterbox", languages: ["en"], fast: true, voice_cloning: true, available: true },
  { id: "turbo", label: "Chatterbox Turbo", engine: "Chatterbox", languages: ["en"], fast: true, voice_cloning: true, available: true },
  { id: "multilingual", label: "Chatterbox Multilingual", engine: "Chatterbox", languages: ["en"], fast: false, voice_cloning: true, available: true },
];

const languages = [
  ["en", "English"],
  ["vi", "Tiếng Việt"],
  ["zh", "Chinese"],
  ["ja", "Japanese"],
  ["ko", "Korean"],
  ["fr", "French"],
  ["de", "German"],
  ["es", "Spanish"],
  ["pt", "Portuguese"],
  ["ru", "Russian"],
  ["it", "Italian"],
  ["hi", "Hindi"],
  ["ms", "Malay"],
  ["tr", "Turkish"],
];

const defaultText = "Xin chào, đây là đoạn thử nghiệm được tạo bằng giọng nói đã clone.";
const waveBars = Array.from({ length: 48 }, (_, index) => 8 + ((index * 17) % 26));
const resultBars = Array.from({ length: 62 }, (_, index) => 9 + ((index * 13) % 36));

export default function Home() {
  const [health, setHealth] = useState<Health | null>(null);
  const [models, setModels] = useState<ModelOption[]>(fallbackModels);
  const [model, setModel] = useState("turbo-fast");
  const [language, setLanguage] = useState("en");
  const [voiceFile, setVoiceFile] = useState<File | null>(null);
  const [voice, setVoice] = useState<VoiceProfile | null>(null);
  const [text, setText] = useState(defaultText);
  const [temperature, setTemperature] = useState(0.8);
  const [exaggeration, setExaggeration] = useState(0.5);
  const [cfgWeight, setCfgWeight] = useState(0.5);
  const [estimate, setEstimate] = useState<Estimate | null>(null);
  const [phase, setPhase] = useState<StudioPhase>("checking");
  const [message, setMessage] = useState("Đang kiểm tra backend...");
  const [job, setJob] = useState<JobResponse | null>(null);
  const [audioUrl, setAudioUrl] = useState("");
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [elapsedNow, setElapsedNow] = useState(Date.now());
  const audioRef = useRef<HTMLAudioElement>(null);

  const activeModel = models.find((item) => item.id === model);
  const allowedLanguages = activeModel?.languages ?? ["en"];
  const textStats = useMemo(() => {
    const words = text.trim().split(/\s+/).filter(Boolean).length;
    return `${text.length.toLocaleString("vi-VN")} / ${(estimate?.max_text_chars ?? 12000).toLocaleString("vi-VN")} ký tự · ${words.toLocaleString("vi-VN")} từ`;
  }, [text, estimate]);
  const chunksTotal = job?.chunks_total || estimate?.chunks || 1;
  const chunksDone = job?.chunks_done || (phase === "audio_ready" ? chunksTotal : 0);
  const progress = phase === "audio_ready" ? 100 : Math.min(96, Math.round((chunksDone / Math.max(1, chunksTotal)) * 100));
  const elapsedSeconds = startedAt ? Math.floor((elapsedNow - startedAt) / 1000) : 0;
  const estimatedSeconds = job?.estimated_seconds ?? estimate?.estimated_seconds ?? 0;
  const remainingSeconds = isRendering(phase) ? Math.max(0, estimatedSeconds - elapsedSeconds) : estimatedSeconds;
  const canGenerate = Boolean(text.trim()) && Boolean(voiceFile || voice) && !isBusy(phase);
  const backendOnline = Boolean(health?.ok) && health?.device !== "offline";

  useEffect(() => {
    const timer = window.setInterval(() => setElapsedNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    refreshBackend();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      const body = new FormData();
      body.append("text", text.trim());
      body.append("voice_ready", String(Boolean(voice)));
      body.append("model", model);

      fetch(`${API_BASE}/api/estimate`, { method: "POST", body, signal: controller.signal })
        .then((response) => {
          if (!response.ok) throw new Error("Không lấy được dự đoán.");
          return response.json();
        })
        .then((data: Estimate) => setEstimate(data))
        .catch((error) => {
          if (error instanceof Error && error.name === "AbortError") return;
          setHealth((current) => (current ? { ...current, device: "offline" } : null));
        });
    }, 350);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [text, voice, model]);

  async function refreshBackend() {
    try {
      const [healthResponse, modelResponse] = await Promise.all([
        fetch(`${API_BASE}/health`),
        fetch(`${API_BASE}/api/models`),
      ]);
      if (!healthResponse.ok) throw new Error("Backend chưa sẵn sàng.");
      const nextHealth = (await healthResponse.json()) as Health;
      setHealth(nextHealth);
      if (modelResponse.ok) {
        const data = await modelResponse.json();
        if (Array.isArray(data.models)) {
          setModels(data.models);
          const current = data.models.find((item: ModelOption) => item.id === model);
          if (current?.available === false) {
            const fallback = data.models.find((item: ModelOption) => item.available !== false);
            changeModel(fallback?.id ?? "turbo", data.models);
          }
        }
      }
      setPhase((current) => (current === "checking" || current === "error" ? "idle" : current));
      setMessage("Hệ thống sẵn sàng.");
    } catch {
      setHealth({ ok: false, device: "offline" });
      setPhase("error");
      setMessage(`Không kết nối được backend: ${API_BASE}`);
    }
  }

  function changeModel(nextModel: string, modelList = models) {
    setModel(nextModel);
    const option = modelList.find((item) => item.id === nextModel);
    if (option?.languages[0]) setLanguage(option.languages[0]);
  }

  function onFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0] ?? null;
    setVoiceFile(file);
    setVoice(null);
    setAudioUrl("");
    setJob(null);
    setStartedAt(null);
    setPhase("idle");
    setMessage(file ? `Đã chọn: ${file.name}` : "Chưa chọn mẫu giọng.");
  }

  async function uploadVoice() {
    if (!voiceFile) throw new Error("Chọn file giọng mẫu trước.");
    setPhase("extracting");
    setStartedAt((current) => current ?? Date.now());
    setMessage("Đang lưu mẫu giọng...");

    const body = new FormData();
    body.append("file", voiceFile);
    const response = await fetch(`${API_BASE}/api/voices`, { method: "POST", body });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || "Không tải được mẫu giọng.");
    }

    const profile = (await response.json()) as VoiceProfile;
    setVoice(profile);
    setPhase("voice_ready");
    setMessage("Mẫu giọng đã sẵn sàng.");
    return profile;
  }

  async function handleUploadVoice() {
    try {
      setStartedAt(Date.now());
      await uploadVoice();
    } catch (error) {
      fail(error);
    }
  }

  async function synthesize(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!text.trim()) {
      fail(new Error("Nhập nội dung cần đọc."));
      return;
    }

    try {
      setAudioUrl("");
      setJob(null);
      setStartedAt(Date.now());
      const profile = voice ?? (await uploadVoice());
      setPhase("queued");
      setMessage("Đang gửi job...");

      const body = new FormData();
      body.append("text", text.trim());
      body.append("voice_id", profile.voice_id);
      body.append("language_id", language);
      body.append("model", model);
      body.append("temperature", String(temperature));
      body.append("exaggeration", String(exaggeration));
      body.append("cfg_weight", String(cfgWeight));

      const response = await fetch(`${API_BASE}/api/tts/jobs`, { method: "POST", body });
      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.detail || "Không tạo được job.");
      }
      const createdJob = (await response.json()) as JobResponse;
      setJob(createdJob);
      await pollJob(createdJob.job_id);
    } catch (error) {
      fail(error);
    }
  }

  async function pollJob(jobId: string) {
    let keepPolling = true;
    while (keepPolling) {
      await wait(1400);
      const response = await fetch(`${API_BASE}/api/tts/jobs/${jobId}`);
      if (!response.ok) throw new Error("Không đọc được tiến độ.");
      const nextJob = (await response.json()) as JobResponse;
      setJob(nextJob);
      setPhase(mapJobPhase(nextJob));
      setMessage(nextJob.message || "Đang xử lý.");

      if (nextJob.status === "done" && nextJob.result) {
        const url = `${API_BASE}${nextJob.result.audio_url}`;
        setAudioUrl(url);
        setPhase("audio_ready");
        setMessage("Tạo giọng nói hoàn tất.");
        window.setTimeout(() => audioRef.current?.play().catch(() => {}), 150);
        keepPolling = false;
      }
      if (nextJob.status === "error") throw new Error(nextJob.error || nextJob.message);
    }
  }

  function fail(error: unknown) {
    const detail = error instanceof Error ? error.message : "Có lỗi xảy ra.";
    setPhase("error");
    setMessage(detail === "Failed to fetch" ? `Không kết nối được backend: ${API_BASE}` : detail);
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-icon">V</div>
          <div>
            <strong>VoiceLab</strong>
            <span>PHÒNG THU GIỌNG AI</span>
          </div>
        </div>
        <div className="nav-label">Không gian làm việc</div>
        <nav>
          <a className="active" href="#">
            <i />
            <span>Phòng thu giọng nói</span>
          </a>
          <a href="#">
            <i />
            <span>Giọng nói của tôi</span>
          </a>
          <a href="#">
            <i />
            <span>Lịch sử tạo giọng</span>
          </a>
        </nav>
        <div className="nav-label system-label">Hệ thống</div>
        <nav>
          <a href="#">
            <i />
            <span>API & Tích hợp</span>
          </a>
          <a href="#">
            <i />
            <span>Cài đặt</span>
          </a>
        </nav>
        <div className="side-bottom">
          <div className="usage">
            <div className="usage-head">
              <b>Thiết bị</b>
              <span>{deviceLabel(health?.device, health?.gpu_name)}</span>
            </div>
            <div className="usage-track">
              <i style={{ width: backendOnline ? "72%" : "18%" }} />
            </div>
          </div>
        </div>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div>
            <div className="eyebrow">Phòng thu giọng nói</div>
            <h1>Clone & Tạo giọng</h1>
          </div>
          <div className="top-actions">
            <button className={`system-status ${backendOnline ? "online" : "offline"}`} type="button" onClick={refreshBackend}>
              <i />
              {backendOnline ? "Hệ thống sẵn sàng" : "Kiểm tra backend"}
            </button>
            <div className="avatar">TV</div>
          </div>
        </header>

        <form className="layout" onSubmit={synthesize}>
          <section>
            <div className="card">
              <div className="card-head">
                <div className="card-title">
                  <div className="step">01</div>
                  <div>
                    <h2>Clone giọng nói</h2>
                    <div className="sub">{voice ? "Hồ sơ giọng đã sẵn sàng" : "Tải lên mẫu giọng"}</div>
                  </div>
                </div>
                <span className={`tag ${voice ? "success" : ""}`}>{voice ? "SẴN SÀNG" : "AUDIO"}</span>
              </div>
              <div className="card-body">
                <label className="dropzone">
                  <input accept="audio/*" type="file" onChange={onFileChange} aria-label="Chọn file giọng mẫu" />
                  <div className="upload-icon">↥</div>
                  <strong>{voiceFile ? voiceFile.name : "Kéo thả hoặc chọn file âm thanh"}</strong>
                  <p>WAV, MP3, M4A, FLAC, OGG</p>
                  <span className="btn primary">Chọn file âm thanh</span>
                </label>

                {voiceFile ? (
                  <div className="audio-preview show">
                    <div className="audio-row">
                      <button className="play" type="button">▶</button>
                      <div className="audio-info">
                        <strong>{voiceFile.name}</strong>
                        <span>{formatFileSize(voiceFile.size)} · {voice ? "Đã xử lý" : "Chưa lưu"}</span>
                      </div>
                      <div className="wave">
                        {waveBars.map((height, index) => (
                          <b key={index} style={{ height }} />
                        ))}
                      </div>
                    </div>
                  </div>
                ) : null}

                {voice ? (
                  <div className="voice-card show">
                    <div className="voice-row">
                      <div className="voice-avatar">◉</div>
                      <div className="voice-meta">
                        <strong>Giọng nói đã clone</strong>
                        <span>{voice.filename}</span>
                      </div>
                      <span className="tag success">READY</span>
                    </div>
                  </div>
                ) : null}

                <button className="btn full" type="button" onClick={handleUploadVoice} disabled={!voiceFile || isBusy(phase)}>
                  Lưu mẫu giọng
                </button>
              </div>
            </div>

            <div className="card create-card">
              <div className="card-head">
                <div className="card-title">
                  <div className="step">02</div>
                  <div>
                    <h2>Tạo giọng từ văn bản</h2>
                    <div className="sub">{textStats}</div>
                  </div>
                </div>
              </div>
              <div className="card-body">
                <textarea
                  value={text}
                  onChange={(event) => setText(event.target.value)}
                  maxLength={estimate?.max_text_chars ?? 12000}
                  aria-label="Nội dung cần đọc"
                  placeholder="Nhập nội dung muốn chuyển thành giọng nói..."
                />
                <div className="text-tools">
                  <span>{estimate?.chunks ?? 1} chunk</span>
                  <span>Ước tính {formatDuration(estimate?.estimated_seconds ?? 0)}</span>
                </div>

                <div className="voice-settings">
                  <label>
                    Model
                    <select value={model} onChange={(event) => changeModel(event.target.value)}>
                      {models.map((option) => (
                        <option key={option.id} value={option.id} disabled={option.available === false}>
                          {option.label}
                          {option.available === false ? " (thiếu weight)" : ""}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Ngôn ngữ
                    <select value={language} onChange={(event) => setLanguage(event.target.value)} disabled={allowedLanguages.length <= 1}>
                      {languages
                        .filter(([id]) => allowedLanguages.includes(id))
                        .map(([id, name]) => (
                          <option key={id} value={id}>
                            {name}
                          </option>
                        ))}
                    </select>
                  </label>
                  <Slider label="Temperature" value={temperature} min={0.05} max={1.5} step={0.05} onChange={setTemperature} />
                  <Slider label="Exaggeration" value={exaggeration} min={0} max={1.5} step={0.05} onChange={setExaggeration} />
                  <Slider label="CFG weight" value={cfgWeight} min={0} max={1.2} step={0.05} onChange={setCfgWeight} />
                </div>

                <div className={`processing ${isRendering(phase) || phase === "audio_ready" ? "show" : ""}`}>
                  <div className="processing-head">
                    <div className="processing-title">
                      {isRendering(phase) ? <span className="spinner" /> : <span className="done-dot" />}
                      <span>{message}</span>
                    </div>
                    <span className="processing-time">{formatDuration(elapsedSeconds)}</span>
                  </div>
                  <div className="process-track">
                    <div className="process-fill" style={{ width: `${progress}%` }} />
                  </div>
                  <div className="process-meta">
                    <span>{progress}% · {chunksDone}/{chunksTotal} chunk</span>
                    <span>Còn lại {formatDuration(remainingSeconds)}</span>
                  </div>
                </div>

                <button className="generate" type="submit" disabled={!canGenerate}>
                  {isBusy(phase) ? "Đang tạo giọng..." : "Tạo giọng nói →"}
                </button>
              </div>
            </div>
          </section>

          <aside className="right-card card">
            <div className="card-head">
              <div>
                <h2>Âm thanh đã tạo</h2>
                <div className="sub">Nghe thử kết quả</div>
              </div>
              <span className="tag">WAV</span>
            </div>

            {!audioUrl ? (
              <div className="empty-result">
                <div>
                  <div className="result-icon">♪</div>
                  <h3>Audio sẽ xuất hiện ở đây</h3>
                  <p>Chọn mẫu giọng, nhập nội dung, rồi nhấn Tạo giọng nói.</p>
                </div>
              </div>
            ) : (
              <div className="result show">
                <div className="result-player">
                  <div className="result-top">
                    <strong>giong-noi-da-tao.wav</strong>
                    <span className="duration">{formatDuration(elapsedSeconds)}</span>
                  </div>
                  <div className="timeline">
                    {resultBars.map((height, index) => (
                      <span key={index} style={{ height }} />
                    ))}
                  </div>
                  <audio ref={audioRef} src={audioUrl} controls />
                </div>
                <div className="result-actions">
                  <button className="btn" type="button" onClick={() => setAudioUrl("")}>Tạo lại</button>
                  <a className="btn primary" href={audioUrl} download>Tải xuống</a>
                </div>
                <div className="tips">
                  <strong>Tạo giọng nói hoàn tất</strong>
                  <p>{job?.result?.chunks ?? chunksTotal} chunk · {activeModel?.label ?? model}</p>
                </div>
              </div>
            )}
          </aside>
        </form>
      </main>
    </div>
  );
}

function Slider({ label, value, min, max, step, onChange }: { label: string; value: number; min: number; max: number; step: number; onChange: (value: number) => void }) {
  return (
    <label>
      {label}
      <select value={String(value)} onChange={(event) => onChange(Number(event.target.value))}>
        {[min, value, max].map((item) => (
          <option key={item} value={item}>{item.toFixed(2)}</option>
        ))}
      </select>
    </label>
  );
}

function mapJobPhase(job: JobResponse): StudioPhase {
  if (job.status === "error") return "error";
  if (job.status === "done") return "audio_ready";
  if (job.phase === "finalizing") return "finalizing";
  if (job.status === "queued") return "queued";
  return "generating";
}

function isBusy(phase: StudioPhase) {
  return phase === "extracting" || phase === "queued" || phase === "generating" || phase === "finalizing";
}

function isRendering(phase: StudioPhase) {
  return phase === "queued" || phase === "generating" || phase === "finalizing";
}

function deviceLabel(device?: string, gpuName?: string | null) {
  if (device === "cuda") return gpuName ? `GPU · ${gpuName}` : "GPU CUDA";
  if (device === "cpu") return "CPU";
  if (device === "mps") return "Apple GPU";
  return "Offline";
}

function formatDuration(totalSeconds: number) {
  const normalized = Math.max(0, Math.floor(totalSeconds || 0));
  const minutes = Math.floor(normalized / 60);
  const seconds = normalized % 60;
  if (minutes <= 0) return `${seconds}s`;
  return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
}

function formatFileSize(bytes: number) {
  if (!bytes) return "0 MB";
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

function wait(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}
