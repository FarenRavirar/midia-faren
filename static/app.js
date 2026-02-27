const state = {
  mode: "audio",
  settings: { audio: {}, video: {} },
  outputDir: "",
  jobs: new Map(),
  groups: new Map(),
  sse: null,
  liveStreams: new Map(),
  stageByJob: new Map(),
  fileHistoryExpanded: new Set(),
  notifiedErrors: new Set(),
  accelLastMode: "",
  accelChangeText: "",
  accelChangeUntil: 0,
};
const ACCEL_CHANGE_VISIBLE_MS = 12000;
const PROCESS_ACCEL_LABELS = {
  cpu: "Processo Usando CPU",
  gpu_no_cuda: "Processo Usando GPU sem CUDA",
  gpu_cuda: "Processo Usando GPU Com CUDA",
};

const audioDefaults = {
  mode: "audio",
  format: "mp3",
  bitrate: "192",
  audio_mode: "cbr",
  normalize: "off",
  preset: "",
};

const videoDefaults = {
  mode: "video",
  container: "mp4",
  codec: "h264",
  resolution: "best",
  bitrate: "auto",
  custom_bitrate: "",
  normalize: "off",
  video_accel: "off",
  preset: "",
  crf: "",
};

const audioPresets = [
  { label: "MP3 192k CBR", format: "mp3", bitrate: "192", audio_mode: "cbr" },
  { label: "MP3 320k CBR", format: "mp3", bitrate: "320", audio_mode: "cbr" },
  { label: "OGG VBR q5", format: "ogg", bitrate: "q5", audio_mode: "vbr" },
];

const videoPresets = [
  { label: "MP4 H.264 1080p", container: "mp4", codec: "h264", resolution: "1080", bitrate: "auto" },
  { label: "WEBM VP9 1080p", container: "webm", codec: "vp9", resolution: "1080", bitrate: "auto" },
];

function byId(id) {
  return document.getElementById(id);
}

function logClient(level, message) {
  fetch("/api/client-log", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ level, message }),
  }).catch(() => {});
}

function logElementPresence(ids) {
  const missing = ids.filter(id => !byId(id));
  if (missing.length) {
    logClient("error", `Elementos faltando: ${missing.join(", ")}`);
  } else {
    logClient("info", "Todos elementos principais encontrados");
  }
}

function logClick(id) {
  logClient("info", `Click: ${id}`);
}

function showErrorToast(message) {
  let box = document.getElementById("toast-container");
  if (!box) {
    box = document.createElement("div");
    box.id = "toast-container";
    box.className = "toast-container";
    document.body.appendChild(box);
  }
  const item = document.createElement("div");
  item.className = "toast toast-error";
  item.textContent = message;
  box.appendChild(item);
  setTimeout(() => item.remove(), 9000);
}

window.addEventListener("error", (event) => {
  const msg = event?.error?.stack || event?.message || "Erro JS";
  logClient("error", msg);
  showErrorToast("Erro detectado, consulte o log.");
});

window.addEventListener("unhandledrejection", (event) => {
  logClient("error", `UnhandledPromise: ${event.reason}`);
  showErrorToast("Erro detectado, consulte o log.");
});

function humanBytes(bytes) {
  if (!bytes && bytes !== 0) return "-";
  let b = Number(bytes);
  if (Number.isNaN(b)) return "-";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (b >= 1024 && i < units.length - 1) {
    b /= 1024;
    i += 1;
  }
  return `${b.toFixed(1)} ${units[i]}`;
}

