const state = {
  mode: "audio",
  settings: { audio: {}, video: {}, image: {}, mixagem: {} },
  outputDir: "",
  files: [],
  selectedFileKeys: new Set(),
  archiveEntries: new Map(),
  archiveExpanded: new Set(),
  sse: null,
  jobs: new Map(),
  showAllJobs: false,
  accelLastMode: "",
  accelChangeText: "",
  accelChangeUntil: 0,
};
const JOB_PREVIEW_LIMIT = 8;
const ACCEL_CHANGE_VISIBLE_MS = 12000;
const PROCESS_ACCEL_LABELS = {
  cpu: "Processo Usando CPU",
  gpu_no_cuda: "Processo Usando GPU sem CUDA",
  gpu_cuda: "Processo Usando GPU Com CUDA",
};

let initializing = true;
window.__convert_init_ok = true;

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
  strip_audio: "off",
  preset: "",
  crf: "",
  resize_width: "",
  resize_height: "",
  resize_mode: "contain",
};

const imageDefaults = {
  mode: "image",
  image_format: "jpg",
  image_quality: "85",
  image_width: "",
  image_height: "",
  image_resize_mode: "contain",
  preset: "",
};

const mixDefaults = {
  mode: "mixagem",
  mix_output_format: "m4a",
  mix_target_bitrate_kbps: "96",
  mix_max_size_mb: "190",
  normalize: "off",
  preset: "",
};

const audioPresets = [
  { label: "MP3 128k CBR", format: "mp3", bitrate: "128", audio_mode: "cbr" },
  { label: "MP3 192k CBR", format: "mp3", bitrate: "192", audio_mode: "cbr" },
  { label: "MP3 320k CBR", format: "mp3", bitrate: "320", audio_mode: "cbr" },
  { label: "OGG VBR q5", format: "ogg", bitrate: "q5", audio_mode: "vbr" },
  { label: "WAV 16k Mono", format: "wav", bitrate: "192", audio_mode: "cbr" },
];

const videoPresets = [
  { label: "YouTube 1080p H.264", container: "mp4", codec: "h264", resolution: "1080", bitrate: "auto", strip_audio: "off" },
  { label: "YouTube 720p H.264", container: "mp4", codec: "h264", resolution: "720", bitrate: "auto", strip_audio: "off" },
  { label: "TikTok 1080x1920", container: "mp4", codec: "h264", resolution: "best", bitrate: "auto", resize_width: "1080", resize_height: "1920", resize_mode: "cover", strip_audio: "off" },
  { label: "Instagram 1080x1350", container: "mp4", codec: "h264", resolution: "best", bitrate: "auto", resize_width: "1080", resize_height: "1350", resize_mode: "cover", strip_audio: "off" },
  { label: "WebM VP9 1080p", container: "webm", codec: "vp9", resolution: "1080", bitrate: "auto", strip_audio: "off" },
];
const VIDEO_CODEC_BY_CONTAINER = {
  mp4: ["h264", "h265", "av1"],
  avi: ["h264", "h265"],
  webm: ["vp9", "av1"],
};

const imagePresets = [
  { label: "JPG 85%", image_format: "jpg", image_quality: "85" },
  { label: "PNG Lossless", image_format: "png" },
  { label: "WEBP 80%", image_format: "webp", image_quality: "80" },
  { label: "AVIF 60%", image_format: "avif", image_quality: "60" },
];

