const state = {
  outputDir: "",
  files: [],
  selectedFileKeys: new Set(),
  archiveEntries: new Map(),
  archiveExpanded: new Set(),
  sse: null,
  models: [],
  defaultModel: "",
  liveStreams: new Map(),
  livePanels: new Map(),
  jobs: new Map(),
  showAllJobs: false,
  stageByJob: new Map(),
  fileHistoryExpanded: new Set(),
  notifiedErrors: new Set(),
  startInFlight: false,
  saveTimer: null,
  glossaryRows: [],
  glossaryEditingId: null,
  glossaryNextId: 1,
  glossaryDirty: false,
  glossaryEditTransitionUntil: 0,
  accelLastMode: "",
  accelChangeText: "",
  accelChangeUntil: 0,
};
const JOB_PREVIEW_LIMIT = 8;
const LIVE_TAIL_PREVIEW_LINES = 30;
const ACCEL_CHANGE_VISIBLE_MS = 12000;
const PROCESS_ACCEL_LABELS = {
  cpu: "Processo Usando CPU",
  gpu_no_cuda: "Processo Usando GPU sem CUDA",
  gpu_cuda: "Processo Usando GPU Com CUDA",
};
const FREE_BACKEND_MODELS = ["tiny", "base", "small", "medium", "large-v3", "distil-large-v3"];
const MODEL_DESCRIPTIONS = {
  tiny: "mais rapido, menor qualidade; bom para rascunho e maquina fraca",
  base: "equilibrio entre velocidade e qualidade para audio simples",
  small: "qualidade melhor que base, ainda relativamente rapido",
  medium: "boa qualidade geral, custo de tempo e memoria maior",
  "large-v3": "maior qualidade para portugues e audio dificil; mais lento",
  "distil-large-v3": "quase qualidade de large-v3 com ganho de velocidade",
  "silero-v5.1.2": "modelo de VAD/deteccao de voz, nao recomendado para transcricao completa",
};
const GUIDED_PRESETS = {
  whisperx_cuda_fast: {
    profile: "single_channel",
    backend: "whisperx",
    model: "large-v3",
    language: "pt",
    chunkSeconds: "300",
    overlapSeconds: "2.0",
    diarize: "off",
    normalize: "on",
    vad: "off",
    mode: "single",
    device: "cuda",
    computeType: "float16",
    whisperxBatchSize: "4",
    initialPrompt: "glossário",
    outputJson: "on",
    help: "WhisperX rápido (CUDA): ideal para GPU NVIDIA, com boa velocidade e mantendo saída em SRT/TXT/JSON.",
  },
  whisperx_cuda_balanced: {
    profile: "single_channel",
    backend: "whisperx",
    model: "large-v3",
    language: "pt",
    chunkSeconds: "600",
    overlapSeconds: "1.0",
    diarize: "off",
    normalize: "on",
    vad: "off",
    mode: "single",
    device: "cuda",
    computeType: "float16",
    whisperxBatchSize: "4",
    initialPrompt: "glossário",
    outputJson: "on",
    help: "WhisperX equilibrado: mantém checkpoint por chunk para proteção anti-loop, com menos overhead que chunk curto.",
  },
  whisperx_cli_puro: {
    profile: "single_channel",
    backend: "whisperx",
    model: "large-v3",
    language: "pt",
    chunkSeconds: "600",
    overlapSeconds: "1.0",
    diarize: "off",
    normalize: "off",
    vad: "off",
    mode: "single",
    device: "cuda",
    computeType: "float16",
    whisperxBatchSize: "4",
    initialPrompt: "glossário",
    outputJson: "on",
    help: "WhisperX CLI puro (Dani): roda pelo comando python -m whisperx para comparação direta com o terminal.",
  },
  craig_long_best: {
    profile: "craig_multitrack",
    backend: "faster_whisper",
    model: "large-v3",
    chunkSeconds: "300",
    overlapSeconds: "2.0",
    diarize: "off",
    normalize: "on",
    vad: "off",
    mode: "single",
    help: "Para ZIP Craig grande com foco em qualidade final. Mais lento, mas mais consistente em audio complexo.",
  },
  craig_long_fast: {
    profile: "craig_multitrack",
    backend: "faster_whisper",
    model: "distil-large-v3",
    chunkSeconds: "300",
    overlapSeconds: "1.5",
    diarize: "off",
    normalize: "on",
    vad: "off",
    mode: "single",
    help: "Para ZIP Craig grande priorizando velocidade. Perde um pouco de qualidade em troca de tempo menor.",
  },
  single_long_best: {
    profile: "single_channel",
    backend: "faster_whisper",
    model: "large-v3",
    chunkSeconds: "300",
    overlapSeconds: "1.5",
    diarize: "on",
    normalize: "on",
    vad: "off",
    mode: "single",
    help: "Para audio unico longo com foco em qualidade. Boa escolha para entrevistas e aulas.",
  },
  single_long_fast: {
    profile: "single_channel",
    backend: "faster_whisper",
    model: "medium",
    chunkSeconds: "300",
    overlapSeconds: "1.0",
    diarize: "off",
    normalize: "on",
    vad: "off",
    mode: "single",
    help: "Para audio unico longo priorizando velocidade. Ideal para triagem inicial.",
  },
};

window.__transcribe_init_ok = true;

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

function parseSecondsLike(value) {
  const raw = String(value ?? "").trim();
  if (!raw) return null;
  if (raw.includes(":")) {
    const parts = raw.split(":").map((p) => p.trim());
    if (parts.length !== 2 && parts.length !== 3) return null;
    const nums = parts.map((p) => Number(p));
    if (nums.some((n) => !Number.isFinite(n) || n < 0)) return null;
    if (nums.length === 2) {
      const [mm, ss] = nums;
      return (mm * 60) + ss;
    }
    const [hh, mm, ss] = nums;
    return (hh * 3600) + (mm * 60) + ss;
  }
  const sec = Number(raw.replace(",", "."));
  if (!Number.isFinite(sec) || sec < 0) return null;
  return sec;
}