function humanTime(seconds) {
  if (!seconds && seconds !== 0) return "-";
  let s = Number(seconds);
  if (Number.isNaN(s)) return "-";
  s = Math.max(0, Math.floor(s));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  return `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}

function parseTranscribeMetrics(rawSpeed) {
  const text = String(rawSpeed || "");
  if (!text.startsWith("trx:")) return null;
  const parts = text.slice(4).split("|");
  if (parts.length < 5) return null;
  const nums = parts.map((p) => Number(p));
  if (nums.some((n) => !Number.isFinite(n))) return null;
  const [elapsed, current, remaining, total, eta] = nums;
  return {
    elapsedSeconds: Math.max(0, elapsed),
    currentAudioSeconds: Math.max(0, current),
    remainingAudioSeconds: Math.max(0, remaining),
    totalAudioSeconds: Math.max(0, total),
    etaSeconds: eta >= 0 ? eta : null,
  };
}

function parseJobOptions(job) {
  if (!job || !job.options) return {};
  if (typeof job.options === "object") return job.options;
  try {
    return JSON.parse(String(job.options || "{}"));
  } catch {
    return {};
  }
}

function normalizeProcessAccelMode(value) {
  const text = String(value || "").trim().toLowerCase();
  if (text === "cpu" || text === "gpu_no_cuda" || text === "gpu_cuda") return text;
  return "";
}

function inferProcessAccelMode(job) {
  if (!job) return "";
  const mode = String(job.mode || "").trim().toLowerCase();
  if (mode !== "transcribe") {
    if (mode && mode !== "group") return "cpu";
    return "";
  }
  const direct = normalizeProcessAccelMode(job.process_accel_mode);
  if (direct) return direct;
  const opts = parseJobOptions(job);
  const runtimeDevice = String(opts.transcribe_runtime_device || "").trim().toLowerCase();
  const requestedDevice = String(opts.transcribe_device || "").trim().toLowerCase();
  const backend = String(opts.transcribe_backend_resolved || opts.transcribe_backend || "").trim().toLowerCase();
  if (runtimeDevice === "cuda") return "gpu_cuda";
  if (runtimeDevice === "cpu") {
    if (requestedDevice === "cuda" && (backend === "faster_whisper" || backend === "whisperx")) return "gpu_no_cuda";
    return "cpu";
  }
  if (requestedDevice === "cpu" || backend === "whisper_cpp") return "cpu";
  if (requestedDevice === "cuda") return "gpu_cuda";
  return "";
}

function updateStatusAccelIndicator(jobs) {
  const indicator = byId("status-accel-indicator");
  const change = byId("status-accel-change");
  if (!indicator) return;

  const allJobs = (jobs || []).filter((j) => String(j.status || "") !== "group");
  const runningJobs = allJobs.filter((j) => String(j.status || "") === "running");
  const runningTranscribe = runningJobs.filter((j) => String(j.mode || "").trim().toLowerCase() === "transcribe");
  let current = runningTranscribe[0] || runningJobs[0] || null;
  if (!current && allJobs.length) {
    const sorted = allJobs.sort((a, b) => String(b.updated_at || b.created_at || "").localeCompare(String(a.updated_at || a.created_at || "")));
    current = sorted[0];
  }

  const mode = inferProcessAccelMode(current);
  const label = mode ? PROCESS_ACCEL_LABELS[mode] : "Processo: sem atividade de transcricao";
  indicator.textContent = label;
  indicator.dataset.mode = mode || "none";
  indicator.title = current ? `Job: ${current.id}` : "";

  const now = Date.now();
  if (mode && state.accelLastMode && mode !== state.accelLastMode) {
    state.accelChangeText = `Alterou para: ${PROCESS_ACCEL_LABELS[mode]}`;
    state.accelChangeUntil = now + ACCEL_CHANGE_VISIBLE_MS;
    logClient("info", `aceleracao alterada: ${state.accelLastMode} -> ${mode}`);
  }
  state.accelLastMode = mode || "";

  if (change) {
    if (state.accelChangeText && now < state.accelChangeUntil) {
      change.textContent = state.accelChangeText;
    } else {
      change.textContent = "";
      state.accelChangeText = "";
    }
  }
}

const DOWNLOAD_STAGES = ["Download", "Conversao"];
const TRANSCRIBE_STAGES = ["Conversao para WAV", "Normalizacao", "Executando VAD", "Transcricao", "Juncao"];

function parseStageInfo(message) {
  const result = {
    fileLabel: "",
    fileKey: "",
    fileIndex: 0,
    fileTotal: 0,
    fileName: "",
    stageName: "",
    stagePercent: 0,
    stageIndex: 0,
    stageTotal: 0,
    stageSkipped: false,
    stageRecovered: false,
  };
  if (!message) return result;
  const fileMatch = message.match(/^\[(\d+)\/(\d+)\]\s+(.+?)\s+\|\s+/);
  if (fileMatch) {
    result.fileIndex = Number(fileMatch[1]) || 0;
    result.fileTotal = Number(fileMatch[2]) || 0;
    result.fileName = String(fileMatch[3]).trim();
    result.fileLabel = `Arquivo ${result.fileIndex}/${result.fileTotal}: ${result.fileName}`;
    result.fileKey = `${result.fileIndex}/${result.fileTotal}:${result.fileName}`;
  }
  const stageMatch = message.match(/Etapa\s+(\d+)\/(\d+):\s*([^()]+)\(([\d.]+)%\)/i);
  if (stageMatch) {
    result.stageIndex = Number(stageMatch[1]) || 0;
    result.stageTotal = Number(stageMatch[2]) || 0;
    result.stageName = String(stageMatch[3]).trim();
    result.stagePercent = Number(stageMatch[4]) || 0;
  }
  const skipMatch = message.match(/Etapa\s+(\d+)\/(\d+):\s*([^()]+)\(pulado\)/i);
  if (skipMatch) {
    result.stageIndex = Number(skipMatch[1]) || 0;
    result.stageTotal = Number(skipMatch[2]) || 0;
    result.stageName = String(skipMatch[3]).trim();
    result.stagePercent = 100.0;
    result.stageSkipped = true;
  }
  const recoveredMatch = message.match(/Etapa\s+(\d+)\/(\d+):\s*([^()]+)\(arquivo recuperado\)/i);
  if (recoveredMatch) {
    result.stageIndex = Number(recoveredMatch[1]) || 0;
    result.stageTotal = Number(recoveredMatch[2]) || 0;
    result.stageName = String(recoveredMatch[3]).trim();
    result.stagePercent = 100.0;
    result.stageRecovered = true;
  }
  return result;
}

function _getStageState(jobId) {
  if (!state.stageByJob.has(jobId)) {
    state.stageByJob.set(jobId, { order: [], files: new Map(), currentKey: "" });
  }
  return state.stageByJob.get(jobId);
}

function updateStageState(job) {
  const info = parseStageInfo(job.message || "");
  if (!info.stageTotal && !info.stageIndex && !info.fileKey && !job.message) return;
  const st = _getStageState(job.id);
  const key = info.fileKey || "__single__";
  const label = info.fileLabel || "Arquivo";

  if (key !== "__single__") {
    if (st.currentKey && st.currentKey !== key) {
      const prev = st.files.get(st.currentKey);
      if (prev) {
        prev.stageIndex = Math.max(prev.stageIndex || 0, prev.stageTotal || 1);
        prev.stagePercent = 100.0;
      }
    }
    st.currentKey = key;
  }

  if (!st.files.has(key)) {
    st.files.set(key, { key, label, stageIndex: 0, stagePercent: 0, stageTotal: 0, skipped: {}, recovered: {} });
    st.order.push(key);
  }
  const rec = st.files.get(key);
  rec.label = label;
  if (info.stageTotal > 0) rec.stageTotal = info.stageTotal;
  if (info.stageIndex > 0) {
    const prevIndex = rec.stageIndex || 0;
    if (info.stageIndex > prevIndex) {
      rec.stageIndex = info.stageIndex;
      rec.stagePercent = info.stagePercent || 0;
    } else if (info.stageIndex === prevIndex) {
      rec.stagePercent = info.stagePercent || 0;
    }
  }
  if (info.stageSkipped && info.stageIndex > 0) {
    rec.skipped[info.stageIndex] = true;
  }
  if (info.stageRecovered && info.stageIndex > 0) {
    rec.recovered[info.stageIndex] = true;
  }
  if (job.status === "done") {
    rec.stageIndex = Math.max(rec.stageIndex || 0, rec.stageTotal || 1);
    rec.stagePercent = 100.0;
  }
}

function buildStageLine(record, stages, isRunning) {
  const wrap = document.createElement("div");
  wrap.className = "stage-wrap";
  const title = document.createElement("div");
  title.className = "stage-file";
  title.textContent = record.label;
  wrap.appendChild(title);
  const line = document.createElement("div");
  line.className = "stage-line";
  stages.forEach((name, idx) => {
    const n = idx + 1;
    const chip = document.createElement("div");
    chip.className = "stage-chip stage-pending";
    if (record.skipped && record.skipped[n]) chip.className = "stage-chip stage-skipped";
    else if (record.stageIndex > n) chip.className = "stage-chip stage-done";
    else if (record.stageIndex === n && isRunning) chip.className = "stage-chip stage-running";
    if (!(record.skipped && record.skipped[n]) && !isRunning && record.stageIndex >= n) chip.className = "stage-chip stage-done";
    let txt = name;
    if (record.skipped && record.skipped[n]) {
      txt += " (pulado)";
    } else if (record.recovered && record.recovered[n]) {
      txt += " (arquivo recuperado)";
    } else if (record.stageIndex === n && isRunning) {
      txt += ` (${(record.stagePercent || 0).toFixed(1)}%)`;
    }
    chip.textContent = txt;
    line.appendChild(chip);
  });
  wrap.appendChild(line);
  return wrap;
}

function buildStageView(job, stages) {
  const st = _getStageState(job.id);
  if (!st.order.length) {
    const info = parseStageInfo(job.message || "");
    const fallback = {
      label: info.fileLabel || "Arquivo",
      stageIndex: info.stageIndex || 0,
      stagePercent: info.stagePercent || 0,
      stageTotal: info.stageTotal || stages.length,
    };
    return buildStageLine(fallback, stages, job.status === "running");
  }

  const wrap = document.createElement("div");
  wrap.className = "stage-wrap";
  const currentKey = st.currentKey || st.order[st.order.length - 1];
  const current = st.files.get(currentKey);
  if (current) wrap.appendChild(buildStageLine(current, stages, job.status === "running"));

  if (job.mode === "transcribe") {
    const history = st.order.filter(k => k !== currentKey).map(k => st.files.get(k)).filter(Boolean);
    if (history.length) {
      const row = document.createElement("div");
      row.className = "job-actions";
      const btn = document.createElement("button");
      btn.className = "ghost";
      const expanded = state.fileHistoryExpanded.has(job.id);
      btn.textContent = expanded ? `Ocultar historico (${history.length})` : `Mostrar historico (${history.length})`;
      btn.addEventListener("click", () => {
        if (expanded) state.fileHistoryExpanded.delete(job.id);
        else state.fileHistoryExpanded.add(job.id);
        renderJobs();
      });
      row.appendChild(btn);
      wrap.appendChild(row);
      if (expanded) history.forEach(rec => wrap.appendChild(buildStageLine(rec, TRANSCRIBE_STAGES, false)));
    }
  }

  return wrap;
}

function stopLiveInline(jobId) {
  const current = state.liveStreams.get(jobId);
  if (current) {
    current.es.close();
    state.liveStreams.delete(jobId);
  }
}

function startLiveInline(job, liveBox, btn) {
  stopLiveInline(job.id);
  liveBox.textContent = "Aguardando transcricao iniciar...";
  btn.textContent = "Parar ao vivo";

  const es = new EventSource(`/api/transcribe/${job.id}/live`);
  state.liveStreams.set(job.id, { es, box: liveBox, btn });

  es.onmessage = (evt) => {
    const data = JSON.parse(evt.data);
    if (data.line !== undefined) {
      if (liveBox.textContent === "Aguardando transcricao iniciar...") {
        liveBox.textContent = "";
      }
      liveBox.textContent += `${data.line}\n`;
      liveBox.scrollTop = liveBox.scrollHeight;
    }
  };
  es.addEventListener("status", (evt) => {
    const data = JSON.parse(evt.data);
    if (!liveBox.textContent || liveBox.textContent === "Aguardando transcricao iniciar...") {
      liveBox.textContent = data.message || "Aguardando transcricao iniciar...";
    }
  });
  es.onerror = () => {
    es.close();
    if (liveBox.style.display !== "none") {
      setTimeout(() => startLiveInline(job, liveBox, btn), 2000);
    }
  };
}

let initializing = true;

function setMode(mode) {
  state.mode = mode;
  byId("mode-audio").classList.toggle("active", mode === "audio");
  byId("mode-video").classList.toggle("active", mode === "video");
  byId("audio-options").classList.toggle("active", mode === "audio");
  byId("video-options").classList.toggle("active", mode === "video");
  applySettingsToUI();
  if (!initializing) {
    saveSettings();
  }
}

async function loadSettings() {
  const audio = await fetch("/api/settings?mode=audio").then(r => r.json());
  const video = await fetch("/api/settings?mode=video").then(r => r.json());
  state.settings.audio = audio.data || {};
  state.settings.video = video.data || {};
  state.outputDir = audio.output_dir || video.output_dir || "";
  const last = audio.last_mode || "audio";
  byId("output-dir").value = state.outputDir;
  setMode(last);
}

function renderAudioOptions() {
  const container = byId("audio-options");
  container.innerHTML = "";
  container.appendChild(selectField("Formato", "audio-format", ["mp3", "wav", "ogg"]));
  container.appendChild(selectField("Bitrate", "audio-bitrate", ["96", "128", "160", "192", "256", "320", "q5"]));
  container.appendChild(selectField("Modo", "audio-mode", ["cbr", "vbr"]));
  container.appendChild(selectField("Normalizacao de audio", "audio-normalize", [
    { label: "Desativar", value: "off" },
    { label: "Ativar", value: "on" },
  ]));
  container.appendChild(selectField("Preset", "audio-preset", ["", ...audioPresets.map(p => p.label)]));
}

function renderVideoOptions() {
  const container = byId("video-options");
  container.innerHTML = "";
  container.appendChild(selectField("Conteiner", "video-container", ["mp4", "avi", "webm"]));
  container.appendChild(selectField("Codec", "video-codec", ["h264", "h265", "vp9", "av1"]));
  container.appendChild(selectField("Resolucao", "video-resolution", ["best", "720", "1080", "1440", "2160"]));
  container.appendChild(selectField("Bitrate", "video-bitrate", ["auto", "1000", "2000", "4000", "6000", "8000", "12000", "20000", "custom"]));
  container.appendChild(textField("Bitrate custom (kbps)", "video-custom-bitrate"));
  container.appendChild(selectField("Normalizacao de audio", "video-normalize", [
    { label: "Desativar", value: "off" },
    { label: "Ativar", value: "on" },
  ]));
  container.appendChild(selectField("Aceleracao GPU", "video-accel", [
    { label: "Desativar", value: "off" },
    { label: "Auto (usar GPU se disponivel)", value: "auto" },
    { label: "Forcar CUDA (NVIDIA)", value: "cuda" },
  ]));
  const gpuHint = document.createElement("p");
  gpuHint.className = "hint";
  gpuHint.textContent = "Recomendado: Auto para H.264/H.265 com GPU NVIDIA. Use Forcar CUDA apenas se souber que seu FFmpeg tem NVENC.";
  container.appendChild(gpuHint);
  container.appendChild(selectField("CRF (H.264/H.265)", "video-crf", ["", "18", "20", "22", "24", "26", "28"]));
  container.appendChild(selectField("Preset (H.264/H.265)", "video-preset", ["", "ultrafast", "veryfast", "fast", "medium", "slow"]));
}

function selectField(label, id, options) {
  const wrap = document.createElement("div");
  wrap.className = "row";
  const lbl = document.createElement("label");
  lbl.textContent = label;
  const select = document.createElement("select");
  select.id = id;
  options.forEach(opt => {
    const o = document.createElement("option");
    o.value = opt?.value ?? opt;
    o.textContent = (opt?.label ?? opt) || "(nenhum)";
    select.appendChild(o);
  });
  select.addEventListener("change", onOptionChange);
  wrap.appendChild(lbl);
  wrap.appendChild(select);
  return wrap;
}

function textField(label, id) {
  const wrap = document.createElement("div");
  wrap.className = "row";
  const lbl = document.createElement("label");
  lbl.textContent = label;
  const input = document.createElement("input");
  input.id = id;
  input.addEventListener("input", onOptionChange);
  wrap.appendChild(lbl);
  wrap.appendChild(input);
  return wrap;
}

function applySettingsToUI() {
  const opts = state.mode === "audio" ? { ...audioDefaults, ...state.settings.audio } : { ...videoDefaults, ...state.settings.video };

  if (state.mode === "audio") {
    byId("audio-format").value = opts.format || "mp3";
    byId("audio-bitrate").value = opts.bitrate || "192";
    byId("audio-mode").value = opts.audio_mode || "cbr";
    byId("audio-normalize").value = opts.normalize || "off";
    byId("audio-preset").value = opts.preset || "";
  } else {
    byId("video-container").value = opts.container || "mp4";
    byId("video-codec").value = opts.codec || "h264";
    byId("video-resolution").value = opts.resolution || "best";
    byId("video-bitrate").value = opts.bitrate || "auto";
    byId("video-custom-bitrate").value = opts.custom_bitrate || "";
    byId("video-normalize").value = opts.normalize || "off";
    byId("video-accel").value = opts.video_accel || "off";
    byId("video-crf").value = opts.crf || "";
    byId("video-preset").value = opts.preset || "";
  }
  if (byId("yt-client")) {
    byId("yt-client").value = opts.yt_client || "tv";
  }
  if (byId("yt-po-token")) {
    byId("yt-po-token").value = opts.po_token || "";
  }
  if (byId("yt-cookies")) {
    byId("yt-cookies").value = opts.use_cookies || "on";
  }
  if (byId("yt-cookies-file")) {
    byId("yt-cookies-file").value = opts.cookies_file || "";
  }
  if (byId("yt-js-runtime")) {
    let runtime = opts.js_runtime || "";
    if (runtime.startsWith("nodejs:")) {
      runtime = runtime.replace("nodejs:", "node:");
      byId("yt-js-runtime").value = runtime;
      saveSettings();
    } else {
      byId("yt-js-runtime").value = runtime;
    }
  }
  if (byId("yt-remote-components")) {
    byId("yt-remote-components").value = opts.remote_components || "on";
  }
  toggleCustomBitrate();
  toggleAdvancedVideo();
}

function onOptionChange() {
  if (state.mode === "audio") {
    const preset = byId("audio-preset").value;
    if (preset) {
      const chosen = audioPresets.find(p => p.label === preset);
      if (chosen) {
        byId("audio-format").value = chosen.format;
        byId("audio-bitrate").value = chosen.bitrate;
        byId("audio-mode").value = chosen.audio_mode;
      }
    }
  } else {
    toggleCustomBitrate();
    toggleAdvancedVideo();
  }
  saveSettings();
}

function toggleCustomBitrate() {
  const row = byId("video-custom-bitrate").parentElement;
  if (!row) return;
  row.style.display = byId("video-bitrate").value === "custom" ? "grid" : "none";
}

function toggleAdvancedVideo() {
  const codec = byId("video-codec").value;
  const show = codec === "h264" || codec === "h265";
  byId("video-crf").parentElement.style.display = show ? "grid" : "none";
  byId("video-preset").parentElement.style.display = show ? "grid" : "none";
}

function gatherOptions() {
  if (state.mode === "audio") {
    return {
      mode: "audio",
      format: byId("audio-format").value,
      bitrate: byId("audio-bitrate").value,
      audio_mode: byId("audio-mode").value,
      normalize: byId("audio-normalize")?.value || "off",
      yt_client: byId("yt-client")?.value || "tv",
      po_token: byId("yt-po-token")?.value || "",
      use_cookies: byId("yt-cookies")?.value || "on",
      cookies_file: byId("yt-cookies-file")?.value || "",
      js_runtime: byId("yt-js-runtime")?.value || "",
      remote_components: byId("yt-remote-components")?.value || "on",
    };
  }
  return {
    mode: "video",
    container: byId("video-container").value,
    codec: byId("video-codec").value,
    resolution: byId("video-resolution").value,
    bitrate: byId("video-bitrate").value,
    custom_bitrate: byId("video-custom-bitrate").value,
    normalize: byId("video-normalize")?.value || "off",
    video_accel: byId("video-accel")?.value || "off",
    crf: byId("video-crf").value,
    preset: byId("video-preset").value,
    yt_client: byId("yt-client")?.value || "tv",
    po_token: byId("yt-po-token")?.value || "",
    use_cookies: byId("yt-cookies")?.value || "on",
    cookies_file: byId("yt-cookies-file")?.value || "",
    js_runtime: byId("yt-js-runtime")?.value || "",
    remote_components: byId("yt-remote-components")?.value || "on",
  };
}

async function saveSettings() {
  const options = gatherOptions();
  if (state.mode === "audio") {
    state.settings.audio = { ...options };
  } else {
    state.settings.video = { ...options };
  }
  state.outputDir = byId("output-dir").value;
  const res = await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      mode: state.mode,
      data: options,
      output_dir: state.outputDir,
      last_mode: state.mode,
    }),
  });
  const data = await res.json().catch(() => ({}));
  if (!data.ok) {
    logClient("error", `settings save falhou: ${data.error || res.status}`);
  } else {
    logClient("info", "settings salvas");
  }
}

function addCard(value = "") {
  const list = byId("card-list");
  const wrap = document.createElement("div");
  wrap.className = "card-item";
  const input = document.createElement("input");
  input.value = value;
  const remove = document.createElement("button");
  remove.textContent = "Remover";
  remove.className = "ghost";
  remove.addEventListener("click", () => wrap.remove());
  wrap.appendChild(input);
  wrap.appendChild(remove);
  list.appendChild(wrap);
}

function collectItems() {
  const items = [];
  const text = byId("links-text").value || "";
  text.split("\n").forEach(line => {
    const trimmed = line.trim();
    if (trimmed) items.push(trimmed);
  });
  document.querySelectorAll("#card-list input").forEach(input => {
    const trimmed = input.value.trim();
    if (trimmed) items.push(trimmed);
  });
  return items;
}

async function startQueue() {
  const items = collectItems();
  if (!items.length) {
    alert("Adicione ao menos um item");
    return;
  }
  const options = gatherOptions();
  const res = await fetch("/api/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items, options, output_dir: byId("output-dir").value }),
  });
  const data = await res.json();
  if (!data.ok) {
    logClient("error", `start-queue falhou: ${data.error || res.status}`);
    alert(data.error || "Falha ao iniciar fila");
  } else {
    logClient("info", `start-queue ok: ${data.ids?.length || 0} jobs`);
  }
}

async function openFolder() {
  // "Abrir pasta" na aba Download agora funciona como seletor de pasta.
  const res = await fetch("/api/browse-folder", { method: "POST" });
  const data = await res.json().catch(() => ({}));
  if (!data.ok) {
    alert(data.error || "Falha ao escolher pasta");
    return;
  }
  byId("output-dir").value = data.path;
  await saveSettings();
  logClient("info", `pasta definida: ${data.path}`);
}

async function browseFolder() {
  const res = await fetch("/api/browse-folder", { method: "POST" });
  const data = await res.json();
  if (!data.ok) {
    alert(data.error || "Falha ao escolher pasta");
    return;
  }
  byId("output-dir").value = data.path;
  await saveSettings();
  logClient("info", `pasta definida: ${data.path}`);
}

async function browseCookies() {
  const res = await fetch("/api/browse-cookies", { method: "POST" });
  const data = await res.json();
  if (!data.ok) {
    alert(data.error || "Falha ao escolher cookies");
    return;
  }
  byId("yt-cookies-file").value = data.path;
  await saveSettings();
  logClient("info", `cookies definidos: ${data.path}`);
}

async function repeatJob(id) {
  const res = await fetch(`/api/jobs/${id}/repeat`, { method: "POST" });
  const data = await res.json();
  if (!data.ok) {
    alert(data.error || "Nao foi possivel repetir");
  }
}

async function deleteJob(id) {
  const res = await fetch(`/api/jobs/${id}/delete`, { method: "POST" });
  const data = await res.json();
  if (!data.ok) {
    alert(data.error || "Nao foi possivel remover");
  } else {
    logClient("info", `job removido: ${id}`);
    loadJobs();
  }
}

async function pauseJob(id) {
  const res = await fetch(`/api/jobs/${id}/pause`, { method: "POST" });
  const data = await res.json();
  if (!data.ok) {
    alert(data.error || "Nao foi possivel pausar");
  }
}

async function resumeJob(id) {
  const res = await fetch(`/api/jobs/${id}/resume`, { method: "POST" });
  const data = await res.json();
  if (!data.ok) {
    alert(data.error || "Nao foi possivel retomar");
  }
}

async function startNow(id) {
  const res = await fetch(`/api/jobs/${id}/start-now`, { method: "POST" });
  const data = await res.json();
  if (!data.ok) {
    alert(data.error || "Nao foi possivel priorizar");
  }
}

function showDetails(job) {
  const modal = byId("job-modal");
  const body = byId("job-modal-body");
  body.textContent = JSON.stringify(job, null, 2);
  modal.classList.add("open");
}

function renderJob(job) {
  const div = document.createElement("div");
  div.className = `job ${job.status}`;
  div.id = `job-${job.id}`;
  const header = document.createElement("div");
  header.className = "job-header";
  const label = job.title ? `${job.title} - ${job.channel || ""}` : (job.url || "");
  header.innerHTML = `<strong>${job.status}</strong><span>${label}</span>`;
  const bar = document.createElement("div");
  bar.className = "progress-bar";
  const span = document.createElement("span");
  span.style.width = `${job.percent || 0}%`;
  bar.appendChild(span);
  const meta = document.createElement("div");
  meta.className = "job-meta";
  const metrics = parseTranscribeMetrics(job.speed);
  const stageInfo = parseStageInfo(job.message || "");
  const totalPercent = Number.isFinite(Number(job.percent)) ? Number(job.percent).toFixed(1) : String(job.percent || 0);
  const stagePercent = stageInfo && Number.isFinite(Number(stageInfo.stagePercent))
    ? `${Number(stageInfo.stagePercent).toFixed(1)}%`
    : "-";
  if (job.mode === "transcribe" && metrics) {
    meta.innerHTML = `
      <div>Mensagem: ${job.message || "-"}</div>
      <div>% total: ${totalPercent}</div>
      <div>% etapa: ${stagePercent}</div>
      <div>ETA: ${humanTime(metrics.etaSeconds)}</div>
      <div>Tempo atual: ${humanTime(metrics.elapsedSeconds)}</div>
      <div>Falta (audio): ${humanTime(metrics.remainingAudioSeconds)}</div>
      <div>Audio atual: ${humanTime(metrics.currentAudioSeconds)}</div>
      <div>Audio total: ${humanTime(metrics.totalAudioSeconds)}</div>
    `;
  } else if (job.mode === "transcribe") {
    meta.innerHTML = `
      <div>Mensagem: ${job.message || "-"}</div>
      <div>% total: ${totalPercent}</div>
      <div>% etapa: ${stagePercent}</div>
      <div>Velocidade: ${job.speed || "-"}</div>
      <div>ETA: ${humanTime(job.eta)}</div>
    `;
  } else {
    meta.innerHTML = `
      <div>Mensagem: ${job.message || "-"}</div>
      <div>%: ${totalPercent}</div>
      <div>Velocidade: ${job.speed || "-"}</div>
      <div>ETA: ${humanTime(job.eta)}</div>
      <div>Tamanho: ${humanBytes(job.size)}</div>
    `;
  }
  const actions = document.createElement("div");
  actions.className = "job-actions";
  let liveBox = null;
  let liveBtn = null;
  if (job.mode === "transcribe") {
    liveBtn = document.createElement("button");
    liveBtn.textContent = "Transcricao ao vivo";
    liveBtn.className = "ghost";
    liveBox = document.createElement("pre");
    liveBox.className = "live-box";
    liveBox.style.display = "none";
    liveBox.textContent = "Aguardando transcricao iniciar...";
    liveBtn.addEventListener("click", () => {
      if (liveBox.style.display === "none") {
        liveBox.style.display = "block";
        startLiveInline(job, liveBox, liveBtn);
      } else {
        liveBox.style.display = "none";
        stopLiveInline(job.id);
        liveBtn.textContent = "Transcricao ao vivo";
      }
    });
    actions.appendChild(liveBtn);
    const redoConvert = document.createElement("button");
    redoConvert.textContent = "Refazer Conversao";
    redoConvert.className = "ghost";
    redoConvert.addEventListener("click", async () => {
      const res = await fetch(`/api/jobs/${job.id}/redo/convert`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!data.ok) alert(data.error || "Falha ao refazer conversao");
    });
    const redoNorm = document.createElement("button");
    redoNorm.textContent = "Refazer Normalizacao";
    redoNorm.className = "ghost";
    redoNorm.addEventListener("click", async () => {
      const res = await fetch(`/api/jobs/${job.id}/redo/normalize`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!data.ok) alert(data.error || "Falha ao refazer normalizacao");
    });
    const redoVad = document.createElement("button");
    redoVad.textContent = "Refazer VAD";
    redoVad.className = "ghost";
    redoVad.addEventListener("click", async () => {
      const res = await fetch(`/api/jobs/${job.id}/redo/vad`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!data.ok) alert(data.error || "Falha ao refazer VAD");
    });
    const redoTrans = document.createElement("button");
    redoTrans.textContent = "Refazer Transcricao";
    redoTrans.className = "ghost";
    redoTrans.addEventListener("click", async () => {
      const res = await fetch(`/api/jobs/${job.id}/redo/transcribe`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!data.ok) alert(data.error || "Falha ao refazer transcricao");
    });
    const redoMerge = document.createElement("button");
    redoMerge.textContent = "Refazer Juncao";
    redoMerge.className = "ghost";
    redoMerge.addEventListener("click", async () => {
      const res = await fetch(`/api/jobs/${job.id}/redo/merge`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!data.ok) alert(data.error || "Falha ao refazer juncao");
    });
    const okBtn = document.createElement("button");
    okBtn.textContent = "OK";
    okBtn.className = "ghost";
    okBtn.addEventListener("click", async () => {
      const res = await fetch(`/api/jobs/${job.id}/ok`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!data.ok) alert(data.error || "Nao foi possivel finalizar");
    });
    const openResult = document.createElement("button");
    openResult.textContent = "Ver Resultado";
    openResult.className = "ghost";
    openResult.addEventListener("click", async () => {
      const res = await fetch(`/api/jobs/${job.id}/open-result`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!data.ok) alert(data.error || "Resultado indisponivel");
    });
    actions.appendChild(redoConvert);
    actions.appendChild(redoNorm);
    actions.appendChild(redoVad);
    actions.appendChild(redoTrans);
    actions.appendChild(redoMerge);
    actions.appendChild(openResult);
    actions.appendChild(okBtn);
  }
  const cancel = document.createElement("button");
  cancel.textContent = "Cancelar item";
  cancel.className = "ghost";
  cancel.addEventListener("click", () => cancelJob(job.id));
  const start = document.createElement("button");
  start.textContent = "Iniciar agora";
  start.className = "ghost";
  start.addEventListener("click", () => startNow(job.id));
  const pause = document.createElement("button");
  pause.textContent = "Pausar";
  pause.className = "ghost";
  pause.addEventListener("click", () => pauseJob(job.id));
  const resume = document.createElement("button");
  resume.textContent = "Retomar";
  resume.className = "ghost";
  resume.addEventListener("click", () => resumeJob(job.id));
  const repeat = document.createElement("button");
  repeat.textContent = "Repetir";
  repeat.className = "ghost";
  repeat.addEventListener("click", () => repeatJob(job.id));
  const remove = document.createElement("button");
  remove.textContent = "Remover";
  remove.className = "ghost";
  remove.addEventListener("click", () => deleteJob(job.id));
  const details = document.createElement("button");
  details.textContent = "Ver detalhes";
  details.className = "ghost";
  details.addEventListener("click", () => showDetails(job));
  actions.appendChild(cancel);
  actions.appendChild(start);
  actions.appendChild(pause);
  actions.appendChild(resume);
  actions.appendChild(repeat);
  actions.appendChild(remove);
  actions.appendChild(details);
  div.appendChild(header);
  if (job.status !== "group") {
    div.appendChild(bar);
    div.appendChild(meta);
    const stageInfo = parseStageInfo(job.message || "");
    const hasStageState = state.stageByJob.has(job.id);
    if (stageInfo.stageTotal > 0 || hasStageState) {
      const stages = job.mode === "transcribe" ? TRANSCRIBE_STAGES : DOWNLOAD_STAGES;
      div.appendChild(buildStageView(job, stages));
    }
    div.appendChild(actions);
    if (liveBox) {
      div.appendChild(liveBox);
    }
  }
  return div;
}

function updateJob(job) {
  if (!job || !job.id) return;
  updateStageState(job);
  state.jobs.set(job.id, job);
  if (job.mode === "transcribe" && job.status === "error" && !state.notifiedErrors.has(job.id)) {
    state.notifiedErrors.add(job.id);
    showErrorToast("Erro detectado, consulte o log.");
  }
  if (job.parent_job_id) {
    const children = state.groups.get(job.parent_job_id) || [];
    const existing = children.findIndex(j => j.id === job.id);
    if (existing >= 0) children[existing] = job;
    else children.push(job);
    state.groups.set(job.parent_job_id, children);
  }
  if (job.status === "group") {
    state.groups.set(job.id, state.groups.get(job.id) || []);
  }
  renderJobs();
}

function renderJobs() {
  const container = byId("jobs");
  container.innerHTML = "";
  const jobs = Array.from(state.jobs.values());
  const parents = jobs.filter(j => j.status === "group");
  const standalones = jobs.filter(j => !j.parent_job_id && j.status !== "group");
  const orphans = jobs.filter(j => j.parent_job_id && !parents.find(p => p.id === j.parent_job_id));
  const summary = {
    total: jobs.filter(j => j.status !== "group").length,
    queued: jobs.filter(j => j.status === "queued").length,
    running: jobs.filter(j => j.status === "running").length,
    paused: jobs.filter(j => j.status === "paused").length,
    done: jobs.filter(j => j.status === "done").length,
    error: jobs.filter(j => j.status === "error").length,
    canceled: jobs.filter(j => j.status === "canceled").length,
  };
  const summaryEl = byId("status-summary");
  if (summaryEl) {
    summaryEl.textContent = `Total: ${summary.total} | Na fila: ${summary.queued} | Rodando: ${summary.running} | Pausados: ${summary.paused} | OK: ${summary.done} | Erro: ${summary.error} | Cancelados: ${summary.canceled}`;
  }
  updateStatusAccelIndicator(jobs);

  parents.forEach(parent => {
    const wrap = document.createElement("div");
    wrap.className = "job-group";
    const header = document.createElement("div");
    header.className = "job-group-header";
    const children = state.groups.get(parent.id) || [];
    const done = children.filter(c => c.status === "done").length;
    const running = children.filter(c => c.status === "running").length;
    const failed = children.filter(c => c.status === "error").length;
    header.innerHTML = `<strong>Grupo</strong><span>${parent.url || ""}</span><span>${children.length} itens â€¢ ${done} ok â€¢ ${running} rodando â€¢ ${failed} erro</span>`;
    const total = children.length || 0;
    let groupPercent = 0;
    if (total > 0) {
      groupPercent = (done / total) * 100;
      if (running > 0) {
        const runningChild = children.find(c => c.status === "running");
        if (runningChild && typeof runningChild.percent === "number") {
          groupPercent += (runningChild.percent / total);
        }
      }
    }
    const groupProgress = document.createElement("div");
    groupProgress.className = "group-progress";
    const groupProgressBar = document.createElement("div");
    groupProgressBar.className = "group-progress-bar";
    groupProgressBar.style.width = `${Math.min(100, Math.max(0, groupPercent)).toFixed(1)}%`;
    groupProgress.appendChild(groupProgressBar);
    const groupProgressText = document.createElement("div");
    groupProgressText.className = "group-progress-text";
    const runningChild = children.find(c => c.status === "running");
    groupProgressText.textContent = total
      ? `Progresso do grupo: ${groupPercent.toFixed(1)}% (${done}/${total})${runningChild ? ` â€¢ atual: ${runningChild.title || runningChild.url || runningChild.id}` : ""}`
      : "Grupo vazio";
    const toggle = document.createElement("button");
    toggle.className = "ghost";
    toggle.textContent = "Expandir";
    const startGroup = document.createElement("button");
    startGroup.className = "ghost";
    startGroup.textContent = "Iniciar grupo";
    startGroup.addEventListener("click", async () => {
      const res = await fetch(`/api/jobs/${parent.id}/start-group`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!data.ok) {
        alert(data.error || "Nao foi possivel iniciar grupo");
      }
    });
    const remove = document.createElement("button");
    remove.className = "ghost";
    remove.textContent = "Remover grupo";
    remove.addEventListener("click", () => deleteJob(parent.id));
    const list = document.createElement("div");
    list.className = "job-group-list";
    children.forEach(child => list.appendChild(renderJob(child)));
    toggle.addEventListener("click", () => {
      list.classList.toggle("open");
      toggle.textContent = list.classList.contains("open") ? "Recolher" : "Expandir";
    });
    wrap.appendChild(header);
    wrap.appendChild(groupProgress);
    wrap.appendChild(groupProgressText);
    wrap.appendChild(toggle);
    wrap.appendChild(startGroup);
    wrap.appendChild(remove);
    wrap.appendChild(list);
    container.appendChild(wrap);
  });

  standalones.forEach(job => container.appendChild(renderJob(job)));
  orphans.forEach(job => container.appendChild(renderJob(job)));
}

function applyDefaultFolder() {
  const input = byId("output-dir");
  if (!input.value) {
    input.value = "C:\\";
  }
}

function applyDefaultJsRuntime() {
  const input = byId("yt-js-runtime");
  if (input && !input.value) {
    input.value = "node:C:\\Program Files\\nodejs\\node.exe";
  }
}

function applyDefaultYtSettings() {
  const cookiesFile = byId("yt-cookies-file");
  if (cookiesFile && (!cookiesFile.value || cookiesFile.value === "C:\\")) {
    cookiesFile.value = "C:\\midia-faren\\data\\www.youtube.com_cookies.txt";
  }
  const ytClient = byId("yt-client");
  if (ytClient && ytClient.value !== "tv") {
    ytClient.value = "tv";
  }
  const ytCookies = byId("yt-cookies");
  if (ytCookies && ytCookies.value !== "on") {
    ytCookies.value = "on";
  }
  const ytRemote = byId("yt-remote-components");
  if (ytRemote && ytRemote.value !== "on") {
    ytRemote.value = "on";
  }
}

async function cancelJob(id) {
  await fetch(`/api/jobs/${id}/cancel`, { method: "POST" });
}

async function loadJobs() {
  const jobs = await fetch("/api/jobs").then(r => r.json());
  state.jobs.clear();
  state.groups.clear();
  jobs.forEach(updateJob);
}

function setupSSE() {
  if (state.sse) state.sse.close();
  const es = new EventSource("/api/stream");
  state.sse = es;
  es.onmessage = (evt) => {
    const payload = JSON.parse(evt.data);
    if (payload.type === "job") {
      if (!payload.job || !payload.job.id) return;
      updateJob(payload.job);
    } else if (payload.type === "job_removed") {
      state.jobs.delete(payload.job_id);
      state.stageByJob.delete(payload.job_id);
      state.fileHistoryExpanded.delete(payload.job_id);
      stopLiveInline(payload.job_id);
      renderJobs();
    }
  };
  es.onerror = () => {
    es.close();
    setTimeout(setupSSE, 2000);
  };
}

function initModal() {
  const modal = byId("job-modal");
  byId("job-modal-close").addEventListener("click", () => modal.classList.remove("open"));
}

function init() {
  logClient("info", "app.js iniciado");
  logElementPresence([
    "mode-audio",
    "mode-video",
    "add-card",
    "start-queue",
    "clear-all",
    "open-folder",
    "browse-folder",
    "output-dir",
    "links-text",
    "card-list",
    "pause-queue",
    "resume-queue",
    "cancel-queue",
    "clear-queue",
    "status-summary",
    "status-accel-indicator",
    "status-accel-change",
    "jobs",
    "yt-client",
    "yt-po-token",
    "yt-cookies",
    "yt-cookies-file",
    "yt-js-runtime",
  ]);
  renderAudioOptions();
  renderVideoOptions();
  byId("mode-audio").addEventListener("click", () => {
    logClick("mode-audio");
    setMode("audio");
  });
  byId("mode-video").addEventListener("click", () => {
    logClick("mode-video");
    setMode("video");
  });
  byId("add-card").addEventListener("click", () => {
    logClick("add-card");
    addCard("");
  });
  byId("start-queue").addEventListener("click", () => {
    logClick("start-queue");
    startQueue();
  });
  const resetBtn = byId("reset-log");
  if (resetBtn) {
    resetBtn.addEventListener("click", async () => {
      logClick("reset-log");
      const res = await fetch("/api/reset-log", { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!data.ok) {
        alert(data.error || "Falha ao limpar log");
      }
    });
  }
  byId("clear-all").addEventListener("click", () => {
    logClick("clear-all");
    byId("links-text").value = "";
    byId("card-list").innerHTML = "";
  });
  byId("open-folder").addEventListener("click", () => {
    logClick("open-folder");
    openFolder();
  });
  byId("browse-folder").addEventListener("click", () => {
    logClick("browse-folder");
    browseFolder();
  });
  const pickCookies = byId("pick-cookies");
  if (pickCookies) {
    pickCookies.addEventListener("click", () => {
      logClick("pick-cookies");
      browseCookies();
    });
  }
  const pauseBtn = byId("pause-queue");
  const resumeBtn = byId("resume-queue");
  if (pauseBtn) {
    pauseBtn.addEventListener("click", async () => {
      logClick("pause-queue");
      await fetch("/api/queue/pause", { method: "POST" });
    });
  }
  if (resumeBtn) {
    resumeBtn.addEventListener("click", async () => {
      logClick("resume-queue");
      await fetch("/api/queue/resume", { method: "POST" });
    });
  }
  const cancelQueueBtn = byId("cancel-queue");
  if (cancelQueueBtn) {
    cancelQueueBtn.addEventListener("click", async () => {
      logClick("cancel-queue");
      await fetch("/api/queue/cancel-all", { method: "POST" });
      await loadJobs();
    });
  }
  const clearQueueBtn = byId("clear-queue");
  if (clearQueueBtn) {
    clearQueueBtn.addEventListener("click", async () => {
      logClick("clear-queue");
      await fetch("/api/queue/clear-all", { method: "POST" });
      await loadJobs();
    });
  }
  byId("output-dir").addEventListener("change", () => {
    logClient("info", "output-dir alterado");
    saveSettings();
  });
  const ytClient = byId("yt-client");
  if (ytClient) ytClient.addEventListener("change", saveSettings);
  const ytPo = byId("yt-po-token");
  if (ytPo) ytPo.addEventListener("input", saveSettings);
  const ytCookies = byId("yt-cookies");
  if (ytCookies) ytCookies.addEventListener("change", saveSettings);
  const ytCookiesFile = byId("yt-cookies-file");
  if (ytCookiesFile) ytCookiesFile.addEventListener("input", saveSettings);
  const ytJs = byId("yt-js-runtime");
  if (ytJs) ytJs.addEventListener("input", saveSettings);
  const ytRemote = byId("yt-remote-components");
  if (ytRemote) ytRemote.addEventListener("change", saveSettings);
  addCard("");
  initModal();
  loadSettings().then(() => {
    logClient("info", "settings carregadas");
    applyDefaultFolder();
    applyDefaultYtSettings();
    applyDefaultJsRuntime();
    initializing = false;
    return saveSettings();
  }).then(() => {
    return loadJobs();
  }).then(() => {
    logClient("info", "jobs carregados");
    setupSSE();
  }).catch((err) => {
    logClient("error", `Falha init: ${err}`);
  });
}

document.addEventListener("DOMContentLoaded", init);