const mixPresets = [
  {
    label: "Mixagem padrao (recomendado)",
    mix_output_format: "m4a",
    mix_target_bitrate_kbps: "96",
    mix_max_size_mb: "190",
    normalize: "off",
  },
  {
    label: "Mixagem qualidade alta",
    mix_output_format: "m4a",
    mix_target_bitrate_kbps: "128",
    mix_max_size_mb: "190",
    normalize: "on",
  },
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

function formatEta(seconds) {
  if (seconds === null || seconds === undefined || seconds === "-") return "-";
  const s = Number(seconds);
  if (!Number.isFinite(s) || s < 0) return "-";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = Math.floor(s % 60);
  if (h > 0) return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  return `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}

function formatSize(bytes) {
  if (bytes === null || bytes === undefined || bytes === "-") return "-";
  const b = Number(bytes);
  if (!Number.isFinite(b) || b < 0) return "-";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = b;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(1)} ${units[i]}`;
}

function fileKey(file) {
  return `${file?.name || ""}-${file?.size || 0}-${file?.lastModified || 0}`;
}

function hasZipInSelection(files) {
  return Array.from(files || []).some((file) => {
    const name = String(file?.name || "").toLowerCase();
    return name.endsWith(".zip") || name.endsWith(".aup.zip");
  });
}

const CONVERT_STAGES = ["Preparando", "Processando", "Finalizando"];
const MIX_STAGES = ["Extração", "Preparação", "Mixagem", "Exportação"];
const JOB_STATUS_LABELS = {
  queued: "Na fila",
  running: "Em andamento",
  done: "Concluido",
  error: "Erro",
  canceled: "Cancelado",
  paused: "Pausado",
  group: "Grupo",
};

function parseStageFromMessage(message) {
  const text = String(message || "");
  const match = text.match(/Etapa\s+(\d+)\/(\d+):\s*([^()]+)\(([\d.]+)%\)/i);
  if (!match) return null;
  return {
    stageIndex: Number(match[1]) || 0,
    stageTotal: Number(match[2]) || 0,
    stageName: String(match[3] || "").trim(),
    stagePercent: Number(match[4]) || 0,
  };
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

function parseMixMetrics(rawSpeed) {
  const text = String(rawSpeed || "");
  if (!text.startsWith("mix:")) return null;
  const parts = text.slice(4).split("|");
  if (parts.length < 8) return null;
  const elapsed = Number(parts[0]);
  const remaining = Number(parts[1]);
  const totalEstimated = Number(parts[2]);
  const overallPercent = Number(parts[3]);
  const stageIndex = Number(parts[4]);
  const stageTotal = Number(parts[5]);
  const stagePercent = Number(parts[6]);
  const eta = Number(parts[7]);
  const stalled = Number(parts[8] ?? "0");
  if ([elapsed, overallPercent, stageIndex, stageTotal, stagePercent].some((n) => !Number.isFinite(n))) return null;
  return {
    elapsedSeconds: Math.max(0, elapsed),
    remainingSeconds: Number.isFinite(remaining) && remaining >= 0 ? remaining : null,
    totalEstimatedSeconds: Number.isFinite(totalEstimated) && totalEstimated >= 0 ? totalEstimated : null,
    overallPercent: Math.max(0, Math.min(100, overallPercent)),
    stageIndex: Math.max(0, Math.floor(stageIndex)),
    stageTotal: Math.max(0, Math.floor(stageTotal)),
    stagePercent: Math.max(0, Math.min(100, stagePercent)),
    etaSeconds: Number.isFinite(eta) && eta >= 0 ? eta : null,
    stalledSeconds: Number.isFinite(stalled) && stalled >= 0 ? stalled : 0,
  };
}

function buildConvertStageView(job) {
  const configuredStages = (job.mode === "mixagem" || job.mode === "craig_notebook") ? MIX_STAGES : CONVERT_STAGES;
  const wrap = document.createElement("div");
  wrap.className = "stage-wrap";
  const line = document.createElement("div");
  line.className = "stage-line";
  const fromMessage = parseStageFromMessage(job.message);
  const p = Number(job.percent || 0);
  const stageTotal = configuredStages.length;
  let active = Math.max(1, Math.min(stageTotal, Math.ceil((p / 100) * stageTotal)));
  if (fromMessage && fromMessage.stageIndex > 0) active = Math.max(1, Math.min(stageTotal, fromMessage.stageIndex));
  configuredStages.forEach((name, idx) => {
    const n = idx + 1;
    const chip = document.createElement("div");
    chip.className = "stage-chip stage-pending";
    let stagePct = 0.0;
    if (job.status === "done") {
      chip.className = "stage-chip stage-done";
      stagePct = 100.0;
    } else if (n < active) {
      chip.className = "stage-chip stage-done";
      stagePct = 100.0;
    } else if (n === active) {
      chip.className = job.status === "running" ? "stage-chip stage-running" : chip.className;
      if (fromMessage && fromMessage.stageIndex === n) {
        stagePct = Number(fromMessage.stagePercent || 0);
      } else {
        const span = 100.0 / Math.max(1, stageTotal);
        const stageStart = span * idx;
        stagePct = Math.max(0, Math.min(100, ((p - stageStart) / span) * 100.0));
      }
    } else if (job.status === "error" || job.status === "canceled") {
      stagePct = 0.0;
    }
    const txt = `${name} (${Math.max(0, Math.min(100, stagePct)).toFixed(1)}%)`;
    chip.textContent = txt;
    line.appendChild(chip);
  });
  wrap.appendChild(line);
  return wrap;
}

window.addEventListener("error", (event) => {
  const msg = event?.error?.stack || event?.message || "Erro JS";
  logClient("error", msg);
});

window.addEventListener("unhandledrejection", (event) => {
  logClient("error", `UnhandledPromise: ${event.reason}`);
});

function selectField(label, id, options) {
  const wrap = document.createElement("div");
  wrap.className = "row";
  const lbl = document.createElement("label");
  lbl.textContent = label;
  const select = document.createElement("select");
  select.id = id;
  options.forEach(opt => {
    const o = document.createElement("option");
    o.value = opt.value ?? opt;
    o.textContent = (opt.label ?? opt) || "(nenhum)";
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

function checkboxField(label, id) {
  const wrap = document.createElement("div");
  wrap.className = "row";
  const checkWrap = document.createElement("label");
  checkWrap.className = "checkbox-label";
  const input = document.createElement("input");
  input.id = id;
  input.type = "checkbox";
  input.addEventListener("change", onOptionChange);
  const span = document.createElement("span");
  span.textContent = label;
  checkWrap.appendChild(input);
  checkWrap.appendChild(span);
  wrap.appendChild(checkWrap);
  return wrap;
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
  gpuHint.textContent = "Recomendado: Auto para H.264/H.265 em GPU NVIDIA. Forcar CUDA exige FFmpeg com NVENC.";
  container.appendChild(gpuHint);
  container.appendChild(selectField("CRF (H.264/H.265)", "video-crf", ["", "18", "20", "22", "24", "26", "28"]));
  container.appendChild(selectField("Preset (H.264/H.265)", "video-preset", ["", "ultrafast", "veryfast", "fast", "medium", "slow"]));
  container.appendChild(textField("Redimensionar largura", "video-resize-width"));
  container.appendChild(textField("Redimensionar altura", "video-resize-height"));
  container.appendChild(selectField("Modo de ajuste", "video-resize-mode", [
    { label: "Conter (com barras)", value: "contain" },
    { label: "Cortar (preencher)", value: "cover" },
    { label: "Esticar", value: "stretch" },
  ]));
  container.appendChild(checkboxField("Remover áudio da saída (vídeo sem trilha de áudio)", "video-strip-audio"));
}

function syncVideoCodecOptions(preferredCodec) {
  const containerEl = byId("video-container");
  const codecEl = byId("video-codec");
  if (!containerEl || !codecEl) return null;
  const container = String(containerEl.value || "mp4").toLowerCase();
  const allowed = VIDEO_CODEC_BY_CONTAINER[container] || ["h264", "h265", "vp9", "av1"];
  const desired = String(preferredCodec || codecEl.value || "").toLowerCase();
  const selected = allowed.includes(desired) ? desired : allowed[0];

  codecEl.innerHTML = "";
  allowed.forEach((codec) => {
    const opt = document.createElement("option");
    opt.value = codec;
    opt.textContent = codec;
    codecEl.appendChild(opt);
  });
  codecEl.value = selected;
  return selected;
}

function renderImageOptions() {
  const container = byId("image-options");
  container.innerHTML = "";
  container.appendChild(selectField("Formato", "image-format", ["jpg", "png", "webp", "avif"]));
  container.appendChild(textField("Qualidade (1-100)", "image-quality"));
  container.appendChild(textField("Largura", "image-width"));
  container.appendChild(textField("Altura", "image-height"));
  container.appendChild(selectField("Modo de ajuste", "image-resize-mode", [
    { label: "Conter (com barras)", value: "contain" },
    { label: "Cortar (preencher)", value: "cover" },
    { label: "Esticar", value: "stretch" },
  ]));
}

function renderMixOptions() {
  const container = byId("mix-options");
  container.innerHTML = "";
  container.appendChild(selectField("Formato final", "mix-output-format", [
    { label: "M4A (recomendado)", value: "m4a" },
    { label: "MP3", value: "mp3" },
    { label: "WAV", value: "wav" },
  ]));
  container.appendChild(textField("Bitrate alvo (kbps)", "mix-target-bitrate-kbps"));
  container.appendChild(textField("Limite de tamanho (MB)", "mix-max-size-mb"));
  container.appendChild(selectField("Normalizacao de audio", "mix-normalize", [
    { label: "Desativar", value: "off" },
    { label: "Ativar", value: "on" },
  ]));
  const hint = document.createElement("p");
  hint.className = "hint";
  hint.textContent = "Recomendado: desativado para manter o som original. Ative se as trilhas vierem com volume muito desigual.";
  container.appendChild(hint);
}

function applyPreset(label) {
  if (!label) return;
  let preset = null;
  if (state.mode === "audio") preset = audioPresets.find(p => p.label === label);
  if (state.mode === "video") preset = videoPresets.find(p => p.label === label);
  if (state.mode === "image") preset = imagePresets.find(p => p.label === label);
  if (state.mode === "mixagem") preset = mixPresets.find(p => p.label === label);
  if (!preset) return;
  const target = state.mode === "audio" ? state.settings.audio
    : state.mode === "video" ? state.settings.video
    : state.mode === "image" ? state.settings.image
    : state.mode === "mixagem" ? state.settings.mixagem
    : state.settings.audio;
  Object.assign(target, preset);
  applySettingsToUI();
}

function updatePresetOptions() {
  const select = byId("profile-preset");
  select.innerHTML = "";
  const options = [{ label: "(nenhum)", value: "" }].concat(
    state.mode === "audio" ? audioPresets
    : state.mode === "video" ? videoPresets
    : state.mode === "mixagem" ? mixPresets
    : imagePresets
  );
  options.forEach(opt => {
    const o = document.createElement("option");
    o.value = (opt.label || opt.value) || "";
    o.textContent = opt.label || opt.value || "(nenhum)";
    select.appendChild(o);
  });
}

function setMode(mode) {
  const valid = ["audio", "video", "image", "mixagem"];
  if (!valid.includes(mode)) mode = "audio";
  state.mode = mode;
  byId("audio-options").classList.toggle("active", mode === "audio");
  byId("video-options").classList.toggle("active", mode === "video");
  byId("image-options").classList.toggle("active", mode === "image");
  byId("mix-options").classList.toggle("active", mode === "mixagem");
  updatePresetOptions();
  applySettingsToUI();
  if (!initializing) saveSettings();
}

async function loadSettings() {
  const audio = await fetch("/api/settings?mode=audio").then(r => r.json());
  const video = await fetch("/api/settings?mode=video").then(r => r.json());
  const image = await fetch("/api/settings?mode=image").then(r => r.json());
  const mixSettings = await fetch("/api/settings?mode=mixagem").then(r => r.json());
  const legacySettings = await fetch("/api/settings?mode=craig_notebook").then(r => r.json());
  state.settings.audio = audio.data || {};
  state.settings.video = video.data || {};
  state.settings.image = image.data || {};
  state.settings.mixagem = mixSettings.data || legacySettings.data || {};
  state.outputDir = mixSettings.output_dir || legacySettings.output_dir || audio.output_dir || video.output_dir || image.output_dir || "";
  const lastRaw = mixSettings.last_mode || legacySettings.last_mode || audio.last_mode || "audio";
  const last = lastRaw === "craig_notebook" ? "mixagem" : lastRaw;
  byId("output-dir").value = state.outputDir;
  byId("profile-main").value = last;
  setMode(last);
}

function applySettingsToUI() {
  if (state.mode === "audio") {
    const opts = { ...audioDefaults, ...state.settings.audio };
    byId("audio-format").value = opts.format || "mp3";
    byId("audio-bitrate").value = opts.bitrate || "192";
    byId("audio-mode").value = opts.audio_mode || "cbr";
    byId("audio-normalize").value = opts.normalize || "off";
  } else if (state.mode === "video") {
    const opts = { ...videoDefaults, ...state.settings.video };
    byId("video-container").value = opts.container || "mp4";
    syncVideoCodecOptions(opts.codec || "h264");
    byId("video-resolution").value = opts.resolution || "best";
    byId("video-bitrate").value = opts.bitrate || "auto";
    byId("video-custom-bitrate").value = opts.custom_bitrate || "";
    byId("video-normalize").value = opts.normalize || "off";
    byId("video-accel").value = opts.video_accel || "off";
    byId("video-crf").value = opts.crf || "";
    byId("video-preset").value = opts.preset || "";
    byId("video-resize-width").value = opts.resize_width || "";
    byId("video-resize-height").value = opts.resize_height || "";
    byId("video-resize-mode").value = opts.resize_mode || "contain";
    byId("video-strip-audio").checked = String(opts.strip_audio || "off").toLowerCase() === "on";
    toggleCustomBitrate();
  } else if (state.mode === "image") {
    const opts = { ...imageDefaults, ...state.settings.image };
    byId("image-format").value = opts.image_format || "jpg";
    byId("image-quality").value = opts.image_quality || "";
    byId("image-width").value = opts.image_width || "";
    byId("image-height").value = opts.image_height || "";
    byId("image-resize-mode").value = opts.image_resize_mode || "contain";
  } else {
    const opts = { ...mixDefaults, ...state.settings.mixagem };
    byId("mix-output-format").value = opts.mix_output_format || "m4a";
    byId("mix-target-bitrate-kbps").value = opts.mix_target_bitrate_kbps || "96";
    byId("mix-max-size-mb").value = opts.mix_max_size_mb || "190";
    byId("mix-normalize").value = opts.normalize || "off";
  }
}

function onOptionChange() {
  if (state.mode === "video") {
    syncVideoCodecOptions();
    toggleCustomBitrate();
  }
  saveSettings();
}

function toggleCustomBitrate() {
  const row = byId("video-custom-bitrate")?.parentElement;
  if (!row) return;
  row.style.display = byId("video-bitrate").value === "custom" ? "grid" : "none";
}

function gatherOptions() {
  if (state.mode === "audio") {
    return {
      mode: "audio",
      format: byId("audio-format").value,
      bitrate: byId("audio-bitrate").value,
      audio_mode: byId("audio-mode").value,
      normalize: byId("audio-normalize")?.value || "off",
    };
  }
  if (state.mode === "video") {
    return {
      mode: "video",
      container: byId("video-container").value,
      codec: byId("video-codec").value,
      resolution: byId("video-resolution").value,
      bitrate: byId("video-bitrate").value,
      custom_bitrate: byId("video-custom-bitrate").value,
      normalize: byId("video-normalize")?.value || "off",
      video_accel: byId("video-accel")?.value || "off",
      strip_audio: byId("video-strip-audio").checked ? "on" : "off",
      crf: byId("video-crf").value,
      preset: byId("video-preset").value,
      resize_width: byId("video-resize-width").value,
      resize_height: byId("video-resize-height").value,
      resize_mode: byId("video-resize-mode").value,
    };
  }
  if (state.mode === "mixagem") {
    return {
      mode: "mixagem",
      mix_output_format: byId("mix-output-format").value,
      mix_target_bitrate_kbps: byId("mix-target-bitrate-kbps").value,
      mix_max_size_mb: byId("mix-max-size-mb").value,
      normalize: byId("mix-normalize")?.value || "off",
    };
  }
  return {
    mode: "image",
    image_format: byId("image-format").value,
    image_quality: byId("image-quality").value,
    image_width: byId("image-width").value,
    image_height: byId("image-height").value,
    image_resize_mode: byId("image-resize-mode").value,
  };
}

async function saveSettings() {
  const options = gatherOptions();
  if (state.mode === "audio") state.settings.audio = { ...options };
  if (state.mode === "video") state.settings.video = { ...options };
  if (state.mode === "image") state.settings.image = { ...options };
  if (state.mode === "mixagem") state.settings.mixagem = { ...options };

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

async function startConvert() {
  if (!state.files.length) {
    alert("Selecione arquivos para converter");
    return;
  }
  const selectedFiles = state.files.filter((file) => state.selectedFileKeys.has(fileKey(file)));
  if (!selectedFiles.length) {
    alert("Selecione ao menos um arquivo marcado para processar.");
    return;
  }
  for (const file of selectedFiles) {
    const key = fileKey(file);
    const entries = state.archiveEntries.get(key);
    if (entries && entries.length && !entries.some((e) => e.selected)) {
      alert(`O ZIP ${file.name} estÃ¡ sem arquivos internos marcados.`);
      return;
    }
  }
  if (state.mode === "mixagem") {
    const invalid = selectedFiles.filter((file) => {
      const name = String(file?.name || "").toLowerCase();
      return !(name.endsWith(".zip") || name.endsWith(".aup.zip"));
    });
    if (invalid.length) {
      alert("No modo Mixagem, selecione apenas arquivos ZIP (.zip/.aup.zip).");
      return;
    }
  }
  const form = new FormData();
  selectedFiles.forEach(file => form.append("files", file));
  const archiveSelections = {};
  selectedFiles.forEach((file) => {
    const key = fileKey(file);
    const entries = state.archiveEntries.get(key);
    if (!entries || !entries.length) return;
    archiveSelections[file.name] = entries.filter((e) => e.selected).map((e) => e.name);
  });
  form.append("archive_selections", JSON.stringify(archiveSelections));
  form.append("options", JSON.stringify(gatherOptions()));
  form.append("output_dir", byId("output-dir").value);

  const res = await fetch("/api/convert", { method: "POST", body: form });
  const data = await res.json();
  if (!data.ok) {
    alert(data.error || "Falha ao iniciar conversao");
  }
}

async function openFolder() {
  const res = await fetch("/api/open-last-folder", { method: "POST" });
  if (!res.ok) {
    const data = await res.json();
    alert(data.error || "Sem pasta disponivel");
  }
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

async function postJobAction(path, fallbackError) {
  const res = await fetch(path, { method: "POST" });
  const data = await res.json().catch(() => ({}));
  if (!data.ok) {
    alert(data.error || fallbackError);
    return false;
  }
  return true;
}

function renderJob(job) {
  const div = document.createElement("div");
  div.className = `job ${job.status || ""}`;
  div.id = `job-${job.id}`;
  const header = document.createElement("div");
  header.className = "job-header";
  const statusText = JOB_STATUS_LABELS[String(job.status || "").toLowerCase()] || String(job.status || "");
  header.innerHTML = `<strong>${statusText}</strong><span>${job.message || ""}</span>`;
  const bar = document.createElement("div");
  bar.className = "progress-bar";
  const span = document.createElement("span");
  span.style.width = `${job.percent || 0}%`;
  bar.appendChild(span);
  const meta = document.createElement("div");
  meta.className = "job-meta";
  const mixMetrics = parseMixMetrics(job.speed);
  const metrics = parseTranscribeMetrics(job.speed);
  const stageInfo = parseStageFromMessage(job.message);
  if ((job.mode === "mixagem" || job.mode === "craig_notebook") && mixMetrics) {
    const stageText = stageInfo
      ? `${stageInfo.stageIndex}/${stageInfo.stageTotal}: ${stageInfo.stageName} (${Number(stageInfo.stagePercent || 0).toFixed(1)}%)`
      : "-";
    const stalledText = mixMetrics.stalledSeconds >= 1 ? formatEta(mixMetrics.stalledSeconds) : "00:00";
    const etaText = formatEta(mixMetrics.etaSeconds ?? job.eta);
    const remainingText = formatEta(mixMetrics.remainingSeconds);
    const totalEstText = formatEta(mixMetrics.totalEstimatedSeconds);
    const stability = mixMetrics.stalledSeconds >= 20
      ? "Estimativa em espera (sem avanço recente)"
      : "Estimativa dinâmica";
    meta.innerHTML = `
      <div>%: ${mixMetrics.overallPercent.toFixed(1)}</div>
      <div>ETA: ${etaText}</div>
      <div>Tempo atual: ${formatEta(mixMetrics.elapsedSeconds)}</div>
      <div>Falta (estimada): ${remainingText}</div>
      <div>Total estimado: ${totalEstText}</div>
      <div>Etapa atual: ${stageText}</div>
      <div>Sem avanço: ${stalledText}</div>
      <div>Status ETA: ${stability}</div>
    `;
  } else if ((job.mode === "mixagem" || job.mode === "craig_notebook") && metrics) {
    meta.innerHTML = `
      <div>%: ${job.percent?.toFixed ? job.percent.toFixed(1) : job.percent || 0}</div>
      <div>ETA: ${formatEta(metrics.etaSeconds)}</div>
      <div>Tempo atual: ${formatEta(metrics.elapsedSeconds)}</div>
      <div>Progresso etapa: ${formatEta(metrics.currentAudioSeconds)}</div>
      <div>Falta (estimada): ${formatEta(metrics.remainingAudioSeconds)}</div>
      <div>Total estimado: ${formatEta(metrics.totalAudioSeconds)}</div>
    `;
  } else if (metrics) {
    meta.innerHTML = `
      <div>%: ${job.percent?.toFixed ? job.percent.toFixed(1) : job.percent || 0}</div>
      <div>ETA: ${formatEta(metrics.etaSeconds)}</div>
      <div>Tempo atual: ${formatEta(metrics.elapsedSeconds)}</div>
      <div>Falta (audio): ${formatEta(metrics.remainingAudioSeconds)}</div>
      <div>Audio atual: ${formatEta(metrics.currentAudioSeconds)}</div>
      <div>Audio total: ${formatEta(metrics.totalAudioSeconds)}</div>
    `;
  } else {
    meta.innerHTML = `
      <div>%: ${job.percent?.toFixed ? job.percent.toFixed(1) : job.percent || 0}</div>
      <div>Velocidade: ${job.speed || "-"}</div>
      <div>ETA: ${formatEta(job.eta)}</div>
      <div>Tamanho: ${formatSize(job.size)}</div>
    `;
  }
  const actions = document.createElement("div");
  actions.className = "job-actions";
  const status = String(job.status || "").toLowerCase();
  const isActive = ["queued", "running", "paused"].includes(status);
  const isTerminal = ["done", "error", "canceled"].includes(status);

  if (status === "queued") {
    const startNow = document.createElement("button");
    startNow.className = "ghost";
    startNow.textContent = "Iniciar agora";
    startNow.addEventListener("click", async () => {
      await postJobAction(`/api/jobs/${job.id}/start-now`, "Nao foi possivel priorizar");
    });
    actions.appendChild(startNow);
  }
  if (status === "running") {
    const pause = document.createElement("button");
    pause.className = "ghost";
    pause.textContent = "Pausar";
    pause.addEventListener("click", async () => {
      await postJobAction(`/api/jobs/${job.id}/pause`, "Nao foi possivel pausar");
    });
    actions.appendChild(pause);
  }
  if (status === "paused") {
    const resume = document.createElement("button");
    resume.className = "ghost";
    resume.textContent = "Retomar";
    resume.addEventListener("click", async () => {
      await postJobAction(`/api/jobs/${job.id}/resume`, "Nao foi possivel retomar");
    });
    actions.appendChild(resume);
  }
  if (isActive) {
    const cancel = document.createElement("button");
    cancel.className = "ghost";
    cancel.textContent = "Parar job";
    cancel.addEventListener("click", async () => {
      await postJobAction(`/api/jobs/${job.id}/cancel`, "Nao foi possivel parar o job");
    });
    actions.appendChild(cancel);
  }
  if (isTerminal) {
    const openResult = document.createElement("button");
    openResult.className = "ghost";
    openResult.textContent = "Ver resultado";
    openResult.addEventListener("click", async () => {
      await postJobAction(`/api/jobs/${job.id}/open-result`, "Resultado indisponivel");
    });
    actions.appendChild(openResult);

    const repeat = document.createElement("button");
    repeat.className = "ghost";
    repeat.textContent = "Repetir";
    repeat.addEventListener("click", async () => {
      await postJobAction(`/api/jobs/${job.id}/repeat`, "Nao foi possivel repetir");
    });
    actions.appendChild(repeat);

    const okBtn = document.createElement("button");
    okBtn.className = "ghost";
    okBtn.textContent = "OK";
    okBtn.addEventListener("click", async () => {
      await postJobAction(`/api/jobs/${job.id}/ok`, "Nao foi possivel finalizar");
    });
    actions.appendChild(okBtn);
  }
  const remove = document.createElement("button");
  remove.className = "ghost";
  remove.textContent = "Remover";
  remove.addEventListener("click", async () => {
    await postJobAction(`/api/jobs/${job.id}/delete`, "Nao foi possivel remover");
  });
  actions.appendChild(remove);
  div.appendChild(header);
  div.appendChild(bar);
  div.appendChild(meta);
  div.appendChild(buildConvertStageView(job));
  div.appendChild(actions);
  return div;
}

function updateJob(job) {
  state.jobs.set(job.id, job);
  renderJobs();
}

async function loadJobs() {
  const jobs = await fetch("/api/jobs").then(r => r.json());
  state.jobs.clear();
  jobs.forEach(job => state.jobs.set(job.id, job));
  renderJobs();
}

function renderJobs() {
  const container = byId("jobs");
  const all = Array.from(state.jobs.values()).filter(j => j.status !== "group");
  const sorted = all.sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
  updateStatusAccelIndicator(sorted);
  const controls = byId("jobs-controls");
  if (controls) {
    controls.innerHTML = "";
    if (sorted.length > JOB_PREVIEW_LIMIT) {
      const btn = document.createElement("button");
      btn.className = "ghost";
      btn.textContent = state.showAllJobs ? "Recolher lote" : `Expandir lote (${sorted.length})`;
      btn.addEventListener("click", () => {
        state.showAllJobs = !state.showAllJobs;
        renderJobs();
      });
      controls.appendChild(btn);
    }
  }
  container.innerHTML = "";
  const visible = state.showAllJobs ? sorted : sorted.slice(0, JOB_PREVIEW_LIMIT);
  visible.forEach(job => container.appendChild(renderJob(job)));
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

function setupSSE() {
  if (state.sse) state.sse.close();
  const es = new EventSource("/api/stream");
  state.sse = es;
  es.onmessage = (evt) => {
    const payload = JSON.parse(evt.data);
    if (payload.type === "job") {
      updateJob(payload.job);
    } else if (payload.type === "job_removed") {
      state.jobs.delete(payload.job_id);
      renderJobs();
    }
  };
  es.onerror = () => {
    es.close();
    setTimeout(setupSSE, 2000);
  };
}

async function inspectArchiveEntries(file) {
  const key = fileKey(file);
  const low = String(file.name || "").toLowerCase();
  if (!(low.endsWith(".zip") || low.endsWith(".aup.zip"))) {
    state.archiveEntries.delete(key);
    return;
  }
  try {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch("/api/archive/entries", { method: "POST", body: form });
    const data = await res.json().catch(() => ({}));
    if (!data.ok) {
      logClient("warning", `falha ao ler zip ${file.name}: ${data.error || res.status}`);
      state.archiveEntries.set(key, []);
      return;
    }
    const items = (data.entries || []).map((e) => ({
      name: String(e.name || ""),
      size: Number(e.size || 0),
      selected: true,
    }));
    state.archiveEntries.set(key, items);
    if (items.length) state.archiveExpanded.add(key);
  } catch (err) {
    logClient("error", `inspect zip falhou ${file.name}: ${err}`);
    state.archiveEntries.set(key, []);
  }
}

function handleFiles(files) {
  const incoming = Array.from(files);
  const existingKeys = new Set(state.files.map((f) => fileKey(f)));
  incoming.forEach(f => {
    const key = fileKey(f);
    if (!existingKeys.has(key)) {
      state.files.push(f);
      existingKeys.add(key);
      state.selectedFileKeys.add(key);
    }
  });
  const inspections = incoming.map((f) => inspectArchiveEntries(f));
  Promise.allSettled(inspections).then(() => renderFileList());
  renderFileList();

  if (hasZipInSelection(state.files) && state.mode !== "mixagem") {
    const mainProfile = byId("profile-main");
    if (mainProfile) {
      mainProfile.value = "mixagem";
    }
    setMode("mixagem");
    logClient("info", "perfil principal recomendado automaticamente: Mixagem");
  }
}

function renderFileList() {
  const list = byId("file-list");
  if (!list) return;
  list.innerHTML = "";
  if (!state.files.length) {
    list.innerHTML = "<div class=\"muted\">Nenhum arquivo selecionado</div>";
  } else {
    state.files.forEach((file, idx) => {
      const key = fileKey(file);
      const row = document.createElement("div");
      row.className = "file-row";
      const check = document.createElement("input");
      check.type = "checkbox";
      check.className = "file-check";
      check.checked = state.selectedFileKeys.has(key);
      check.addEventListener("change", () => {
        if (check.checked) state.selectedFileKeys.add(key);
        else state.selectedFileKeys.delete(key);
        renderFileList();
      });
      const name = document.createElement("span");
      name.textContent = file.name;
      const size = document.createElement("span");
      size.className = "muted";
      size.textContent = formatSize(file.size);
      const remove = document.createElement("button");
      remove.className = "ghost";
      remove.textContent = "Remover";
      remove.addEventListener("click", () => {
        state.selectedFileKeys.delete(key);
        state.archiveEntries.delete(key);
        state.archiveExpanded.delete(key);
        state.files.splice(idx, 1);
        renderFileList();
      });
      row.appendChild(check);
      row.appendChild(name);
      row.appendChild(size);
      row.appendChild(remove);
      list.appendChild(row);

      const entries = state.archiveEntries.get(key);
      if (Array.isArray(entries) && entries.length) {
        const expanded = state.archiveExpanded.has(key);
        const panel = document.createElement("div");
        panel.className = "archive-panel";

        const title = document.createElement("div");
        title.className = "archive-title";
        const selectedCount = entries.filter((e) => e.selected).length;
        title.textContent = `ZIP interno: ${selectedCount}/${entries.length} marcado(s)`;
        panel.appendChild(title);

        const controls = document.createElement("div");
        controls.className = "actions";
        const toggle = document.createElement("button");
        toggle.className = "ghost";
        toggle.textContent = expanded ? "Ocultar internos" : "Mostrar internos";
        toggle.addEventListener("click", () => {
          if (expanded) state.archiveExpanded.delete(key);
          else state.archiveExpanded.add(key);
          renderFileList();
        });
        const allBtn = document.createElement("button");
        allBtn.className = "ghost";
        allBtn.textContent = "Marcar todos internos";
        allBtn.addEventListener("click", () => {
          entries.forEach((e) => {
            e.selected = true;
          });
          renderFileList();
        });
        const noneBtn = document.createElement("button");
        noneBtn.className = "ghost";
        noneBtn.textContent = "Desmarcar internos";
        noneBtn.addEventListener("click", () => {
          entries.forEach((e) => {
            e.selected = false;
          });
          renderFileList();
        });
        controls.appendChild(toggle);
        controls.appendChild(allBtn);
        controls.appendChild(noneBtn);
        panel.appendChild(controls);

        if (expanded) {
          const innerList = document.createElement("div");
          innerList.className = "archive-list";
          entries.forEach((entry) => {
            const r = document.createElement("div");
            r.className = "archive-row";
            const c = document.createElement("input");
            c.type = "checkbox";
            c.className = "file-check";
            c.checked = !!entry.selected;
            c.addEventListener("change", () => {
              entry.selected = c.checked;
              renderFileList();
            });
            const n = document.createElement("span");
            n.textContent = entry.name;
            const s = document.createElement("span");
            s.className = "muted";
            s.textContent = formatSize(entry.size);
            r.appendChild(c);
            r.appendChild(n);
            r.appendChild(s);
            innerList.appendChild(r);
          });
          panel.appendChild(innerList);
        }

        list.appendChild(panel);
      }
    });
  }
  byId("drop-zone").classList.toggle("active", state.files.length > 0);
  const selectedCount = state.files.filter((file) => state.selectedFileKeys.has(fileKey(file))).length;
  const countEl = byId("selected-files-count");
  if (countEl) countEl.textContent = `${selectedCount} de ${state.files.length} marcado(s)`;
  const text = state.files.length
    ? `${selectedCount}/${state.files.length} marcado(s) para processar`
    : "Solte arquivos aqui ou clique para selecionar";
  byId("drop-zone").querySelector("p").textContent = text;
}

function init() {
  logClient("info", "convert.js iniciado");
  logElementPresence([
    "profile-main",
    "profile-preset",
    "file-input",
    "clear-files",
    "select-all-files",
    "deselect-all-files",
    "selected-files-count",
    "file-list",
    "start-convert",
    "open-folder",
    "browse-folder",
    "mix-options",
    "mix-output-format",
    "mix-target-bitrate-kbps",
    "mix-max-size-mb",
    "video-strip-audio",
    "output-dir",
    "drop-zone",
    "jobs-controls",
    "jobs",
  ]);
  renderAudioOptions();
  renderVideoOptions();
  renderImageOptions();
  renderMixOptions();
  updatePresetOptions();
  setMode(byId("profile-main").value || "audio");

  byId("profile-main").addEventListener("change", (e) => {
    logClick("profile-main");
    setMode(e.target.value);
  });
  byId("profile-preset").addEventListener("change", (e) => {
    logClick("profile-preset");
    applyPreset(e.target.value);
  });

  byId("file-input").addEventListener("change", (e) => {
    logClient("info", "file-input change");
    handleFiles(e.target.files);
  });
  const clearBtn = byId("clear-files");
  if (clearBtn) {
    clearBtn.addEventListener("click", () => {
      logClick("clear-files");
      state.files = [];
      state.selectedFileKeys.clear();
      state.archiveEntries.clear();
      state.archiveExpanded.clear();
      renderFileList();
    });
  }
  const selectAllBtn = byId("select-all-files");
  if (selectAllBtn) {
    selectAllBtn.addEventListener("click", () => {
      logClick("select-all-files");
      state.files.forEach((file) => state.selectedFileKeys.add(fileKey(file)));
      renderFileList();
    });
  }
  const deselectAllBtn = byId("deselect-all-files");
  if (deselectAllBtn) {
    deselectAllBtn.addEventListener("click", () => {
      logClick("deselect-all-files");
      state.selectedFileKeys.clear();
      renderFileList();
    });
  }
  const drop = byId("drop-zone");
  drop.addEventListener("dragover", (e) => {
    e.preventDefault();
  });
  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    logClient("info", "drop-zone drop");
    handleFiles(e.dataTransfer.files);
  });
  drop.addEventListener("click", () => {
    logClick("drop-zone");
    byId("file-input").click();
  });
  document.addEventListener("dragover", (e) => e.preventDefault());
  document.addEventListener("drop", (e) => e.preventDefault());

  byId("start-convert").addEventListener("click", () => {
    logClick("start-convert");
    startConvert();
  });
  byId("open-folder").addEventListener("click", () => {
    logClick("open-folder");
    openFolder();
  });
  byId("browse-folder").addEventListener("click", () => {
    logClick("browse-folder");
    browseFolder();
  });
  byId("output-dir").addEventListener("change", () => {
    logClient("info", "output-dir alterado");
    saveSettings();
  });

  Promise.resolve()
    .then(loadSettings)
    .then(() => {
      logClient("info", "settings carregadas");
      if (!byId("output-dir").value) {
        byId("output-dir").value = "C:\\";
      }
      initializing = false;
      return saveSettings();
    })
    .then(() => {
      renderFileList();
    })
    .then(loadJobs)
    .then(() => {
      logClient("info", "jobs carregados");
      setupSSE();
    })
    .catch((err) => {
      logClient("error", `Falha init: ${err}`);
    });
}

document.addEventListener("DOMContentLoaded", init);