function formatSecondsAsClock(seconds) {
  const s = Number(seconds);
  if (!Number.isFinite(s) || s < 0) return "";
  const total = Math.round(s);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const sec = total % 60;
  if (h > 0) return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  return `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}

function normalizeChunkInput(value, fallbackSeconds = 300) {
  const parsed = parseSecondsLike(value);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return formatSecondsAsClock(fallbackSeconds);
  }
  return formatSecondsAsClock(parsed);
}

function makeGlossaryRow(reference = "", context = "") {
  const id = `g-${state.glossaryNextId++}`;
  return { id, reference, context };
}

function parseGlossaryTextToRows(text) {
  const rows = [];
  String(text || "")
    .split(/\r?\n/)
    .forEach((line) => {
      const raw = String(line || "").trim();
      if (!raw || raw.startsWith("#")) return;
      const match = raw.match(/^(.+?)(=>|->)(.+)$/);
      if (!match) {
        rows.push(makeGlossaryRow(raw, ""));
        return;
      }
      const context = String(match[1] || "").trim();
      const reference = String(match[3] || "").trim();
      if (!reference) return;
      rows.push(makeGlossaryRow(reference, context));
    });
  return rows;
}

function glossaryRowsToText(rows) {
  return (rows || [])
    .map((row) => ({
      reference: String(row?.reference || "").trim(),
      context: String(row?.context || "").trim(),
    }))
    .filter((row) => row.reference)
    .map((row) => (row.context ? `${row.context} => ${row.reference}` : row.reference))
    .join("\n");
}

function markGlossaryDirty() {
  state.glossaryDirty = true;
}

function commitGlossaryIfDirty() {
  if (!state.glossaryDirty) return;
  state.glossaryDirty = false;
  saveSettings();
}

function focusGlossaryReferenceInput(id) {
  const input = document.querySelector(`input[data-glossary-reference-id="${id}"]`);
  if (!input) return;
  input.focus();
}

function setGlossaryEditing(id) {
  state.glossaryEditingId = id || null;
}

function isGlossaryFocusoutSuppressed() {
  return Number(state.glossaryEditTransitionUntil || 0) > Date.now();
}

function beginGlossaryEdit(id) {
  state.glossaryEditTransitionUntil = Date.now() + 280;
  setGlossaryEditing(id);
  renderGlossaryEditor();
  setTimeout(() => {
    focusGlossaryReferenceInput(id);
  }, 0);
}

function removeGlossaryRowById(rowId) {
  state.glossaryRows = state.glossaryRows.filter((item) => item.id !== rowId);
  if (state.glossaryEditingId === rowId) {
    setGlossaryEditing(null);
  }
  markGlossaryDirty();
  renderGlossaryEditor();
  commitGlossaryIfDirty();
}

function renderGlossaryEditor() {
  const list = byId("transcribe-glossary-list");
  if (!list) return;
  list.innerHTML = "";
  const header = document.createElement("div");
  header.className = "glossary-columns";
  header.innerHTML = "<span>Palavra referencia</span><span>Palavra contextual (opcional)</span>";
  list.appendChild(header);

  if (!state.glossaryRows.length) {
    const muted = document.createElement("div");
    muted.className = "muted";
    muted.textContent = "Sem regras no glossario.";
    list.appendChild(muted);
    return;
  }
  state.glossaryRows.forEach((row) => {
    const line = document.createElement("div");
    const isEditing = state.glossaryEditingId === row.id;
    const arrow = document.createElement("span");
    arrow.className = "glossary-arrow muted";
    arrow.textContent = "->";

    if (isEditing) {
      line.className = "glossary-row glossary-row-edit";

      const reference = document.createElement("input");
      reference.type = "text";
      reference.placeholder = "Palavra referencia (ex: Kovir)";
      reference.value = row.reference || "";
      reference.dataset.glossaryReferenceId = String(row.id);
      reference.addEventListener("input", () => {
        row.reference = reference.value;
        markGlossaryDirty();
        scheduleSaveSettings(800);
      });

      const context = document.createElement("input");
      context.type = "text";
      context.placeholder = "Palavra contextual (opcional)";
      context.value = row.context || "";
      context.addEventListener("input", () => {
        row.context = context.value;
        markGlossaryDirty();
        scheduleSaveSettings(800);
      });

      const onLeaveRow = () => {
        setTimeout(() => {
          const active = document.activeElement;
          if (active && line.contains(active)) return;
          setGlossaryEditing(null);
          commitGlossaryIfDirty();
          renderGlossaryEditor();
        }, 0);
      };
      reference.addEventListener("blur", onLeaveRow);
      context.addEventListener("blur", onLeaveRow);

      const done = document.createElement("button");
      done.className = "ghost glossary-edit-row";
      done.type = "button";
      done.textContent = "Concluir";
      done.addEventListener("click", () => {
        setGlossaryEditing(null);
        commitGlossaryIfDirty();
        renderGlossaryEditor();
      });

      const remove = document.createElement("button");
      remove.className = "ghost glossary-remove";
      remove.type = "button";
      remove.textContent = "Remover";
      remove.addEventListener("click", () => {
        removeGlossaryRowById(row.id);
      });

      line.appendChild(reference);
      line.appendChild(arrow);
      line.appendChild(context);
      line.appendChild(done);
      line.appendChild(remove);
    } else {
      line.className = "glossary-row glossary-row-view";

      const referenceView = document.createElement("span");
      referenceView.className = "glossary-cell";
      referenceView.textContent = row.reference || "(vazio)";

      const contextView = document.createElement("span");
      contextView.className = "glossary-cell";
      contextView.textContent = row.context || "(opcional)";

      const edit = document.createElement("button");
      edit.className = "ghost glossary-edit-row";
      edit.type = "button";
      edit.textContent = "Editar";
      edit.addEventListener("click", () => {
        beginGlossaryEdit(row.id);
      });

      const removeInline = document.createElement("button");
      removeInline.className = "ghost glossary-remove-inline";
      removeInline.type = "button";
      removeInline.textContent = "X";
      removeInline.title = "Remover linha";
      removeInline.addEventListener("click", () => {
        removeGlossaryRowById(row.id);
      });

      line.appendChild(referenceView);
      line.appendChild(arrow);
      line.appendChild(contextView);
      line.appendChild(edit);
      line.appendChild(removeInline);
    }
    list.appendChild(line);
  });
}

function modelDisplayName(value) {
  const raw = String(value || "");
  if (!raw) return "";
  const parts = raw.split(/[/\\]/);
  return parts[parts.length - 1] || raw;
}

function normalizeModelKey(value) {
  let key = modelDisplayName(value).toLowerCase();
  key = key.replace(/^ggml-/, "").replace(/\.bin$/, "");
  if (key.startsWith("distil-large-v3")) return "distil-large-v3";
  if (key.startsWith("large-v3")) return "large-v3";
  if (key.startsWith("silero-v5")) return "silero-v5.1.2";
  return key;
}

function modelDescription(value) {
  const key = normalizeModelKey(value);
  if (MODEL_DESCRIPTIONS[key]) return MODEL_DESCRIPTIONS[key];
  return "modelo custom/local; teste em audio curto antes de usar em lote grande";
}

function modelOptionLabel(value) {
  const name = modelDisplayName(value).replace(/^ggml-/i, "").replace(/\.bin$/i, "");
  return `${name} - ${modelDescription(value)}`;
}

function updateModelHelp() {
  const help = byId("transcribe-model-help");
  const select = byId("transcribe-model");
  const backend = byId("transcribe-backend")?.value || "faster_whisper";
  if (!help || !select) return;
  const value = select.value || "";
  if (!value) {
    help.textContent = "Selecione um modelo para ver detalhes.";
    return;
  }
  const title = modelDisplayName(value).replace(/^ggml-/i, "").replace(/\.bin$/i, "");
  const backendHint = backend === "whisper_cpp"
    ? "Backend whisper_cpp usa modelos .bin locais."
    : `Backend ${backend} baixa/usa modelo por nome.`;
  help.textContent = `${title}: ${modelDescription(value)}. ${backendHint}`;
}

function fileKey(file) {
  if (file && typeof file.localPath === "string" && file.localPath) {
    return `local:${file.localPath.toLowerCase()}`;
  }
  return `${file.name}-${file.size}-${file.lastModified}`;
}

function isAbsolutePath(text) {
  const p = String(text || "").trim();
  if (!p) return false;
  if (/^[a-zA-Z]:[\\/]/.test(p)) return true;
  if (p.startsWith("\\\\") || p.startsWith("/")) return true;
  return false;
}

function dirnamePath(pathText) {
  const p = String(pathText || "").trim().replace(/[\\/]+$/, "");
  if (!p) return "";
  const idx = Math.max(p.lastIndexOf("/"), p.lastIndexOf("\\"));
  if (idx <= 0) return "";
  return p.slice(0, idx);
}

function inferSourceDir(file) {
  if (!file) return "";
  if (typeof file.localPath === "string" && file.localPath) {
    return dirnamePath(file.localPath);
  }
  const rawPath = typeof file.path === "string" ? file.path : "";
  if (rawPath && isAbsolutePath(rawPath) && !/fakepath/i.test(rawPath)) {
    return dirnamePath(rawPath);
  }
  return "";
}

async function suggestOutputDirFromSelection(files) {
  const list = Array.isArray(files) ? files : [];
  const first = list.find((f) => !!inferSourceDir(f));
  const sourceDir = first ? inferSourceDir(first) : "";
  if (!sourceDir || !isAbsolutePath(sourceDir)) return;
  const outputInput = byId("output-dir");
  if (!outputInput) return;
  if (String(outputInput.value || "").trim() === sourceDir) return;
  outputInput.value = sourceDir;
  state.outputDir = sourceDir;
  await saveSettings();
}

function getSelectedFiles() {
  if (!state.files.length) return [];
  return state.files.filter((f) => state.selectedFileKeys.has(fileKey(f)));
}

function selectedBytesTotal() {
  return getSelectedFiles().reduce((acc, file) => acc + Number(file?.size || 0), 0);
}

function isLargeSelection() {
  return selectedBytesTotal() >= (300 * 1024 * 1024);
}

function recommendedGuidedMode() {
  if (hasArchiveInput()) return "craig_long_best";
  if (isLargeSelection()) return "single_long_best";
  return "single_long_fast";
}

function updateGuidedHelp() {
  const help = byId("transcribe-guided-help");
  const select = byId("transcribe-guided-mode");
  if (!help || !select) return;
  const mode = String(select.value || "manual");
  if (mode !== "manual" && GUIDED_PRESETS[mode]) {
    help.textContent = GUIDED_PRESETS[mode].help;
    return;
  }
  const recommended = recommendedGuidedMode();
  const recText = GUIDED_PRESETS[recommended]?.help || "";
  help.textContent = `Modo manual ativo. Recomendacao para seu lote atual: ${recText}`;
}

function applyGuidedPreset(mode) {
  const preset = GUIDED_PRESETS[String(mode || "")];
  if (!preset) {
    updateGuidedHelp();
    return;
  }
  byId("transcribe-profile").value = preset.profile;
  byId("transcribe-backend").value = preset.backend;
  renderModels();
  byId("transcribe-model").value = preset.model;
  if (preset.language && byId("transcribe-language")) {
    byId("transcribe-language").value = preset.language;
  }
  byId("transcribe-chunk-seconds").value = normalizeChunkInput(preset.chunkSeconds, 300);
  byId("transcribe-chunk-overlap-seconds").value = preset.overlapSeconds;
  byId("transcribe-diarize").value = preset.diarize;
  byId("transcribe-normalize").value = preset.normalize;
  byId("transcribe-vad").value = preset.vad;
  byId("transcribe-mode").value = preset.mode;
  if (byId("transcribe-device")) {
    byId("transcribe-device").value = preset.device || "";
  }
  if (byId("transcribe-compute-type")) {
    byId("transcribe-compute-type").value = preset.computeType || "";
  }
  if (byId("whisperx-batch-size")) {
    byId("whisperx-batch-size").value = preset.whisperxBatchSize || "4";
  }
  if (byId("transcribe-initial-prompt")) {
    byId("transcribe-initial-prompt").value = preset.initialPrompt || "";
  }
  if (byId("transcribe-output-json")) {
    byId("transcribe-output-json").value = preset.outputJson || "on";
  }
  applyBackendAwareMode();
  applyInputAwareDefaults();
  updateModelHelp();
  updateGuidedHelp();
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

const TRANSCRIBE_STAGES = ["Conversão para WAV", "Normalização", "Executando VAD", "Transcrição", "Junção"];
const JOB_STATUS_LABELS = {
  queued: "Na fila",
  running: "Em andamento",
  done: "Concluído",
  error: "Erro",
  canceled: "Cancelado",
  paused: "Pausado",
  group: "Grupo",
};

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
  const st = _getStageState(job.id);
  let key = info.fileKey || "__single__";
  let label = info.fileLabel || "Arquivo";

  if (key !== "__single__") {
    if (st.currentKey && st.currentKey !== key) {
      const prev = st.files.get(st.currentKey);
      if (prev) {
        prev.stageIndex = Math.max(prev.stageIndex || 0, prev.stageTotal || TRANSCRIBE_STAGES.length);
        prev.stagePercent = 100.0;
      }
    }
    st.currentKey = key;
  }

  if (!st.files.has(key)) {
    st.files.set(key, {
      key,
      label,
      stageIndex: 0,
      stageTotal: TRANSCRIBE_STAGES.length,
      stagePercent: 0,
      skipped: {},
      recovered: {},
    });
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
    rec.stageIndex = Math.max(rec.stageTotal || TRANSCRIBE_STAGES.length, rec.stageIndex || 0);
    rec.stagePercent = 100.0;
  }
}

function buildStageLine(record, isRunning) {
  const wrap = document.createElement("div");
  wrap.className = "stage-wrap";
  const title = document.createElement("div");
  title.className = "stage-file";
  title.textContent = record.label;
  wrap.appendChild(title);

  const line = document.createElement("div");
  line.className = "stage-line";
  TRANSCRIBE_STAGES.forEach((name, idx) => {
    const n = idx + 1;
    const chip = document.createElement("div");
    chip.className = "stage-chip stage-pending";
    if (record.skipped && record.skipped[n]) chip.className = "stage-chip stage-skipped";
    else if (record.stageIndex > n) chip.className = "stage-chip stage-done";
    else if (record.stageIndex === n && isRunning) chip.className = "stage-chip stage-running";
    let txt = name;
    if (record.skipped && record.skipped[n]) {
      txt += " (pulado)";
    } else if (record.recovered && record.recovered[n]) {
      txt += " (arquivo recuperado)";
    } else if (record.stageIndex === n && isRunning) {
      txt += ` (${(record.stagePercent || 0).toFixed(1)}%)`;
    }
    if (!(record.skipped && record.skipped[n]) && !isRunning && record.stageIndex >= n) {
      chip.className = "stage-chip stage-done";
    }
    chip.textContent = txt;
    line.appendChild(chip);
  });
  wrap.appendChild(line);
  return wrap;
}

function buildStageView(job) {
  const st = _getStageState(job.id);
  const wrap = document.createElement("div");
  wrap.className = "stage-wrap";
  if (!st.order.length) {
    return buildStageLine({ label: "Arquivo", stageIndex: 0, stagePercent: 0, stageTotal: TRANSCRIBE_STAGES.length }, job.status === "running");
  }

  const currentKey = st.currentKey || st.order[st.order.length - 1];
  const current = st.files.get(currentKey);
  if (current) {
    wrap.appendChild(buildStageLine(current, job.status === "running"));
  }

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
    if (expanded) {
      history.forEach(rec => wrap.appendChild(buildStageLine(rec, false)));
    }
  }
  return wrap;
}

async function loadModels() {
  try {
    const res = await fetch("/api/transcriber/models");
    const data = await res.json();
    state.models = data.models || [];
    state.defaultModel = data.default || "";
  } catch (err) {
    logClient("error", `Falha ao carregar modelos: ${err}`);
    state.models = [];
    state.defaultModel = "";
  }
}

function renderModels() {
  const select = byId("transcribe-model");
  const backend = byId("transcribe-backend")?.value || "faster_whisper";
  const savedValue = select.value;
  select.innerHTML = "";
  if (backend === "whisper_cpp") {
    if (!state.models.length) {
      const o = document.createElement("option");
      o.value = "";
      o.textContent = "(nenhum modelo)";
      select.appendChild(o);
      return;
    }
    state.models.forEach(path => {
      const o = document.createElement("option");
      o.value = path;
      o.textContent = modelOptionLabel(path);
      select.appendChild(o);
    });
    if (savedValue && state.models.includes(savedValue)) {
      select.value = savedValue;
    } else if (state.defaultModel) {
      select.value = state.defaultModel;
    }
    updateModelHelp();
    return;
  }

  const fromCpp = state.models
    .map((p) => (p || "").split("\\").pop()?.replace(/^ggml-/i, "").replace(/\.bin$/i, ""))
    .filter(Boolean);
  const allModels = Array.from(new Set([...FREE_BACKEND_MODELS, ...fromCpp]));
  if (!allModels.length) {
    const o = document.createElement("option");
    o.value = "";
    o.textContent = "(nenhum modelo)";
    select.appendChild(o);
    return;
  }
  allModels.forEach((name) => {
    const o = document.createElement("option");
    o.value = name;
    o.textContent = modelOptionLabel(name);
    select.appendChild(o);
  });
  if (savedValue && allModels.includes(savedValue)) {
    select.value = savedValue;
  } else {
    select.value = "large-v3";
  }
  updateModelHelp();
}

function decodeFileUriToPath(uri) {
  const text = String(uri || "").trim();
  if (!text.toLowerCase().startsWith("file://")) return "";
  let raw = text.replace(/^file:\/\//i, "");
  if (raw.startsWith("/")) {
    raw = raw.replace(/^\/+/, "");
  }
  raw = decodeURIComponent(raw);
  if (/^[a-zA-Z]:\//.test(raw)) {
    raw = raw.replace(/\//g, "\\");
  }
  if (/^[a-zA-Z]:\\/.test(raw)) return raw;
  if (raw.startsWith("\\\\")) return raw;
  return "";
}

function droppedPathsFromEvent(e) {
  try {
    const dt = e?.dataTransfer;
    if (!dt) return [];
    const uriList = String(dt.getData("text/uri-list") || "");
    if (!uriList.trim()) return [];
    const lines = uriList
      .split(/\r?\n/)
      .map((s) => s.trim())
      .filter((s) => s && !s.startsWith("#"));
    const paths = lines.map(decodeFileUriToPath).filter((p) => !!p);
    return paths;
  } catch (_) {
    return [];
  }
}

function handleFiles(files, localPaths = []) {
  const incoming = Array.from(files);
  if (Array.isArray(localPaths) && localPaths.length) {
    const byName = new Map();
    localPaths.forEach((p) => {
      const name = String(p || "").split(/[/\\]/).pop() || "";
      if (name && !byName.has(name.toLowerCase())) {
        byName.set(name.toLowerCase(), p);
      }
    });
    incoming.forEach((f, idx) => {
      let candidate = String(localPaths[idx] || "");
      if (!candidate) {
        candidate = String(byName.get(String(f?.name || "").toLowerCase()) || "");
      }
      if (candidate && isAbsolutePath(candidate)) {
        try {
          f.localPath = candidate;
        } catch (_) {}
      }
    });
  }
  const existingKeys = new Set(state.files.map(f => fileKey(f)));
  incoming.forEach(f => {
    const key = fileKey(f);
    if (!existingKeys.has(key)) {
      state.files.push(f);
      existingKeys.add(key);
      state.selectedFileKeys.add(key);
    }
  });
  const inspections = incoming.map((f) => inspectArchiveEntries(f));
  Promise.allSettled(inspections).then(() => {
    renderFileList();
    suggestOutputDirFromSelection(getSelectedFiles()).catch(() => {});
  });
  renderFileList();
  updateGuidedHelp();
  applyInputAwareDefaults();
  saveSettings();
}

async function pickLocalFiles() {
  const res = await fetch("/api/transcribe/pick-files", { method: "POST" });
  const data = await res.json().catch(() => ({}));
  if (!data.ok) {
    if (res.status !== 400) {
      alert(data.error || "Falha ao selecionar arquivos");
    }
    return;
  }
  const incoming = Array.isArray(data.files) ? data.files : [];
  const existingKeys = new Set(state.files.map(f => fileKey(f)));
  incoming.forEach((item, idx) => {
    const path = String(item.path || "");
    const name = String(item.name || path || `arquivo_${idx + 1}`);
    const size = Number(item.size || 0);
    if (!path) return;
    const f = {
      name,
      size,
      lastModified: Date.now() + idx,
      localPath: path,
    };
    const key = fileKey(f);
    if (!existingKeys.has(key)) {
      state.files.push(f);
      existingKeys.add(key);
    }
    state.selectedFileKeys.add(key);
  });
  renderFileList();
  updateGuidedHelp();
  applyInputAwareDefaults();
  if (data.source_dir && isAbsolutePath(data.source_dir)) {
    byId("output-dir").value = String(data.source_dir);
    state.outputDir = String(data.source_dir);
  } else {
    await suggestOutputDirFromSelection(getSelectedFiles());
  }
  await saveSettings();
}

async function saveSettings() {
  const options = gatherOptions();
  state.outputDir = byId("output-dir").value;
  const res = await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      mode: "transcribe",
      data: options,
      output_dir: state.outputDir,
      last_mode: "transcribe",
    }),
  });
  const data = await res.json().catch(() => ({}));
  if (!data.ok) {
    logClient("error", `settings save falhou: ${data.error || res.status}`);
  } else {
    logClient("info", "settings salvas");
  }
}

function scheduleSaveSettings(delayMs = 250) {
  if (state.saveTimer) {
    clearTimeout(state.saveTimer);
  }
  state.saveTimer = setTimeout(() => {
    state.saveTimer = null;
    saveSettings();
  }, delayMs);
}

function gatherOptions() {
  const chunkSecondsRaw = byId("transcribe-chunk-seconds").value;
  const chunkSecondsParsed = parseSecondsLike(chunkSecondsRaw);
  return {
    mode: "transcribe",
    transcribe_guided_mode: byId("transcribe-guided-mode").value || "manual",
    transcribe_profile: byId("transcribe-profile").value || "auto",
    transcribe_backend: byId("transcribe-backend").value || "faster_whisper",
    model: byId("transcribe-model").value,
    language: byId("transcribe-language").value,
    diarize: byId("transcribe-diarize").value,
    threads: byId("transcribe-threads").value,
    beam_size: byId("transcribe-beam").value,
    max_len: byId("transcribe-max-len").value,
    chunk_seconds: Number.isFinite(chunkSecondsParsed) ? String(chunkSecondsParsed) : String(chunkSecondsRaw || ""),
    chunk_overlap_seconds: byId("transcribe-chunk-overlap-seconds").value,
    transcribe_device: byId("transcribe-device").value,
    transcribe_compute_type: byId("transcribe-compute-type").value,
    whisperx_batch_size: byId("whisperx-batch-size").value,
    transcribe_initial_prompt: byId("transcribe-initial-prompt").value,
    transcribe_output_json: byId("transcribe-output-json").value,
    compare_all: byId("transcribe-mode").value === "compare",
    normalize: byId("transcribe-normalize").value,
    vad: byId("transcribe-vad").value,
    vad_threshold: byId("transcribe-vad-threshold").value,
    vad_min_silence: byId("transcribe-vad-min-silence").value,
    transcribe_glossary: glossaryRowsToText(state.glossaryRows),
  };
}

function hasArchiveInput() {
  return getSelectedFiles().some((f) => {
    const n = String(f.name || "").toLowerCase();
    return n.endsWith(".zip") || n.endsWith(".aup.zip");
  });
}

function applyInputAwareDefaults() {
  const profile = byId("transcribe-profile")?.value || "auto";
  const normalize = byId("transcribe-normalize");
  const vad = byId("transcribe-vad");
  const diarize = byId("transcribe-diarize");
  if (!normalize || !vad || !diarize) return;

  if (profile === "craig_multitrack" || (profile === "auto" && hasArchiveInput())) {
    normalize.value = "on";
    vad.value = "off";
    diarize.value = "off";
    normalize.disabled = true;
    vad.disabled = true;
    diarize.disabled = true;
  } else {
    normalize.disabled = false;
    vad.disabled = false;
    diarize.disabled = false;
    if (!normalize.value) normalize.value = "on";
    if (!vad.value) vad.value = "off";
    if (!diarize.value) diarize.value = "on";
    if (profile === "single_channel") {
      if (!vad.value) vad.value = "off";
    }
  }
  updateGuidedHelp();
}

function applyBackendAwareMode() {
  const backend = byId("transcribe-backend")?.value || "faster_whisper";
  const mode = byId("transcribe-mode");
  if (!mode) return;
  const compareOpt = mode.querySelector("option[value='compare']");
  if (compareOpt) {
    compareOpt.disabled = backend !== "whisper_cpp";
  }
  if (backend !== "whisper_cpp" && mode.value === "compare") {
    mode.value = "single";
  }
}

async function startTranscribe() {
  if (state.startInFlight) return;
  if (!state.files.length) {
    alert("Selecione arquivos para transcrever");
    return;
  }
  const selected = getSelectedFiles();
  if (!selected.length) {
    alert("Selecione ao menos um arquivo na lista para processar.");
    return;
  }
  const localSelected = selected.filter((f) => typeof f.localPath === "string" && f.localPath);
  const uploadedSelected = selected.filter((f) => !(typeof f.localPath === "string" && f.localPath));
  if (localSelected.length && uploadedSelected.length) {
    alert("Selecione arquivos de uma unica origem por vez: locais do PC ou upload do navegador.");
    return;
  }
  for (const file of selected) {
    const key = fileKey(file);
    const entries = state.archiveEntries.get(key);
    if (entries && entries.length && !entries.some((e) => e.selected)) {
      alert(`O ZIP ${file.name} está sem arquivos internos marcados.`);
      return;
    }
  }
  state.startInFlight = true;
  const startBtn = byId("start-transcribe");
  if (startBtn) startBtn.disabled = true;
  try {
    let res;
    if (localSelected.length) {
      const payload = {
        file_paths: localSelected.map((f) => f.localPath),
        options: gatherOptions(),
        output_dir: byId("output-dir").value,
      };
      res = await fetch("/api/transcribe/start-local", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    } else {
      const form = new FormData();
      uploadedSelected.forEach(file => form.append("files", file));
      const archiveSelections = {};
      uploadedSelected.forEach((file) => {
        const key = fileKey(file);
        const entries = state.archiveEntries.get(key);
        if (!entries || !entries.length) return;
        const picked = entries.filter((e) => e.selected).map((e) => e.name);
        archiveSelections[file.name] = picked;
      });
      form.append("archive_selections", JSON.stringify(archiveSelections));
      form.append("options", JSON.stringify(gatherOptions()));
      form.append("output_dir", byId("output-dir").value);
      res = await fetch("/api/convert", { method: "POST", body: form });
    }
    const data = await res.json().catch(() => ({}));
    if (!data.ok) {
      alert(data.error || "Falha ao iniciar transcricao");
    }
  } finally {
    state.startInFlight = false;
    if (startBtn) startBtn.disabled = false;
  }
}

async function openFolder() {
  const res = await fetch("/api/open-last-folder", { method: "POST" });
  if (!res.ok) {
    const data = await res.json();
    alert(data.error || "Sem pasta disponivel");
  }
}

async function resumeLastTranscribe() {
  const res = await fetch("/api/transcribe/resume-last", { method: "POST" });
  const data = await res.json().catch(() => ({}));
  if (!data.ok) {
    alert(data.error || "Não foi possível retomar o projeto.");
    return;
  }
  alert(summarizeResumeDetails(data.resume_details));
  logClient("info", `retomada executada: ids=${(data.ids || []).join(",")}`);
}

async function cleanupTranscribe() {
  const ok = window.confirm("Limpar todo cache e resquícios de transcrição? (jobs ativos serão preservados)");
  if (!ok) return;
  const res = await fetch("/api/transcribe/cleanup", { method: "POST" });
  const data = await res.json().catch(() => ({}));
  if (!data.ok) {
    alert(data.error || "Falha na limpeza.");
    return;
  }
  alert(`Limpeza concluída. Itens removidos: ${data.removed || 0}`);
  logClient("info", `cleanup transcribe removidos=${data.removed || 0}`);
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

function getLivePanelState(jobId) {
  if (!state.livePanels.has(jobId)) {
    state.livePanels.set(jobId, {
      open: false,
      lines: [],
      userInteracted: false,
      autoFollow: true,
      statusMessage: "Aguardando transcricao iniciar...",
      box: null,
      btn: null,
    });
  }
  return state.livePanels.get(jobId);
}

function isNearBottom(el, threshold = 12) {
  if (!el) return true;
  const remain = el.scrollHeight - (el.scrollTop + el.clientHeight);
  return remain <= threshold;
}

function renderLivePanel(jobId) {
  const panel = getLivePanelState(jobId);
  const box = panel.box;
  const btn = panel.btn;
  if (btn) {
    btn.textContent = panel.open ? "Parar ao vivo" : "Transcrição ao vivo";
  }
  if (!box) return;

  if (!panel.open) {
    box.style.display = "none";
    return;
  }
  box.style.display = "block";
  const prevGapFromBottom = box.scrollHeight - box.scrollTop;
  const keepViewport = panel.userInteracted && !panel.autoFollow;

  if (!panel.lines.length) {
    box.textContent = panel.statusMessage || "Aguardando transcricao iniciar...";
  } else {
    const visible = panel.userInteracted ? panel.lines : panel.lines.slice(-LIVE_TAIL_PREVIEW_LINES);
    box.textContent = visible.join("\n");
  }

  if (panel.autoFollow) {
    box.scrollTop = box.scrollHeight;
  } else if (keepViewport) {
    const nextTop = box.scrollHeight - prevGapFromBottom;
    box.scrollTop = Math.max(0, nextTop);
  }
}

function bindLiveBoxEvents(jobId, liveBox) {
  const panel = getLivePanelState(jobId);
  liveBox.addEventListener("scroll", () => {
    const atBottom = isNearBottom(liveBox);
    panel.autoFollow = atBottom;
    if (!atBottom) {
      panel.userInteracted = true;
    }
  });
  const markInteracted = () => {
    if (!panel.userInteracted) {
      panel.userInteracted = true;
    }
  };
  liveBox.addEventListener("wheel", markInteracted, { passive: true });
  liveBox.addEventListener("mousedown", markInteracted);
  liveBox.addEventListener("touchstart", markInteracted, { passive: true });
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

function jobStatusLabel(status) {
  const key = String(status || "").trim().toLowerCase();
  return JOB_STATUS_LABELS[key] || (key || "Status");
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

function jobSupportsLive(job, options) {
  if (!job || job.mode !== "transcribe") return false;
  const opts = options && typeof options === "object" ? options : {};
  const guidedMode = String(opts.transcribe_guided_mode || "").trim().toLowerCase();
  if (guidedMode === "whisperx_cli_puro") return false;
  return true;
}

function normalizeProcessAccelMode(value) {
  const text = String(value || "").trim().toLowerCase();
  if (text === "cpu" || text === "gpu_no_cuda" || text === "gpu_cuda") return text;
  return "";
}

function inferProcessAccelMode(job) {
  if (!job || String(job.mode || "") !== "transcribe") return "";
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

function updateStatusAccelIndicator(sortedJobs) {
  const indicator = byId("status-accel-indicator");
  const change = byId("status-accel-change");
  if (!indicator) return;
  const transcribeJobs = (sortedJobs || []).filter((j) => String(j.mode || "") === "transcribe" && String(j.status || "") !== "group");
  let current = transcribeJobs.find((j) => String(j.status || "") === "running");
  if (!current && transcribeJobs.length) current = transcribeJobs[0];
  const mode = inferProcessAccelMode(current);
  const label = mode ? PROCESS_ACCEL_LABELS[mode] : "Processo: sem atividade de transcrição";
  indicator.textContent = label;
  indicator.dataset.mode = mode || "none";
  indicator.title = current ? `Job: ${current.id}` : "";

  const now = Date.now();
  if (mode && state.accelLastMode && mode !== state.accelLastMode) {
    state.accelChangeText = `Alterou para: ${PROCESS_ACCEL_LABELS[mode]}`;
    state.accelChangeUntil = now + ACCEL_CHANGE_VISIBLE_MS;
    logClient("info", `aceleração alterada: ${state.accelLastMode} -> ${mode}`);
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

function resumeOriginText(origin) {
  const value = String(origin || "").trim().toLowerCase();
  if (value === "checkpoint") return "Retomada: checkpoint";
  if (value === "log") return "Retomada: log";
  return "";
}

function summarizeResumeDetails(details) {
  const list = Array.isArray(details) ? details : [];
  if (!list.length) return "Retomada criada.";
  const lines = [];
  list.forEach((item) => {
    const origin = String(item?.origin || "none").toLowerCase();
    const fileName = String(item?.input_path || "").split(/[\\/]/).pop() || "arquivo";
    if (origin === "checkpoint") {
      lines.push(`- ${fileName}: checkpoint`);
      return;
    }
    if (origin === "log") {
      const chunk = Number(item?.suggested_chunk_index || 0);
      if (Number.isFinite(chunk) && chunk > 0) {
        lines.push(`- ${fileName}: log (chunk sugerido ${chunk})`);
      } else {
        lines.push(`- ${fileName}: log`);
      }
      return;
    }
    lines.push(`- ${fileName}: sem origem de retomada`);
  });
  return `Retomada criada:\n${lines.join("\n")}`;
}

function renderJob(job) {
  if (job.mode !== "transcribe") return null;
  const div = document.createElement("div");
  div.className = `job ${job.status || ""}`;
  div.id = `job-${job.id}`;
  const header = document.createElement("div");
  header.className = "job-header";
  const headerMain = document.createElement("div");
  headerMain.className = "job-header-main";
  const statusEl = document.createElement("strong");
  statusEl.className = "job-status-label";
  statusEl.textContent = jobStatusLabel(job.status);
  const messageEl = document.createElement("span");
  messageEl.className = "job-header-message";
  messageEl.textContent = String(job.message || "");
  headerMain.appendChild(statusEl);
  headerMain.appendChild(messageEl);
  header.appendChild(headerMain);
  const jobOptions = parseJobOptions(job);
  const resumeText = resumeOriginText(jobOptions.transcribe_resume_origin);
  if (resumeText && ["queued", "running", "paused"].includes(String(job.status || ""))) {
    const badge = document.createElement("span");
    badge.className = "job-resume-indicator";
    badge.textContent = resumeText;
    header.appendChild(badge);
  }
  const bar = document.createElement("div");
  bar.className = "progress-bar";
  const span = document.createElement("span");
  span.style.width = `${job.percent || 0}%`;
  bar.appendChild(span);
  const meta = document.createElement("div");
  meta.className = "job-meta";
  const metrics = parseTranscribeMetrics(job.speed);
  const stageInfo = parseStageInfo(job.message || "");
  const totalPercent = job.percent?.toFixed ? job.percent.toFixed(1) : job.percent || 0;
  const stagePercent = Number.isFinite(Number(stageInfo?.stagePercent)) ? Number(stageInfo.stagePercent).toFixed(1) : "0.0";
  if (metrics) {
    meta.innerHTML = `
      <div>% total: ${totalPercent}</div>
      <div>% etapa: ${stagePercent}</div>
      <div>ETA: ${formatEta(metrics.etaSeconds)}</div>
      <div>Tempo atual: ${formatEta(metrics.elapsedSeconds)}</div>
      <div>Falta (audio): ${formatEta(metrics.remainingAudioSeconds)}</div>
      <div>Audio atual: ${formatEta(metrics.currentAudioSeconds)}</div>
      <div>Audio total: ${formatEta(metrics.totalAudioSeconds)}</div>
    `;
  } else {
    meta.innerHTML = `
      <div>% total: ${totalPercent}</div>
      <div>% etapa: ${stagePercent}</div>
      <div>Velocidade: ${job.speed || "-"}</div>
      <div>ETA: ${formatEta(job.eta)}</div>
      <div>Falta (audio): ${formatEta(job.size)}</div>
    `;
  }
  const actions = document.createElement("div");
  actions.className = "job-actions job-actions-stack";
  const primaryActions = document.createElement("div");
  primaryActions.className = "job-actions-row";
  const retryActions = document.createElement("div");
  retryActions.className = "job-actions-row job-actions-row-retry";
  const panel = getLivePanelState(job.id);
  const liveEnabled = jobSupportsLive(job, jobOptions);
  if (!liveEnabled && panel.open) {
    panel.open = false;
    stopLiveInline(job.id);
  }
  let live = null;
  if (liveEnabled) {
    live = document.createElement("button");
    live.className = "ghost";
    live.textContent = panel.open ? "Parar ao vivo" : "Transcrição ao vivo";
  }
  const liveBox = document.createElement("pre");
  liveBox.className = "live-box";
  panel.box = liveBox;
  panel.btn = live;
  bindLiveBoxEvents(job.id, liveBox);
  renderLivePanel(job.id);
  if (live) {
    live.addEventListener("click", () => {
      if (!panel.open) {
        panel.open = true;
        panel.userInteracted = false;
        panel.autoFollow = true;
        renderLivePanel(job.id);
        startLiveInline(job, liveBox, live);
      } else {
        panel.open = false;
        stopLiveInline(job.id);
        renderLivePanel(job.id);
      }
    });
  }
  const remove = document.createElement("button");
  remove.className = "ghost";
  remove.textContent = "Remover";
  remove.addEventListener("click", async () => {
    const res = await fetch(`/api/jobs/${job.id}/delete`, { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!data.ok) alert(data.error || "Nao foi possivel remover");
  });
  const stop = document.createElement("button");
  stop.className = "ghost";
  stop.textContent = "Parar tarefa";
  stop.addEventListener("click", async () => {
    const res = await fetch(`/api/jobs/${job.id}/cancel`, { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!data.ok) alert(data.error || "Nao foi possivel parar o job");
  });
  const okBtn = document.createElement("button");
  okBtn.className = "ghost";
  okBtn.textContent = "OK";
  okBtn.addEventListener("click", async () => {
    const res = await fetch(`/api/jobs/${job.id}/ok`, { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!data.ok) alert(data.error || "Nao foi possivel finalizar");
  });
  const openResult = document.createElement("button");
  openResult.className = "ghost";
  openResult.textContent = "Ver Resultado";
  openResult.addEventListener("click", async () => {
    const res = await fetch(`/api/jobs/${job.id}/open-result`, { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!data.ok) alert(data.error || "Resultado indisponivel");
  });
  const redoConvert = document.createElement("button");
  redoConvert.className = "ghost";
  redoConvert.textContent = "Refazer Conversão";
  redoConvert.addEventListener("click", async () => {
    const res = await fetch(`/api/jobs/${job.id}/redo/convert`, { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!data.ok) alert(data.error || "Falha ao refazer conversao");
  });
  const redoNorm = document.createElement("button");
  redoNorm.className = "ghost";
  redoNorm.textContent = "Refazer Normalização";
  redoNorm.addEventListener("click", async () => {
    const res = await fetch(`/api/jobs/${job.id}/redo/normalize`, { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!data.ok) alert(data.error || "Falha ao refazer normalizacao");
  });
  const redoVad = document.createElement("button");
  redoVad.className = "ghost";
  redoVad.textContent = "Refazer VAD";
  redoVad.addEventListener("click", async () => {
    const res = await fetch(`/api/jobs/${job.id}/redo/vad`, { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!data.ok) alert(data.error || "Falha ao refazer VAD");
  });
  const redoTrans = document.createElement("button");
  redoTrans.className = "ghost";
  redoTrans.textContent = "Refazer Transcrição";
  redoTrans.addEventListener("click", async () => {
    const res = await fetch(`/api/jobs/${job.id}/redo/transcribe`, { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!data.ok) alert(data.error || "Falha ao refazer transcricao");
  });
  const redoChunk = document.createElement("button");
  redoChunk.className = "ghost";
  redoChunk.textContent = "Refazer Chunk";
  redoChunk.addEventListener("click", async () => {
    const raw = window.prompt("Numero do chunk para refazer (1, 2, 3...):", "");
    if (raw === null) return;
    const chunkIndex = Number.parseInt(String(raw).trim(), 10);
    if (!Number.isFinite(chunkIndex) || chunkIndex < 1) {
      alert("Chunk invalido. Informe um numero inteiro >= 1.");
      return;
    }
    const res = await fetch(`/api/jobs/${job.id}/redo/chunk`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chunk_index: chunkIndex }),
    });
    const data = await res.json().catch(() => ({}));
    if (!data.ok) alert(data.error || "Falha ao refazer chunk");
  });
  const redoMerge = document.createElement("button");
  redoMerge.className = "ghost";
  redoMerge.textContent = "Refazer Junção";
  redoMerge.addEventListener("click", async () => {
    const res = await fetch(`/api/jobs/${job.id}/redo/merge`, { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!data.ok) alert(data.error || "Falha ao refazer juncao");
  });
  if (live) {
    primaryActions.appendChild(live);
  }
  primaryActions.appendChild(openResult);
  if (["running", "queued", "paused"].includes(String(job.status || ""))) {
    primaryActions.appendChild(stop);
  }
  primaryActions.appendChild(okBtn);
  primaryActions.appendChild(remove);
  retryActions.appendChild(redoConvert);
  retryActions.appendChild(redoNorm);
  retryActions.appendChild(redoVad);
  retryActions.appendChild(redoTrans);
  retryActions.appendChild(redoChunk);
  retryActions.appendChild(redoMerge);
  actions.appendChild(primaryActions);
  actions.appendChild(retryActions);
  div.appendChild(header);
  div.appendChild(bar);
  div.appendChild(meta);
  div.appendChild(buildStageView(job));
  div.appendChild(actions);
  div.appendChild(liveBox);
  if (panel.open && live) {
    const stream = state.liveStreams.get(job.id);
    if (stream) {
      stream.box = liveBox;
      stream.btn = live;
      renderLivePanel(job.id);
    } else {
      startLiveInline(job, liveBox, live);
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
  const all = Array.from(state.jobs.values()).filter(j => j.mode === "transcribe" && j.status !== "group");
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
  visible.forEach(job => {
    const el = renderJob(job);
    if (el) container.appendChild(el);
  });
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
      renderJobs();
      stopLiveInline(payload.job_id);
      state.livePanels.delete(payload.job_id);
    }
  };
  es.onerror = () => {
    es.close();
    setTimeout(setupSSE, 2000);
  };
}

function renderFileList() {
  const list = byId("file-list");
  if (!list) return;
  list.innerHTML = "";
  if (!state.files.length) {
    list.innerHTML = "<div class=\"muted\">Nenhum arquivo selecionado</div>";
  } else {
    state.files.forEach((file, idx) => {
      const row = document.createElement("div");
      row.className = "file-row";
      const check = document.createElement("input");
      check.type = "checkbox";
      check.className = "file-check";
      const key = fileKey(file);
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
      size.textContent = `${(file.size / (1024 * 1024)).toFixed(2)} MB`;
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
      if (entries) {
        const panel = document.createElement("div");
        panel.className = "archive-panel";
        const title = document.createElement("div");
        title.className = "archive-title";
        const selectedCount = entries.filter((e) => e.selected).length;
        title.textContent = entries.length
          ? `ZIP interno: ${selectedCount}/${entries.length} marcado(s)`
          : "ZIP interno: nenhum arquivo de mídia detectado";
        panel.appendChild(title);

        if (entries.length) {
          const controls = document.createElement("div");
          controls.className = "job-actions";
          const toggle = document.createElement("button");
          toggle.className = "ghost";
          const expanded = state.archiveExpanded.has(key);
          toggle.textContent = expanded ? "Ocultar internos" : "Selecionar arquivos";
          toggle.addEventListener("click", () => {
            if (expanded) state.archiveExpanded.delete(key);
            else state.archiveExpanded.add(key);
            renderFileList();
          });
          const allBtn = document.createElement("button");
          allBtn.className = "ghost";
          allBtn.textContent = "Marcar todos internos";
          allBtn.addEventListener("click", () => {
            entries.forEach((e) => { e.selected = true; });
            renderFileList();
          });
          const noneBtn = document.createElement("button");
          noneBtn.className = "ghost";
          noneBtn.textContent = "Desmarcar internos";
          noneBtn.addEventListener("click", () => {
            entries.forEach((e) => { e.selected = false; });
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
        }
        list.appendChild(panel);
      }
    });
  }
  byId("drop-zone").classList.toggle("active", state.files.length > 0);
  const selectedCount = getSelectedFiles().length;
  const countEl = byId("selected-files-count");
  if (countEl) countEl.textContent = `${selectedCount} de ${state.files.length} marcado(s)`;
  const text = state.files.length
    ? `${selectedCount}/${state.files.length} marcado(s) para processar`
    : "Solte arquivos aqui ou clique para selecionar";
  byId("drop-zone").querySelector("p").textContent = text;
  applyInputAwareDefaults();
}


function stopLiveInline(jobId) {
  const current = state.liveStreams.get(jobId);
  if (current) {
    current.es.close();
    state.liveStreams.delete(jobId);
  }
}

function startLiveInline(job, liveBox, btn) {
  const panel = getLivePanelState(job.id);
  panel.open = true;
  panel.box = liveBox;
  panel.btn = btn;
  panel.statusMessage = panel.statusMessage || "Aguardando transcricao iniciar...";
  stopLiveInline(job.id);
  renderLivePanel(job.id);

  const es = new EventSource(`/api/transcribe/${job.id}/live`);
  state.liveStreams.set(job.id, { es, box: liveBox, btn });

  es.onmessage = (evt) => {
    const data = JSON.parse(evt.data);
    if (data.line !== undefined) {
      panel.lines.push(String(data.line));
      if (panel.lines.length > 5000) {
        panel.lines = panel.lines.slice(-5000);
      }
      renderLivePanel(job.id);
    }
  };
  es.addEventListener("status", (evt) => {
    const data = JSON.parse(evt.data);
    panel.statusMessage = data.message || "Aguardando transcricao iniciar...";
    if (!panel.lines.length) {
      renderLivePanel(job.id);
    }
  });
  es.onerror = () => {
    es.close();
    state.liveStreams.delete(job.id);
    if (panel.open) {
      setTimeout(() => {
        if (!panel.open || !panel.box || !panel.btn) return;
        if (!document.body.contains(panel.box)) return;
        startLiveInline(job, panel.box, panel.btn);
      }, 2000);
    }
  };
}



async function loadSettings() {
  const transcribe = await fetch("/api/settings?mode=transcribe").then(r => r.json());
  state.outputDir = transcribe.output_dir || "";
  byId("output-dir").value = state.outputDir;
  const opts = transcribe.data || {};
  byId("transcribe-guided-mode").value = opts.transcribe_guided_mode || "craig_long_best";
  byId("transcribe-profile").value = opts.transcribe_profile || "auto";
  byId("transcribe-backend").value = opts.transcribe_backend || "faster_whisper";
  renderModels();
  if (opts.model) {
    byId("transcribe-model").value = opts.model;
  }
  byId("transcribe-language").value = opts.language || "pt";
  byId("transcribe-diarize").value = opts.diarize || "on";
  byId("transcribe-threads").value = opts.threads || "6";
  byId("transcribe-beam").value = opts.beam_size || "5";
  byId("transcribe-max-len").value = opts.max_len || "42";
  byId("transcribe-chunk-seconds").value = normalizeChunkInput(opts.chunk_seconds || "300", 300);
  byId("transcribe-chunk-overlap-seconds").value = opts.chunk_overlap_seconds || "1.5";
  byId("transcribe-device").value = opts.transcribe_device || "";
  byId("transcribe-compute-type").value = opts.transcribe_compute_type || "";
  byId("whisperx-batch-size").value = opts.whisperx_batch_size || "4";
  byId("transcribe-initial-prompt").value = opts.transcribe_initial_prompt || "";
  byId("transcribe-output-json").value = opts.transcribe_output_json || "on";
  byId("transcribe-normalize").value = opts.normalize || "on";
  byId("transcribe-vad").value = opts.vad || "off";
  byId("transcribe-vad-threshold").value = opts.vad_threshold || "-30";
  byId("transcribe-vad-min-silence").value = opts.vad_min_silence || "0.3";
  state.glossaryRows = parseGlossaryTextToRows(opts.transcribe_glossary || "");
  state.glossaryDirty = false;
  setGlossaryEditing(null);
  renderGlossaryEditor();
  if (opts.compare_all) {
    byId("transcribe-mode").value = "compare";
  }
  if ((opts.transcribe_guided_mode || "craig_long_best") !== "manual") {
    applyGuidedPreset(opts.transcribe_guided_mode || "craig_long_best");
  }
  applyBackendAwareMode();
  applyInputAwareDefaults();
  updateModelHelp();
  updateGuidedHelp();
}

function init() {
  logClient("info", "transcribe.js iniciado");
  logElementPresence([
    "file-input",
    "clear-files",
    "pick-local-files",
    "select-all-files",
    "deselect-all-files",
    "selected-files-count",
    "file-list",
    "start-transcribe",
    "resume-transcribe",
    "open-folder",
    "browse-folder",
    "cleanup-transcribe",
    "output-dir",
    "drop-zone",
    "jobs-controls",
    "jobs",
    "transcribe-model",
    "transcribe-language",
    "transcribe-diarize",
    "transcribe-threads",
    "transcribe-beam",
    "transcribe-max-len",
    "transcribe-mode",
    "transcribe-profile",
    "transcribe-guided-mode",
    "transcribe-guided-help",
    "transcribe-backend",
    "transcribe-normalize",
    "transcribe-vad",
    "transcribe-vad-threshold",
    "transcribe-vad-min-silence",
    "transcribe-chunk-seconds",
    "transcribe-chunk-overlap-seconds",
    "transcribe-device",
    "transcribe-compute-type",
    "whisperx-batch-size",
    "transcribe-initial-prompt",
    "transcribe-output-json",
    "transcribe-glossary-list",
    "glossary-add-row",
  ]);

  byId("file-input").addEventListener("change", (e) => {
    logClient("info", "file-input change");
    handleFiles(e.target.files, []);
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
      state.files.forEach((f) => state.selectedFileKeys.add(fileKey(f)));
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
  const pickLocalBtn = byId("pick-local-files");
  if (pickLocalBtn) {
    pickLocalBtn.addEventListener("click", () => {
      logClick("pick-local-files");
      pickLocalFiles();
    });
  }
  const drop = byId("drop-zone");
  drop.addEventListener("dragover", (e) => {
    e.preventDefault();
  });
  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    logClient("info", "drop-zone drop");
    const paths = droppedPathsFromEvent(e);
    handleFiles(e.dataTransfer.files, paths);
  });
  drop.addEventListener("click", () => {
    logClick("drop-zone");
    pickLocalFiles().catch(() => {
      byId("file-input").click();
    });
  });
  document.addEventListener("dragover", (e) => e.preventDefault());
  document.addEventListener("drop", (e) => e.preventDefault());

  byId("start-transcribe").addEventListener("click", () => {
    logClick("start-transcribe");
    startTranscribe();
  });
  byId("open-folder").addEventListener("click", () => {
    logClick("open-folder");
    openFolder();
  });
  byId("resume-transcribe").addEventListener("click", () => {
    logClick("resume-transcribe");
    resumeLastTranscribe();
  });
  byId("cleanup-transcribe").addEventListener("click", () => {
    logClick("cleanup-transcribe");
    cleanupTranscribe();
  });
  byId("browse-folder").addEventListener("click", () => {
    logClick("browse-folder");
    browseFolder();
  });
  byId("glossary-add-row").addEventListener("click", () => {
    const row = makeGlossaryRow("", "");
    state.glossaryRows.push(row);
    markGlossaryDirty();
    beginGlossaryEdit(row.id);
  });
  const glossaryList = byId("transcribe-glossary-list");
  if (glossaryList) {
    glossaryList.addEventListener("focusout", () => {
      setTimeout(() => {
        if (isGlossaryFocusoutSuppressed()) return;
        if (!state.glossaryEditingId) return;
        const active = document.activeElement;
        if (!active || !glossaryList.contains(active)) {
          commitGlossaryIfDirty();
          setGlossaryEditing(null);
          renderGlossaryEditor();
        }
      }, 0);
    });
  }
  window.addEventListener("blur", () => {
    if (isGlossaryFocusoutSuppressed()) return;
    if (!state.glossaryEditingId) return;
    commitGlossaryIfDirty();
    setGlossaryEditing(null);
    renderGlossaryEditor();
  });
  byId("output-dir").addEventListener("change", () => {
    logClient("info", "output-dir alterado");
    saveSettings();
  });
  byId("transcribe-model").addEventListener("change", () => {
    updateModelHelp();
    byId("transcribe-guided-mode").value = "manual";
    updateGuidedHelp();
    saveSettings();
  });
  byId("transcribe-guided-mode").addEventListener("change", () => {
    const mode = byId("transcribe-guided-mode").value || "manual";
    if (mode !== "manual") {
      applyGuidedPreset(mode);
    } else {
      updateGuidedHelp();
    }
    saveSettings();
  });
  byId("transcribe-backend").addEventListener("change", () => {
    renderModels();
    applyBackendAwareMode();
    updateModelHelp();
    byId("transcribe-guided-mode").value = "manual";
    updateGuidedHelp();
    saveSettings();
  });
  byId("transcribe-language").addEventListener("change", saveSettings);
  byId("transcribe-diarize").addEventListener("change", () => {
    byId("transcribe-guided-mode").value = "manual";
    updateGuidedHelp();
    saveSettings();
  });
  byId("transcribe-threads").addEventListener("input", () => {
    byId("transcribe-guided-mode").value = "manual";
    updateGuidedHelp();
    scheduleSaveSettings(300);
  });
  byId("transcribe-beam").addEventListener("input", () => {
    byId("transcribe-guided-mode").value = "manual";
    updateGuidedHelp();
    scheduleSaveSettings(300);
  });
  byId("transcribe-max-len").addEventListener("input", () => {
    byId("transcribe-guided-mode").value = "manual";
    updateGuidedHelp();
    scheduleSaveSettings(300);
  });
  byId("transcribe-chunk-seconds").addEventListener("input", () => {
    byId("transcribe-guided-mode").value = "manual";
    updateGuidedHelp();
    scheduleSaveSettings(300);
  });
  byId("transcribe-chunk-seconds").addEventListener("blur", () => {
    const normalized = normalizeChunkInput(byId("transcribe-chunk-seconds").value, 300);
    if (normalized) {
      byId("transcribe-chunk-seconds").value = normalized;
    }
    saveSettings();
  });
  byId("transcribe-chunk-overlap-seconds").addEventListener("input", () => {
    byId("transcribe-guided-mode").value = "manual";
    updateGuidedHelp();
    scheduleSaveSettings(300);
  });
  byId("transcribe-device").addEventListener("change", () => {
    byId("transcribe-guided-mode").value = "manual";
    updateGuidedHelp();
    saveSettings();
  });
  byId("transcribe-compute-type").addEventListener("change", () => {
    byId("transcribe-guided-mode").value = "manual";
    updateGuidedHelp();
    saveSettings();
  });
  byId("whisperx-batch-size").addEventListener("input", () => {
    byId("transcribe-guided-mode").value = "manual";
    updateGuidedHelp();
    scheduleSaveSettings(300);
  });
  byId("transcribe-initial-prompt").addEventListener("input", () => {
    byId("transcribe-guided-mode").value = "manual";
    updateGuidedHelp();
    scheduleSaveSettings(300);
  });
  byId("transcribe-output-json").addEventListener("change", () => {
    byId("transcribe-guided-mode").value = "manual";
    updateGuidedHelp();
    saveSettings();
  });
  byId("transcribe-mode").addEventListener("change", () => {
    byId("transcribe-guided-mode").value = "manual";
    updateGuidedHelp();
    saveSettings();
  });
  byId("transcribe-profile").addEventListener("change", () => {
    byId("transcribe-guided-mode").value = "manual";
    applyInputAwareDefaults();
    updateGuidedHelp();
    saveSettings();
  });
  byId("transcribe-normalize").addEventListener("change", () => {
    byId("transcribe-guided-mode").value = "manual";
    updateGuidedHelp();
    saveSettings();
  });
  byId("transcribe-vad").addEventListener("change", () => {
    byId("transcribe-guided-mode").value = "manual";
    updateGuidedHelp();
    saveSettings();
  });
  byId("transcribe-vad-threshold").addEventListener("input", () => {
    byId("transcribe-guided-mode").value = "manual";
    updateGuidedHelp();
    scheduleSaveSettings(300);
  });
  byId("transcribe-vad-min-silence").addEventListener("input", () => {
    byId("transcribe-guided-mode").value = "manual";
    updateGuidedHelp();
    scheduleSaveSettings(300);
  });

  Promise.resolve()
    .then(loadModels)
    .then(renderModels)
    .then(loadSettings)
    .then(() => {
      logClient("info", "settings carregadas");
      if (!byId("output-dir").value) {
        byId("output-dir").value = "C:\\";
      }
      return saveSettings();
    })
    .then(() => renderFileList())
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

