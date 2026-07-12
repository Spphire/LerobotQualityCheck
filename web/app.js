const DEFAULT_DATASET = "/mnt/nm_dataset/dataset/giftbox_0628_1912episodes";
const STATUS_ORDER = ["reject", "pending", "accept"];
const QC_VIEW_STORAGE_KEY = "lqcp.qcViewMode";
const QC_VIEW_QUERY_KEY = "view";
const ROBOPOCKET_PRO_HEAD_CAMERA = {
  imageWidth: 640,
  imageHeight: 480,
  matrix: [
    [374.98844113, 0, 335.88487191],
    [0, 374.83395961, 264.74203765],
    [0, 0, 1],
  ],
  dist: [0.025715207, -0.0621292964, 0.0001586866, -0.0005806411, 0.0230209656],
  axisLength: 0.045,
};

function qcRouteMode(pathname = window.location.pathname) {
  if (pathname === "/" || pathname === "") {
    return "desktop";
  }
  if (pathname === "/phone" || pathname === "/phone/") {
    return "phone";
  }
  return "";
}

function readStoredQcViewMode() {
  try {
    const stored = window.localStorage.getItem(QC_VIEW_STORAGE_KEY);
    return stored === "phone" || stored === "desktop" ? stored : "";
  } catch {
    return "";
  }
}

function writeStoredQcViewMode(mode) {
  try {
    if (mode === "phone" || mode === "desktop") {
      window.localStorage.setItem(QC_VIEW_STORAGE_KEY, mode);
    } else {
      window.localStorage.removeItem(QC_VIEW_STORAGE_KEY);
    }
  } catch {
    // Storage can be blocked in private browsing; auto routing still works.
  }
}

function detectQcViewMode() {
  const width = window.innerWidth || document.documentElement?.clientWidth || window.screen?.width || 0;
  const height = window.innerHeight || document.documentElement?.clientHeight || window.screen?.height || 0;
  const userAgent = navigator.userAgent || "";
  const mobileUa = /Android|iPhone|iPod|Mobile|Windows Phone|webOS|BlackBerry/i.test(userAgent);
  const tabletUa = /iPad|Tablet/i.test(userAgent)
    || (navigator.platform === "MacIntel" && Number(navigator.maxTouchPoints || 0) > 1);
  const coarsePointer = Boolean(window.matchMedia?.("(pointer: coarse)")?.matches);
  const noHover = Boolean(window.matchMedia?.("(hover: none)")?.matches);
  const touch = Number(navigator.maxTouchPoints || 0) > 0;
  const portrait = height >= width;

  if (mobileUa || ((coarsePointer || noHover || touch) && width <= 900)) {
    return "phone";
  }
  if ((tabletUa || coarsePointer || touch) && portrait && width <= 1180) {
    return "phone";
  }
  return "desktop";
}

function searchWithoutQcView(params) {
  const next = new URLSearchParams(params);
  next.delete(QC_VIEW_QUERY_KEY);
  const text = next.toString();
  return text ? `?${text}` : "";
}

function preferredQcViewMode(params) {
  const requested = String(params.get(QC_VIEW_QUERY_KEY) || "").toLowerCase();
  if (requested === "phone" || requested === "mobile") {
    writeStoredQcViewMode("phone");
    return "phone";
  }
  if (requested === "desktop" || requested === "pc") {
    writeStoredQcViewMode("desktop");
    return "desktop";
  }
  if (requested === "auto") {
    writeStoredQcViewMode("");
    return detectQcViewMode();
  }
  return readStoredQcViewMode() || detectQcViewMode();
}

(function routeQcViewForDevice() {
  const currentMode = qcRouteMode();
  if (!currentMode) {
    return;
  }
  const params = new URLSearchParams(window.location.search);
  const preferredMode = preferredQcViewMode(params);
  const cleanSearch = searchWithoutQcView(params);
  const nextPath = preferredMode === "phone" ? "/phone" : "/";
  if (preferredMode && preferredMode !== currentMode) {
    window.location.replace(`${nextPath}${cleanSearch}${window.location.hash || ""}`);
    return;
  }
  if (params.has(QC_VIEW_QUERY_KEY)) {
    window.history.replaceState(null, "", `${window.location.pathname}${cleanSearch}${window.location.hash || ""}`);
  }
})();

const STATUS_LABELS = {
  unlabeled: "待审",
  reject: "拒绝",
  pending: "待审",
  accept: "接收",
};

function normalizeStatus(status) {
  if (status === "reject" || status === "accept") {
    return status;
  }
  return "pending";
}

const ISSUE_OPTIONS = [
  ["task_failure", "任务失败"],
  ["wrong_object", "物体错误"],
  ["missing_object", "物体缺失"],
  ["bad_grasp", "抓取失败"],
  ["dropped_object", "掉落"],
  ["camera_issue", "画面异常"],
  ["robot_issue", "机器人异常"],
  ["trajectory_issue", "轨迹异常"],
  ["too_short", "过短"],
  ["other", "其他"],
];

const urlParams = new URLSearchParams(window.location.search);
const IS_ADMIN_REVIEW = window.location.pathname.startsWith("/admin/review")
  || document.body?.dataset.mode === "admin-review";
const IS_PHONE = window.location.pathname.startsWith("/phone")
  || document.body?.dataset.mode === "phone";
const USER_STORAGE_KEY = IS_ADMIN_REVIEW ? "lqcp.adminReview.user" : "lqcp.user";
const PAGE_STORAGE_KEY = IS_ADMIN_REVIEW ? "lqcp.adminReview.page" : IS_PHONE ? "lqcp.phone.page" : "lqcp.page";

const tokenFromUrl = urlParams.get("token");
if (tokenFromUrl) {
  window.localStorage.setItem("lqcp.token", tokenFromUrl);
}
const userFromUrl = urlParams.get("user");
if (userFromUrl) {
  window.localStorage.setItem(USER_STORAGE_KEY, userFromUrl);
}
const datasetFromUrl = IS_ADMIN_REVIEW ? urlParams.get("dataset") : "";
const storedUser = window.localStorage.getItem(USER_STORAGE_KEY);
const defaultUser = IS_ADMIN_REVIEW ? "admin" : `user-${Math.random().toString(16).slice(2, 6)}`;
const initialPage = parseInt(urlParams.get("page") || window.localStorage.getItem(PAGE_STORAGE_KEY) || "1", 10);
const initialStatus = urlParams.get("status") || "all";
const initialEpisodeIndex = parseInt(urlParams.get("episode_index") || "", 10);

const state = {
  adminReview: IS_ADMIN_REVIEW,
  phone: IS_PHONE,
  token: tokenFromUrl || window.localStorage.getItem("lqcp.token") || "",
  user: userFromUrl || storedUser || defaultUser,
  datasetPath: datasetFromUrl || DEFAULT_DATASET,
  page: Number.isInteger(initialPage) && initialPage > 0 ? initialPage : 1,
  initialEpisodeIndex: Number.isInteger(initialEpisodeIndex) && initialEpisodeIndex >= 0 ? initialEpisodeIndex : null,
  pageSize: 60,
  status: IS_ADMIN_REVIEW && ["all", "pending", "accept", "reject"].includes(initialStatus) ? initialStatus : "all",
  total: 0,
  episodes: [],
  counts: null,
  users: [],
  info: null,
  current: null,
  currentIndex: null,
  selectedStatus: "pending",
  hiddenVideos: [],
  headHandOverlayCanvas: null,
  headVideoIndex: 0,
  quickVideoIndexes: { left: 0, head: 0, right: 0 },
  headVideoAspect: 16 / 9,
  curveHover: { left: null, right: null },
  modalVideoSide: null,
  isDraggingModalProgress: false,
  isDraggingProgress: false,
  syncInFlight: false,
  syncTimer: null,
  navAnchor: { listKey: "", episodeIndex: null, listIndex: -1 },
  userActionQueue: Promise.resolve(),
  userActionPending: 0,
  listRequest: 0,
  episodeRequest: 0,
  labelRequest: 0,
  trajectoryTimer: null,
  trajectory: null,
  trajectoryRequest: 0,
  lastTrajectoryHighlightFrame: null,
  lastTrajectoryHighlightAt: 0,
  framesRequest: 0,
  searchRequest: 0,
  lastPlaybackUiAt: 0,
  rejectOverlayTimer: null,
  rejectOverlayRequest: 0,
  rejectAssetAudio: null,
  rejectAssetAudioReady: false,
  rejectAssetAudioFailed: false,
  labelEffectRequest: 0,
  reviveOverlayTimer: null,
  reviveAudioContext: null,
  reviveAssetAudio: null,
  reviveAssetAudioReady: false,
  reviveAssetAudioFailed: false,
  reviveTotemAssetReady: false,
  phoneSwipe: null,
  phoneSuppressClickUntil: 0,
};

let Three3D = null;
let OrbitControls3D = null;
let trajectoryView = null;

const el = {};
const TRAJECTORY_BASE_OPACITY = 0.05;
const TRAJECTORY_ACTIVE_TRAIL_FRACTION = 0.1;

const TRAJECTORY_SERIES_CONFIG = [
  {
    id: "leftState",
    cssClass: "legend-left-state",
    label: "左 state",
    getData: (trajectory) => trajectory.left,
    colors: { core: 0xfacc15, marker: 0xfacc15 },
    radiusScale: 1,
    endpointScale: 0.8,
    opacity: TRAJECTORY_BASE_OPACITY,
    showCurrentAxes: true,
  },
  {
    id: "leftAction",
    cssClass: "legend-left-action",
    label: "左 action",
    getData: (trajectory) => trajectory.action?.left,
    colors: { core: 0xf59e0b, marker: 0xf59e0b },
    radiusScale: 0.72,
    endpointScale: 0.56,
    opacity: TRAJECTORY_BASE_OPACITY,
  },
  {
    id: "rightState",
    cssClass: "legend-right-state",
    label: "右 state",
    getData: (trajectory) => trajectory.right,
    colors: { core: 0xc084fc, marker: 0xc084fc },
    radiusScale: 1,
    endpointScale: 0.8,
    opacity: TRAJECTORY_BASE_OPACITY,
    showCurrentAxes: true,
  },
  {
    id: "rightAction",
    cssClass: "legend-right-action",
    label: "右 action",
    getData: (trajectory) => trajectory.action?.right,
    colors: { core: 0x8b5cf6, marker: 0x8b5cf6 },
    radiusScale: 0.72,
    endpointScale: 0.56,
    opacity: TRAJECTORY_BASE_OPACITY,
  },
];
const PHONE_DEFAULT_PLAYBACK_RATE = 10;

function $(id) {
  return document.getElementById(id);
}

function initElements() {
  [
    "datasetSubtitle",
    "userInput",
    "setUserButton",
    "datasetInput",
    "loadDatasetButton",
    "refreshButton",
    "totalCount",
    "markedCount",
    "rejectCount",
    "allMarkedCount",
    "progressBar",
    "statusFilter",
    "searchInput",
    "episodeList",
    "prevPageButton",
    "nextPageButton",
    "pageInfo",
    "currentStatus",
    "episodeTitle",
    "episodeMeta",
    "exportJsonlButton",
    "exportCsvButton",
    "playAllButton",
    "pauseAllButton",
    "restartAllButton",
    "speedSelect",
    "videoProgress",
    "videoTime",
    "headVideoCanvas",
    "trajectoryCanvas",
    "trajectoryState",
    "leftGripperCanvas",
    "rightGripperCanvas",
    "quickVideoLeft",
    "quickVideoHead",
    "quickVideoRight",
    "rejectButton",
    "pendingButton",
    "acceptButton",
    "issueOptions",
    "noteInput",
    "saveButton",
    "clearButton",
    "saveState",
    "headFramesStrip",
    "videoModal",
    "modalVideoTitle",
    "closeVideoModalButton",
    "modalVideo",
    "modalPlayPauseButton",
    "modalVideoProgress",
    "modalVideoTime",
    "rejectOverlay",
    "rejectCollectorName",
    "reviveOverlay",
    "reviveCollectorName",
    "reviveTotemAsset",
    "hiddenVideos",
    "phoneSettingsButton",
    "phoneDrawer",
    "phoneDrawerCloseButton",
  ].forEach((id) => {
    el[id] = $(id);
  });
}

function renderTrajectoryLegends() {
  document.querySelectorAll(".trajectory-legend").forEach((legend) => {
    legend.replaceChildren(...TRAJECTORY_SERIES_CONFIG.map((series) => {
      const item = document.createElement("span");
      item.className = series.cssClass;
      item.textContent = series.label;
      return item;
    }));
  });
}

function paramsWithDataset(params = {}) {
  const next = new URLSearchParams();
  next.set("user", state.user);
  if (state.token) {
    next.set("token", state.token);
  }
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      next.set(key, value);
    }
  });
  return next;
}

function apiUrl(path, params = {}) {
  return `${path}?${paramsWithDataset(params).toString()}`;
}

function applyDatasetPath(datasetPath) {
  const nextDatasetPath = String(datasetPath || "").trim();
  if (!nextDatasetPath) {
    return;
  }
  state.datasetPath = nextDatasetPath;
  if (el.datasetSubtitle) {
    el.datasetSubtitle.textContent = state.datasetPath;
  }
  if (el.datasetInput && el.datasetInput.value !== state.datasetPath) {
    el.datasetInput.value = state.datasetPath;
  }
}

function syncBrowserUrl() {
  const params = paramsWithDataset({
    page: state.page > 1 ? state.page : "",
    status: state.adminReview && state.status !== "all" ? state.status : "",
    episode_index: state.currentIndex !== null ? state.currentIndex : "",
  });
  window.history.replaceState(null, "", `${window.location.pathname}?${params.toString()}`);
  syncNavigationLinks();
}

function urlWithContext(path, params = {}) {
  return `${path}?${paramsWithDataset(params).toString()}`;
}

function syncNavigationLinks() {
  document.querySelectorAll("[data-context-link]").forEach((link) => {
    const path = link.getAttribute("data-context-link");
    if (path) {
      const viewMode = link.getAttribute("data-view-mode");
      link.href = urlWithContext(path, viewMode ? { view: viewMode } : {});
    }
  });
}

async function requestJson(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...(state.token ? { "X-LQCP-Token": state.token } : {}),
      ...(options.headers || {}),
    },
  });
  const text = await response.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { error: text || response.statusText };
  }
  if (!response.ok) {
    throw new Error(data.error || response.statusText);
  }
  return data;
}

async function saveUserSession() {
  await requestJson(apiUrl("/api/session"), {
    method: "POST",
    body: JSON.stringify({ user: state.user }),
  });
}

async function loadUserSession() {
  try {
    if (userFromUrl) {
      await saveUserSession();
      return;
    }
    const session = await requestJson(apiUrl("/api/session"));
    if (session.user) {
      state.user = session.user;
      window.localStorage.setItem(USER_STORAGE_KEY, state.user);
      return;
    }
    if (storedUser) {
      await saveUserSession();
    }
  } catch (error) {
    console.warn("User session restore failed", error);
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function episodeName(index) {
  return `episode_${String(index).padStart(6, "0")}`;
}

function statusLabel(status) {
  return STATUS_LABELS[normalizeStatus(status)] || STATUS_LABELS.pending;
}

function statusClass(status) {
  return normalizeStatus(status);
}

function formatNumber(value) {
  if (value === undefined || value === null || value === "") {
    return "-";
  }
  return Number(value).toLocaleString();
}

function setSaveState(message, isError = false) {
  if (!el.saveState) {
    return;
  }
  el.saveState.textContent = message || "";
  el.saveState.style.color = isError ? "var(--red)" : "var(--muted)";
}

function resizeCanvas(canvas) {
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const width = Math.max(1, Math.round(rect.width * dpr));
  const height = Math.max(1, Math.round(rect.height * dpr));
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, width: rect.width, height: rect.height };
}

function renderIssueOptions() {
  if (!el.issueOptions) {
    return;
  }
  el.issueOptions.innerHTML = ISSUE_OPTIONS.map(
    ([value, label]) => `
      <label class="issue-option">
        <input type="checkbox" value="${escapeHtml(value)}" />
        <span>${escapeHtml(label)}</span>
      </label>
    `,
  ).join("");
}

function selectedIssues() {
  if (!el.issueOptions) {
    return [];
  }
  return [...el.issueOptions.querySelectorAll("input:checked")].map((input) => input.value);
}

function setSelectedIssues(issues = []) {
  if (!el.issueOptions) {
    return;
  }
  const selected = new Set(issues);
  [...el.issueOptions.querySelectorAll("input")].forEach((input) => {
    input.checked = selected.has(input.value);
  });
}

function renderSummary(counts) {
  const total = counts?.total || 0;
  const marked = counts?.marked || 0;
  if (el.totalCount) {
    el.totalCount.textContent = formatNumber(total);
  }
  if (el.markedCount) {
    el.markedCount.textContent = formatNumber(marked);
  }
  if (el.rejectCount) {
    el.rejectCount.textContent = formatNumber(counts?.reject || 0);
  }
  if (el.allMarkedCount) {
    el.allMarkedCount.textContent = formatNumber(counts?.all_marked || 0);
  }
  const percent = total > 0 ? Math.round((marked / total) * 100) : 0;
  if (el.progressBar) {
    el.progressBar.style.width = `${percent}%`;
  }
}

function renderEpisodeList() {
  if (!el.episodeList) {
    return;
  }
  if (!state.episodes.length) {
    el.episodeList.innerHTML = `<div class="empty-state">没有匹配的 episode</div>`;
    return;
  }
  el.episodeList.innerHTML = state.episodes.map((episode) => {
    const active = episode.episode_index === state.currentIndex ? "active" : "";
    const status = statusClass(episode.status);
    const task = episode.task_description || episode.task_annotation || (episode.tasks || []).join(" / ");
    const sub = `${formatNumber(episode.length)} frames · ${episode.video_count || 0} videos`;
    const lockedBy = Array.isArray(episode.locked_by) ? episode.locked_by.filter(Boolean) : [];
    const lockText = lockedBy.length ? (lockedBy.length === 1 ? `锁 ${lockedBy[0]}` : `锁 ${lockedBy.length}`) : "";
    const lockTitle = lockedBy.length ? `正在查看: ${lockedBy.join(", ")}` : "";
    const effectiveLabel = episode.effective_label || {};
    const effectiveStatus = statusClass(effectiveLabel.status);
    const effectiveUser = effectiveLabel.user || effectiveLabel.annotator || "";
    const effectiveText = effectiveUser && (effectiveStatus === "reject" || effectiveStatus === "accept")
      ? `${statusLabel(effectiveStatus)} ${effectiveUser}`
      : "";
    const effectiveTitle = effectiveText ? `当前标注: ${statusLabel(effectiveStatus)} / ${effectiveUser}` : "";
    return `
      <button class="episode-item ${active}" data-index="${episode.episode_index}" type="button">
        <span class="episode-main">
          <span class="episode-name">${escapeHtml(episode.episode_name)}</span>
          <span class="episode-task">${escapeHtml(task || "-")}</span>
          <span class="episode-sub">${escapeHtml(sub)}</span>
        </span>
        <span class="status-stack">
          <span class="status-pill ${status}">${escapeHtml(statusLabel(status))}</span>
          ${effectiveText ? `<span class="effective-label ${effectiveStatus}" title="${escapeHtml(effectiveTitle)}">${escapeHtml(effectiveText)}</span>` : ""}
          ${lockText ? `<span class="lock-pill" title="${escapeHtml(lockTitle)}">${escapeHtml(lockText)}</span>` : ""}
        </span>
      </button>
    `;
  }).join("");
}

function renderPager() {
  const pageCount = Math.max(1, Math.ceil(state.total / state.pageSize));
  if (el.pageInfo) {
    el.pageInfo.textContent = `${state.page} / ${pageCount}`;
  }
  if (el.prevPageButton) {
    el.prevPageButton.disabled = state.page <= 1;
  }
  if (el.nextPageButton) {
    el.nextPageButton.disabled = state.page >= pageCount;
  }
}

function renderHeader(current) {
  const summary = current?.summary;
  const episode = current?.episode;
  const label = current?.label || {};
  const status = statusClass(label.status || summary?.status);
  const labelUser = label.user || label.annotator || "";
  const showStatusUser = state.phone && labelUser && (status === "reject" || status === "accept");
  el.currentStatus.className = `status-pill ${status}`;
  el.currentStatus.textContent = showStatusUser ? `${statusLabel(status)} · ${labelUser}` : statusLabel(status);
  el.currentStatus.title = showStatusUser ? `${statusLabel(status)} / ${labelUser}` : statusLabel(status);
  if (!episode) {
    el.episodeTitle.textContent = "选择一个 episode";
    el.episodeMeta.textContent = "";
    return;
  }
  el.episodeTitle.textContent = state.phone
    ? `Episode ${episode.episode_index}`
    : episode.episode_name || episodeName(episode.episode_index);
  const task = episode.task_description || episode.task_annotation || (episode.tasks || []).join(" / ");
  const bits = state.phone
    ? [
      episode.episode_name || episodeName(episode.episode_index),
      `${formatNumber(episode.length)} frames`,
      task,
    ].filter(Boolean)
    : [
      `index ${episode.episode_index}`,
      `${formatNumber(episode.length)} frames`,
      `${current.videos.length} videos`,
      `user ${state.user}`,
      task,
    ].filter(Boolean);
  el.episodeMeta.textContent = bits.join(" · ");
}

function renderHeadFramePlaceholders(count = 6) {
  if (!el.headFramesStrip) {
    return;
  }
  el.headFramesStrip.innerHTML = Array.from({ length: count }, (_, index) => `
    <figure class="frame-thumb">
      <div class="frame-placeholder">${index + 1}</div>
      <figcaption>--</figcaption>
    </figure>
  `).join("");
}

function seekVideo(video, time) {
  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(() => {
      cleanup();
      reject(new Error("seek timeout"));
    }, 6000);
    function cleanup() {
      window.clearTimeout(timeout);
      video.removeEventListener("seeked", onSeeked);
      video.removeEventListener("error", onError);
    }
    function onSeeked() {
      cleanup();
      resolve();
    }
    function onError() {
      cleanup();
      reject(new Error("video seek failed"));
    }
    video.addEventListener("seeked", onSeeked, { once: true });
    video.addEventListener("error", onError, { once: true });
    video.currentTime = time;
  });
}

async function renderHeadFrames(current) {
  if (!el.headFramesStrip) {
    return;
  }
  const requestId = state.framesRequest + 1;
  state.framesRequest = requestId;
  renderHeadFramePlaceholders(6);
  const headVideo = (current?.videos || []).find((video) => video.camera === "image" || video.key === "observation.images.image")
    || current?.videos?.[0];
  if (!headVideo) {
    el.headFramesStrip.innerHTML = `<div class="frame-placeholder">无头部视频</div>`;
    return;
  }

  const video = document.createElement("video");
  video.src = headVideo.url;
  video.muted = true;
  video.preload = "auto";
  video.crossOrigin = "same-origin";
  await new Promise((resolve, reject) => {
    const timeout = window.setTimeout(() => reject(new Error("metadata timeout")), 8000);
    video.addEventListener("loadedmetadata", () => {
      window.clearTimeout(timeout);
      resolve();
    }, { once: true });
    video.addEventListener("error", () => {
      window.clearTimeout(timeout);
      reject(new Error("video metadata failed"));
    }, { once: true });
  });

  if (state.framesRequest !== requestId) {
    return;
  }
  const duration = Number.isFinite(video.duration) && video.duration > 0 ? video.duration : 1;
  const ratios = [0.02, 0.18, 0.34, 0.5, 0.66, 0.82, 0.98];
  const canvas = document.createElement("canvas");
  const width = 180;
  const height = Math.max(1, Math.round(width / (video.videoWidth / video.videoHeight || 4 / 3)));
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  const frames = [];
  for (const ratio of ratios) {
    if (state.framesRequest !== requestId) {
      return;
    }
    await seekVideo(video, Math.min(duration - 0.05, Math.max(0, duration * ratio)));
    ctx.fillStyle = "#0b0f14";
    ctx.fillRect(0, 0, width, height);
    ctx.drawImage(video, 0, 0, width, height);
    frames.push({
      label: `${Math.round(ratio * 100)}%`,
      src: canvas.toDataURL("image/jpeg", 0.82),
    });
  }
  if (state.framesRequest !== requestId) {
    return;
  }
  el.headFramesStrip.innerHTML = frames.map((frame) => `
    <figure class="frame-thumb">
      <img src="${frame.src}" alt="head frame ${frame.label}" />
      <figcaption>${frame.label}</figcaption>
    </figure>
  `).join("");
}

function renderLabelForm(label = {}) {
  state.selectedStatus = normalizeStatus(label.status);
  setSelectedIssues(label.issues || []);
  if (el.noteInput) {
    el.noteInput.value = label.note || "";
  }
  renderStatusButtons();
}

function renderStatusButtons() {
  [
    ["reject", el.rejectButton],
    ["pending", el.pendingButton],
    ["accept", el.acceptButton],
  ].forEach(([status, button]) => {
    button?.classList.toggle("active", state.selectedStatus === status);
  });
}

function stopHiddenVideos() {
  state.hiddenVideos.forEach((video) => {
    video.pause();
    video.onloadedmetadata = null;
    video.onloadeddata = null;
    video.onseeked = null;
    video.onended = null;
    video.removeAttribute("src");
    video.load();
  });
  state.hiddenVideos = [];
  state.headVideoIndex = 0;
  updateProgressUI(0);
}

async function collectorNameForEpisode(episodeIndex) {
  try {
    const data = await requestJson(apiUrl("/api/source_metadata", { episode_index: episodeIndex }));
    return data.source_metadata?.collector || "未知采集人";
  } catch (error) {
    console.debug("collector lookup failed", error);
    return "未知采集人";
  }
}

function stopRejectOverlay() {
  if (state.rejectOverlayTimer) {
    window.clearTimeout(state.rejectOverlayTimer);
    state.rejectOverlayTimer = null;
  }
  if (el.rejectOverlay) {
    el.rejectOverlay.classList.remove("playing");
    el.rejectOverlay.hidden = true;
  }
}

function stopReviveOverlay() {
  if (state.reviveOverlayTimer) {
    window.clearTimeout(state.reviveOverlayTimer);
    state.reviveOverlayTimer = null;
  }
  if (el.reviveOverlay) {
    el.reviveOverlay.classList.remove("playing");
    el.reviveOverlay.hidden = true;
  }
}

function playRejectOverlay(collectorName) {
  if (!el.rejectOverlay || !el.rejectCollectorName) {
    return;
  }
  stopReviveOverlay();
  stopRejectOverlay();
  playRejectSound();
  el.rejectCollectorName.textContent = collectorName || "未知采集人";
  el.rejectCollectorName.setAttribute("data-name", collectorName || "未知采集人");
  el.rejectOverlay.classList.remove("playing");
  el.rejectOverlay.hidden = true;
  void el.rejectOverlay.offsetWidth;
  el.rejectOverlay.hidden = false;
  void el.rejectOverlay.offsetWidth;
  el.rejectOverlay.classList.add("playing");
  state.rejectOverlayTimer = window.setTimeout(() => {
    el.rejectOverlay.classList.remove("playing");
    el.rejectOverlay.hidden = true;
    state.rejectOverlayTimer = null;
  }, 1180);
}

function createEffectNoiseBuffer(context, duration, envelope = (ratio) => 1 - ratio) {
  const length = Math.max(1, Math.floor(context.sampleRate * duration));
  const buffer = context.createBuffer(1, length, context.sampleRate);
  const channel = buffer.getChannelData(0);
  for (let i = 0; i < length; i += 1) {
    const ratio = i / Math.max(1, length - 1);
    channel[i] = (Math.random() * 2 - 1) * envelope(ratio);
  }
  return buffer;
}

function audioContextForEffects() {
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextClass) {
    return null;
  }
  if (!state.reviveAudioContext || state.reviveAudioContext.state === "closed") {
    state.reviveAudioContext = new AudioContextClass();
  }
  if (state.reviveAudioContext.state === "suspended") {
    state.reviveAudioContext.resume().catch(() => {});
  }
  return state.reviveAudioContext;
}

function playRejectSound() {
  if (playRejectAssetSound()) {
    return;
  }
  playRejectGeneratedSound();
}

function playRejectGeneratedSound() {
  const context = audioContextForEffects();
  if (!context) {
    return;
  }
  const now = context.currentTime;
  const master = context.createGain();
  master.gain.setValueAtTime(0.0001, now);
  master.gain.exponentialRampToValueAtTime(0.48, now + 0.012);
  master.gain.exponentialRampToValueAtTime(0.0001, now + 0.62);
  master.connect(context.destination);

  const slash = context.createBufferSource();
  const slashFilter = context.createBiquadFilter();
  const slashGain = context.createGain();
  slash.buffer = createEffectNoiseBuffer(context, 0.34, (ratio) => Math.pow(1 - ratio, 1.6));
  slashFilter.type = "bandpass";
  slashFilter.frequency.setValueAtTime(5200, now);
  slashFilter.frequency.exponentialRampToValueAtTime(1200, now + 0.28);
  slashFilter.Q.setValueAtTime(5.5, now);
  slashGain.gain.setValueAtTime(0.0001, now);
  slashGain.gain.exponentialRampToValueAtTime(0.3, now + 0.018);
  slashGain.gain.exponentialRampToValueAtTime(0.0001, now + 0.34);
  slash.connect(slashFilter);
  slashFilter.connect(slashGain);
  slashGain.connect(master);
  slash.start(now);
  slash.stop(now + 0.36);

  const edge = context.createOscillator();
  const edgeGain = context.createGain();
  edge.type = "sawtooth";
  edge.frequency.setValueAtTime(1360, now + 0.02);
  edge.frequency.exponentialRampToValueAtTime(460, now + 0.22);
  edgeGain.gain.setValueAtTime(0.0001, now + 0.02);
  edgeGain.gain.exponentialRampToValueAtTime(0.16, now + 0.035);
  edgeGain.gain.exponentialRampToValueAtTime(0.0001, now + 0.24);
  edge.connect(edgeGain);
  edgeGain.connect(master);
  edge.start(now + 0.02);
  edge.stop(now + 0.28);

  const impact = context.createOscillator();
  const impactGain = context.createGain();
  impact.type = "triangle";
  impact.frequency.setValueAtTime(120, now + 0.12);
  impact.frequency.exponentialRampToValueAtTime(46, now + 0.44);
  impactGain.gain.setValueAtTime(0.0001, now + 0.1);
  impactGain.gain.exponentialRampToValueAtTime(0.42, now + 0.135);
  impactGain.gain.exponentialRampToValueAtTime(0.0001, now + 0.55);
  impact.connect(impactGain);
  impactGain.connect(master);
  impact.start(now + 0.1);
  impact.stop(now + 0.58);
}

function initReviveAssets() {
  if (el.reviveTotemAsset) {
    if (el.reviveTotemAsset.complete && el.reviveTotemAsset.naturalWidth > 0) {
      state.reviveTotemAssetReady = true;
      el.reviveOverlay?.classList.add("has-totem-asset");
    }
    el.reviveTotemAsset.addEventListener("load", () => {
      state.reviveTotemAssetReady = true;
      el.reviveOverlay?.classList.add("has-totem-asset");
    }, { once: true });
    el.reviveTotemAsset.addEventListener("error", () => {
      state.reviveTotemAssetReady = false;
      el.reviveOverlay?.classList.remove("has-totem-asset");
    }, { once: true });
  }

  const audio = new Audio("/dev-assets/minecraft/use_totem.ogg");
  audio.preload = "auto";
  audio.volume = 0.82;
  audio.addEventListener("canplaythrough", () => {
    state.reviveAssetAudioReady = true;
  }, { once: true });
  audio.addEventListener("error", () => {
    state.reviveAssetAudioFailed = true;
  }, { once: true });
  state.reviveAssetAudio = audio;
}

function initRejectAssets() {
  const audio = new Audio("/dev-assets/effects/kill_slash.wav");
  audio.preload = "auto";
  audio.volume = 0.86;
  audio.addEventListener("canplaythrough", () => {
    state.rejectAssetAudioReady = true;
  }, { once: true });
  audio.addEventListener("error", () => {
    state.rejectAssetAudioFailed = true;
  }, { once: true });
  state.rejectAssetAudio = audio;
}

function playEffectAssetSound(audio, markFailed, fallback) {
  if (!audio) {
    return false;
  }
  try {
    audio.pause();
    audio.currentTime = 0;
    const played = audio.play();
    if (played && typeof played.catch === "function") {
      played.catch(() => {
        markFailed();
        fallback?.();
      });
    }
    return true;
  } catch (error) {
    markFailed();
    return false;
  }
}

function playRejectAssetSound() {
  if (state.rejectAssetAudioFailed) {
    return false;
  }
  return playEffectAssetSound(
    state.rejectAssetAudio,
    () => { state.rejectAssetAudioFailed = true; },
    playRejectGeneratedSound,
  );
}

function playReviveAssetSound() {
  if (state.reviveAssetAudioFailed) {
    return false;
  }
  return playEffectAssetSound(
    state.reviveAssetAudio,
    () => { state.reviveAssetAudioFailed = true; },
    playReviveGeneratedSound,
  );
}

function playReviveSound() {
  if (playReviveAssetSound()) {
    return;
  }
  playReviveGeneratedSound();
}

function playReviveGeneratedSound() {
  const context = audioContextForEffects();
  if (!context) {
    return;
  }
  const now = context.currentTime;
  const master = context.createGain();
  master.gain.setValueAtTime(0.0001, now);
  master.gain.exponentialRampToValueAtTime(0.36, now + 0.02);
  master.gain.exponentialRampToValueAtTime(0.0001, now + 1.08);
  master.connect(context.destination);

  const notes = [
    { frequency: 523.25, start: 0, duration: 0.22 },
    { frequency: 659.25, start: 0.1, duration: 0.24 },
    { frequency: 783.99, start: 0.2, duration: 0.3 },
    { frequency: 1046.5, start: 0.38, duration: 0.42 },
  ];
  notes.forEach((note) => {
    const osc = context.createOscillator();
    const gain = context.createGain();
    osc.type = "triangle";
    osc.frequency.setValueAtTime(note.frequency, now + note.start);
    osc.frequency.exponentialRampToValueAtTime(note.frequency * 1.12, now + note.start + note.duration);
    gain.gain.setValueAtTime(0.0001, now + note.start);
    gain.gain.exponentialRampToValueAtTime(0.18, now + note.start + 0.025);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + note.start + note.duration);
    osc.connect(gain);
    gain.connect(master);
    osc.start(now + note.start);
    osc.stop(now + note.start + note.duration + 0.03);
  });

  const noise = context.createBufferSource();
  const noiseGain = context.createGain();
  const filter = context.createBiquadFilter();
  noise.buffer = createEffectNoiseBuffer(context, 0.32);
  filter.type = "bandpass";
  filter.frequency.setValueAtTime(3400, now + 0.05);
  filter.Q.setValueAtTime(1.8, now + 0.05);
  noiseGain.gain.setValueAtTime(0.0001, now + 0.05);
  noiseGain.gain.exponentialRampToValueAtTime(0.11, now + 0.09);
  noiseGain.gain.exponentialRampToValueAtTime(0.0001, now + 0.44);
  noise.connect(filter);
  filter.connect(noiseGain);
  noiseGain.connect(master);
  noise.start(now + 0.05);
  noise.stop(now + 0.42);
}

function unlockEffectAudioContext() {
  const context = audioContextForEffects();
  if (!context) {
    return;
  }
  const now = context.currentTime;
  const source = context.createBufferSource();
  const gain = context.createGain();
  source.buffer = createEffectNoiseBuffer(context, 0.012, () => 0);
  gain.gain.setValueAtTime(0.0001, now);
  source.connect(gain);
  gain.connect(context.destination);
  source.start(now);
  source.stop(now + 0.014);
}

function primeEffectAudio() {
  unlockEffectAudioContext();
  state.rejectAssetAudio?.load();
  state.reviveAssetAudio?.load();
}

function playReviveOverlay(collectorName) {
  if (!el.reviveOverlay) {
    return;
  }
  stopRejectOverlay();
  stopReviveOverlay();
  if (el.reviveCollectorName) {
    const displayName = collectorName || "未知采集人";
    el.reviveCollectorName.textContent = displayName;
    el.reviveCollectorName.setAttribute("data-name", displayName);
  }
  playReviveSound();
  el.reviveOverlay.classList.remove("playing");
  el.reviveOverlay.hidden = true;
  void el.reviveOverlay.offsetWidth;
  el.reviveOverlay.hidden = false;
  void el.reviveOverlay.offsetWidth;
  el.reviveOverlay.classList.add("playing");
  state.reviveOverlayTimer = window.setTimeout(() => {
    el.reviveOverlay.classList.remove("playing");
    el.reviveOverlay.hidden = true;
    state.reviveOverlayTimer = null;
  }, 1340);
}

function findHeadVideoIndex(videos = []) {
  const exactIndex = videos.findIndex((video) => video.camera === "image" || video.key === "observation.images.image");
  if (exactIndex >= 0) {
    return exactIndex;
  }
  const fuzzyIndex = videos.findIndex((video) => {
    const text = `${video.camera || ""} ${video.key || ""}`.toLowerCase();
    return text.includes("head") || text.includes("ego") || text.endsWith(".image") || text.includes("images.image");
  });
  return fuzzyIndex >= 0 ? fuzzyIndex : 0;
}

function findWristVideoIndex(videos = [], side = "left") {
  const normalized = videos.map((video, index) => ({
    index,
    text: `${video.camera || ""} ${video.key || ""}`.toLowerCase(),
  }));
  const exactNeedle = side === "left" ? "wrist_image_1" : "wrist_image_2";
  const sideNeedle = side === "left" ? "left" : "right";
  const exact = normalized.find((item) => item.text.includes(exactNeedle) || item.text.includes(sideNeedle));
  if (exact) {
    return exact.index;
  }
  const wrists = normalized.filter((item) => item.text.includes("wrist"));
  return (wrists[side === "left" ? 0 : 1] || wrists[0])?.index ?? 0;
}

function renderCameraCanvases(videos) {
  stopHiddenVideos();
  resetQuickVideoMediaLayout();
  state.headVideoIndex = findHeadVideoIndex(videos);
  state.quickVideoIndexes = {
    left: findWristVideoIndex(videos, "left"),
    head: state.headVideoIndex,
    right: findWristVideoIndex(videos, "right"),
  };
  setHeadVideoSize(16 / 9);
  if (!videos.length) {
    syncQuickVideoLayout();
    drawQuickVideoCanvases();
    return;
  }
  quickVideoEntries().forEach(([key, video, message]) => {
    if (!video) {
      return;
    }
    const index = state.quickVideoIndexes[key];
    const videoInfo = videos[index];
    if (!videoInfo) {
      video.pause();
      video.removeAttribute("src");
      video.load();
      video.dataset.emptyMessage = message;
      syncQuickVideoLayout();
      return;
    }
    video.src = videoInfo.url;
    video.muted = true;
    video.loop = true;
    video.preload = "metadata";
    video.playsInline = true;
    video.crossOrigin = "same-origin";
    video.onloadedmetadata = () => {
      if (index === state.headVideoIndex && video.videoWidth && video.videoHeight && el.headVideoCanvas) {
        el.headVideoCanvas.style.setProperty("--video-aspect", `${video.videoWidth} / ${video.videoHeight}`);
        setHeadVideoSize(video.videoWidth / video.videoHeight);
      }
      syncQuickVideoAspect(index, video);
      video.playbackRate = currentPlaybackRate();
      syncVideoTimes(true);
      drawQuickVideoCanvases();
    };
    video.onloadeddata = () => {
      drawQuickVideoCanvases();
    };
    video.onseeked = () => {
      drawQuickVideoCanvases();
    };
    video.onended = () => {
      setAllVideoProgress(0);
      playAll();
    };
    state.hiddenVideos[index] = video;
    video.load();
  });
  updateProgressUI(0);
  syncQuickVideoLayout();
  drawQuickVideoCanvases();
  applyPlaybackRate();
  window.setTimeout(playAll, 80);
}

function setHeadVideoSize(aspect) {
  if (!el.headVideoCanvas) {
    return;
  }
  const safeAspect = Number.isFinite(aspect) && aspect > 0 ? aspect : 16 / 9;
  state.headVideoAspect = safeAspect;
  const rootStyle = window.getComputedStyle(document.documentElement);
  const stripHeight = el.headFramesStrip?.getBoundingClientRect().height || 0;
  const fallbackHeight = Number.parseFloat(rootStyle.getPropertyValue("--frame-media-height")) || 112;
  const frameHeight = Math.max(112, stripHeight > 50 ? stripHeight - 36 : fallbackHeight);
  const width = Math.round(Math.max(160, Math.min(520, frameHeight * safeAspect)));
  const panel = el.headVideoCanvas.closest(".head-video-panel");
  el.headVideoCanvas.style.setProperty("--video-aspect", `${safeAspect}`);
  panel?.style.setProperty("--head-video-width", `${width}px`);
}

function drawCanvasMessage(canvas, message) {
  if (!canvas) {
    return;
  }
  const { ctx, width, height } = resizeCanvas(canvas);
  ctx.fillStyle = "#0b0f14";
  ctx.fillRect(0, 0, width, height);
  ctx.fillStyle = "#9aa6b2";
  ctx.font = "13px sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(message, width / 2, height / 2);
}

function drawVideoToCanvas(canvas, video, emptyMessage = "无视频") {
  if (!canvas) {
    return;
  }
  const { ctx, width, height } = resizeCanvas(canvas);
  ctx.fillStyle = "#0b0f14";
  ctx.fillRect(0, 0, width, height);
  if (!video) {
    ctx.fillStyle = "#9aa6b2";
    ctx.font = "13px sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(emptyMessage, width / 2, height / 2);
    return;
  }
  if (video.readyState < 2 || !video.videoWidth || !video.videoHeight) {
    ctx.fillStyle = "#9aa6b2";
    ctx.font = "13px sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("加载中", width / 2, height / 2);
    return;
  }
  const scale = Math.min(width / video.videoWidth, height / video.videoHeight);
  const drawWidth = video.videoWidth * scale;
  const drawHeight = video.videoHeight * scale;
  const x = (width - drawWidth) / 2;
  const y = (height - drawHeight) / 2;
  ctx.drawImage(video, x, y, drawWidth, drawHeight);
}

function robopocketProDeviceType(value = "") {
  const normalized = String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "");
  return normalized.includes("robopocket20pro")
    || normalized.includes("robopocket2pro")
    || normalized.includes("robopocketpro");
}

function currentDeviceType() {
  return state.trajectory?.metadata?.device_type
    || state.trajectory?.device_type
    || state.current?.episode?.device_type
    || state.info?.device_type
    || "";
}

function shouldDrawHeadHandOverlay() {
  return Boolean(state.trajectory && robopocketProDeviceType(currentDeviceType()));
}

function ensureHeadHandOverlayCanvas() {
  if (state.headHandOverlayCanvas?.isConnected) {
    return state.headHandOverlayCanvas;
  }
  const video = el.quickVideoHead;
  const card = video?.closest(".quick-video-card");
  if (!video || !card) {
    return null;
  }
  const canvas = document.createElement("canvas");
  canvas.className = "head-hand-overlay";
  canvas.setAttribute("aria-hidden", "true");
  card.appendChild(canvas);
  state.headHandOverlayCanvas = canvas;
  return canvas;
}

function clearHeadHandOverlay() {
  const canvas = state.headHandOverlayCanvas;
  if (!canvas) {
    return;
  }
  const ctx = canvas.getContext("2d");
  if (ctx) {
    ctx.clearRect(0, 0, canvas.width || 0, canvas.height || 0);
  }
  canvas.hidden = true;
}

function headVideoDrawRect(video) {
  const rect = video?.getBoundingClientRect();
  if (!rect || rect.width <= 0 || rect.height <= 0 || !video.videoWidth || !video.videoHeight) {
    return null;
  }
  const mediaAspect = video.videoWidth / video.videoHeight;
  const boxAspect = rect.width / rect.height;
  if (boxAspect > mediaAspect) {
    const width = rect.height * mediaAspect;
    return {
      left: rect.left + (rect.width - width) / 2,
      top: rect.top,
      width,
      height: rect.height,
    };
  }
  const height = rect.width / mediaAspect;
  return {
    left: rect.left,
    top: rect.top + (rect.height - height) / 2,
    width: rect.width,
    height,
  };
}

function positionHeadHandOverlay(canvas, video) {
  const card = video.closest(".quick-video-card");
  const cardRect = card?.getBoundingClientRect();
  const drawRect = headVideoDrawRect(video);
  if (!cardRect || !drawRect) {
    return null;
  }
  const dpr = Math.max(1, Math.min(3, window.devicePixelRatio || 1));
  const width = Math.max(1, Math.round(drawRect.width));
  const height = Math.max(1, Math.round(drawRect.height));
  const backingWidth = Math.max(1, Math.round(width * dpr));
  const backingHeight = Math.max(1, Math.round(height * dpr));
  canvas.hidden = false;
  canvas.style.left = `${drawRect.left - cardRect.left}px`;
  canvas.style.top = `${drawRect.top - cardRect.top}px`;
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  if (canvas.width !== backingWidth || canvas.height !== backingHeight) {
    canvas.width = backingWidth;
    canvas.height = backingHeight;
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  return { ctx, width, height };
}

function mat3Transpose(matrix) {
  return [
    [matrix[0][0], matrix[1][0], matrix[2][0]],
    [matrix[0][1], matrix[1][1], matrix[2][1]],
    [matrix[0][2], matrix[1][2], matrix[2][2]],
  ];
}

function mat3Vec(matrix, vector) {
  return [
    matrix[0][0] * vector[0] + matrix[0][1] * vector[1] + matrix[0][2] * vector[2],
    matrix[1][0] * vector[0] + matrix[1][1] * vector[1] + matrix[1][2] * vector[2],
    matrix[2][0] * vector[0] + matrix[2][1] * vector[1] + matrix[2][2] * vector[2],
  ];
}

function mat3Multiply(a, b) {
  const out = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
  for (let row = 0; row < 3; row += 1) {
    for (let col = 0; col < 3; col += 1) {
      out[row][col] = a[row][0] * b[0][col] + a[row][1] * b[1][col] + a[row][2] * b[2][col];
    }
  }
  return out;
}

function rotationMatrixWxyz(quat) {
  if (!validQuat(quat)) {
    return null;
  }
  const [w, x, y, z] = normalizeQuat(quat);
  return [
    [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
    [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
    [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
  ];
}

function projectRobopocketHeadPoint(point, width, height) {
  if (!Array.isArray(point) || point.length < 3 || point[2] <= 1e-4) {
    return null;
  }
  const sx = width / ROBOPOCKET_PRO_HEAD_CAMERA.imageWidth;
  const sy = height / ROBOPOCKET_PRO_HEAD_CAMERA.imageHeight;
  const fx = ROBOPOCKET_PRO_HEAD_CAMERA.matrix[0][0] * sx;
  const fy = ROBOPOCKET_PRO_HEAD_CAMERA.matrix[1][1] * sy;
  const cx = ROBOPOCKET_PRO_HEAD_CAMERA.matrix[0][2] * sx;
  const cy = ROBOPOCKET_PRO_HEAD_CAMERA.matrix[1][2] * sy;
  const [k1, k2, p1, p2, k3] = ROBOPOCKET_PRO_HEAD_CAMERA.dist;
  const x = point[0] / point[2];
  const y = point[1] / point[2];
  const r2 = x * x + y * y;
  const r4 = r2 * r2;
  const r6 = r4 * r2;
  const radial = 1 + k1 * r2 + k2 * r4 + k3 * r6;
  const xDist = x * radial + 2 * p1 * x * y + p2 * (r2 + 2 * x * x);
  const yDist = y * radial + p1 * (r2 + 2 * y * y) + 2 * p2 * x * y;
  const u = fx * xDist + cx;
  const v = fy * yDist + cy;
  if (!Number.isFinite(u) || !Number.isFinite(v)) {
    return null;
  }
  return { x: u, y: v, inFrame: u >= -30 && u <= width + 30 && v >= -30 && v <= height + 30 };
}

function wristCameraPose(side, index) {
  const trajectory = state.trajectory;
  const egoPoint = trajectory?.ego?.points?.[index];
  const egoQuat = trajectory?.ego?.quaternions?.[index];
  const sidePoint = trajectory?.[side]?.points?.[index];
  const sideQuat = trajectory?.[side]?.quaternions?.[index];
  if (!validPoint(egoPoint) || !validQuat(egoQuat) || !validPoint(sidePoint) || !validQuat(sideQuat)) {
    return null;
  }
  const egoRotation = rotationMatrixWxyz(egoQuat);
  const wristRotation = rotationMatrixWxyz(sideQuat);
  if (!egoRotation || !wristRotation) {
    return null;
  }
  const cameraFromEgo = [[1, 0, 0], [0, -1, 0], [0, 0, -1]];
  const egoInv = mat3Transpose(egoRotation);
  const delta = [
    sidePoint[0] - egoPoint[0],
    sidePoint[1] - egoPoint[1],
    sidePoint[2] - egoPoint[2],
  ];
  const center = mat3Vec(cameraFromEgo, mat3Vec(egoInv, delta));
  const axes = mat3Multiply(cameraFromEgo, mat3Multiply(egoInv, wristRotation));
  return { center, axes };
}

function drawProjectedTrail(ctx, side, frame, width, height, color) {
  const frames = state.trajectory?.frames || [];
  const points = state.trajectory?.[side]?.points || [];
  if (!points.length) {
    return;
  }
  const sample = trajectorySample(points, frames, frame);
  if (sample.index < 0) {
    return;
  }
  const projected = [];
  const start = Math.max(0, sample.index - 24);
  for (let index = start; index <= sample.index; index += 1) {
    const pose = wristCameraPose(side, index);
    const point = pose ? projectRobopocketHeadPoint(pose.center, width, height) : null;
    if (point?.inFrame) {
      projected.push(point);
    } else if (projected.length) {
      projected.push(null);
    }
  }
  ctx.save();
  ctx.lineWidth = 2;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.strokeStyle = color.trail;
  ctx.shadowColor = color.glow;
  ctx.shadowBlur = 8;
  ctx.beginPath();
  let hasTrailPoint = false;
  projected.forEach((point) => {
    if (!point) {
      hasTrailPoint = false;
      return;
    }
    if (!hasTrailPoint) {
      ctx.moveTo(point.x, point.y);
      hasTrailPoint = true;
    } else {
      ctx.lineTo(point.x, point.y);
    }
  });
  if (hasTrailPoint) {
    ctx.stroke();
  }
  ctx.restore();
}

function drawProjectedWrist(ctx, side, frame, width, height, color) {
  const frames = state.trajectory?.frames || [];
  const points = state.trajectory?.[side]?.points || [];
  const sample = trajectorySample(points, frames, frame);
  if (sample.index < 0) {
    return;
  }
  const pose = wristCameraPose(side, sample.index);
  if (!pose) {
    return;
  }
  const center = projectRobopocketHeadPoint(pose.center, width, height);
  if (!center?.inFrame) {
    return;
  }
  const axisLength = ROBOPOCKET_PRO_HEAD_CAMERA.axisLength;
  const axisColors = ["#ff453a", "#32d74b", "#0a84ff"];
  ctx.save();
  ctx.lineCap = "round";
  [0, 1, 2].forEach((axis) => {
    const axisEnd = [
      pose.center[0] + pose.axes[0][axis] * axisLength,
      pose.center[1] + pose.axes[1][axis] * axisLength,
      pose.center[2] + pose.axes[2][axis] * axisLength,
    ];
    const projected = projectRobopocketHeadPoint(axisEnd, width, height);
    if (!projected) {
      return;
    }
    ctx.strokeStyle = axisColors[axis];
    ctx.lineWidth = 2.2;
    ctx.shadowColor = "rgba(0, 0, 0, 0.4)";
    ctx.shadowBlur = 2;
    ctx.beginPath();
    ctx.moveTo(center.x, center.y);
    ctx.lineTo(projected.x, projected.y);
    ctx.stroke();
    ctx.fillStyle = axisColors[axis];
    ctx.beginPath();
    ctx.arc(projected.x, projected.y, 2.3, 0, Math.PI * 2);
    ctx.fill();
  });
  ctx.shadowColor = color.glow;
  ctx.shadowBlur = 10;
  ctx.fillStyle = color.core;
  ctx.beginPath();
  ctx.arc(center.x, center.y, 5, 0, Math.PI * 2);
  ctx.fill();
  ctx.shadowBlur = 0;
  ctx.strokeStyle = "rgba(255, 255, 255, 0.92)";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.arc(center.x, center.y, 8, 0, Math.PI * 2);
  ctx.stroke();
  ctx.fillStyle = color.core;
  ctx.font = "600 12px system-ui, -apple-system, BlinkMacSystemFont, sans-serif";
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  ctx.fillText(side === "left" ? "L" : "R", center.x + 10, center.y - 8);
  ctx.restore();
}

function drawHeadVideoCanvas() {
  const video = el.quickVideoHead;
  const canvas = ensureHeadHandOverlayCanvas();
  if (!video || !canvas || !shouldDrawHeadHandOverlay() || video.readyState < 2 || !video.videoWidth || !video.videoHeight) {
    clearHeadHandOverlay();
    return;
  }
  const target = positionHeadHandOverlay(canvas, video);
  if (!target) {
    clearHeadHandOverlay();
    return;
  }
  const frame = currentFrameNumber();
  const sides = [
    ["left", { core: "#ffb020", trail: "rgba(255, 176, 32, 0.82)", glow: "rgba(255, 176, 32, 0.7)" }],
    ["right", { core: "#64d2ff", trail: "rgba(100, 210, 255, 0.82)", glow: "rgba(100, 210, 255, 0.72)" }],
  ];
  sides.forEach(([side, color]) => drawProjectedTrail(target.ctx, side, frame, target.width, target.height, color));
  sides.forEach(([side, color]) => drawProjectedWrist(target.ctx, side, frame, target.width, target.height, color));
}

function quickVideoEntries() {
  return [
    ["left", el.quickVideoLeft, "无左腕视频"],
    ["head", el.quickVideoHead, "无头部视频"],
    ["right", el.quickVideoRight, "无右腕视频"],
  ];
}

function syncQuickVideoAspect(index, video) {
  if (!video.videoWidth || !video.videoHeight) {
    return;
  }
  const aspectValue = video.videoWidth / video.videoHeight;
  const aspect = `${video.videoWidth} / ${video.videoHeight}`;
  quickVideoEntries().forEach(([key, quickVideo]) => {
    if (state.quickVideoIndexes[key] === index && quickVideo) {
      const card = quickVideo.closest(".quick-video-card");
      quickVideo.style.setProperty("--video-aspect", aspect);
      quickVideo.style.setProperty("--video-aspect-value", String(aspectValue));
      card?.style.setProperty("--video-aspect", aspect);
      card?.style.setProperty("--video-aspect-value", String(aspectValue));
    }
  });
  syncQuickVideoLayout();
}

function quickVideoAspectValue(video, card) {
  if (video?.videoWidth && video.videoHeight) {
    return video.videoWidth / video.videoHeight;
  }
  const raw = card?.style.getPropertyValue("--video-aspect-value");
  const value = Number.parseFloat(raw || "");
  return Number.isFinite(value) && value > 0 ? value : 16 / 9;
}

function cssPixelValue(style, property) {
  const value = Number.parseFloat(style.getPropertyValue(property) || "0");
  return Number.isFinite(value) ? value : 0;
}

function applyPhoneQuickVideoCardSize(card, video, contentWidth, videoHeight, cardWidth, cardHeight) {
  const contentWidthPx = `${contentWidth}px`;
  const videoHeightPx = `${videoHeight}px`;
  const widthPx = `${cardWidth}px`;
  const heightPx = `${cardHeight}px`;
  card.style.setProperty("--phone-card-width", widthPx);
  card.style.setProperty("--phone-card-height", heightPx);
  card.style.setProperty("--phone-video-width", contentWidthPx);
  card.style.setProperty("--phone-video-height", videoHeightPx);
  card.style.width = widthPx;
  card.style.minWidth = widthPx;
  card.style.maxWidth = widthPx;
  card.style.flexBasis = widthPx;
  card.style.height = heightPx;
  if (video) {
    video.style.width = contentWidthPx;
    video.style.minWidth = contentWidthPx;
    video.style.maxWidth = contentWidthPx;
    video.style.height = videoHeightPx;
  }
}

function clearPhoneQuickVideoLayout(cards) {
  cards.forEach((card) => {
    card.style.removeProperty("--phone-card-width");
    card.style.removeProperty("--phone-card-height");
    card.style.removeProperty("--phone-video-width");
    card.style.removeProperty("--phone-video-height");
    card.style.width = "";
    card.style.minWidth = "";
    card.style.maxWidth = "";
    card.style.flexBasis = "";
    card.style.height = "";
    const video = card.querySelector(".quick-video");
    if (video) {
      video.style.width = "";
      video.style.minWidth = "";
      video.style.maxWidth = "";
      video.style.height = "";
    }
  });
}

function syncQuickVideoLayout() {
  const cards = quickVideoEntries()
    .map(([, video]) => {
      const card = video?.closest(".quick-video-card");
      return card ? { card, video, aspect: quickVideoAspectValue(video, card) } : null;
    })
    .filter(Boolean);
  if (!cards.length) {
    return;
  }
  if (!state.phone) {
    clearPhoneQuickVideoLayout(cards.map((item) => item.card));
    return;
  }
  const container = document.querySelector(".phone-video-stream .video-stream-inner");
  if (!container) {
    return;
  }
  const containerRect = container.getBoundingClientRect();
  const containerStyle = window.getComputedStyle(container);
  const rawGap = Number.parseFloat(containerStyle.columnGap || containerStyle.gap || "0");
  const gap = Number.isFinite(rawGap) ? rawGap : 0;
  const availableWidth = Math.max(0, containerRect.width - gap * Math.max(0, cards.length - 1));
  const availableHeight = Math.max(0, containerRect.height);
  if (!availableWidth || !availableHeight) {
    return;
  }
  const measuredCards = cards.map((item) => {
    const cardStyle = window.getComputedStyle(item.card);
    return {
      ...item,
      borderX: cssPixelValue(cardStyle, "border-left-width") + cssPixelValue(cardStyle, "border-right-width"),
      borderY: cssPixelValue(cardStyle, "border-top-width") + cssPixelValue(cardStyle, "border-bottom-width"),
    };
  });
  const headerHeight = Math.max(...measuredCards.map(({ card }) => {
    const header = card.querySelector("header");
    return header?.getBoundingClientRect().height || 0;
  }), 0);
  const aspectSum = measuredCards.reduce((sum, item) => sum + item.aspect, 0);
  const borderXSum = measuredCards.reduce((sum, item) => sum + item.borderX, 0);
  const borderY = Math.max(...measuredCards.map((item) => item.borderY), 0);
  const videoHeightByHeight = Math.max(1, availableHeight - headerHeight - borderY);
  const videoHeightByWidth = Math.max(1, (availableWidth - borderXSum) / aspectSum);
  const videoHeight = Math.max(1, Math.min(videoHeightByHeight, videoHeightByWidth));
  const cardHeight = videoHeight + headerHeight + borderY;
  measuredCards.forEach(({ card, video, aspect, borderX }) => {
    const contentWidth = videoHeight * aspect;
    applyPhoneQuickVideoCardSize(card, video, contentWidth, videoHeight, contentWidth + borderX, cardHeight);
  });
}

function resetQuickVideoMediaLayout() {
  quickVideoEntries().forEach(([, video]) => {
    if (!video) {
      return;
    }
    video.style.removeProperty("--video-aspect");
    video.style.removeProperty("--video-aspect-value");
    const card = video.closest(".quick-video-card");
    if (card) {
      card.style.removeProperty("--video-aspect");
      card.style.removeProperty("--video-aspect-value");
      card.style.removeProperty("--phone-card-width");
      card.style.removeProperty("--phone-card-height");
      card.style.removeProperty("--phone-video-width");
      card.style.removeProperty("--phone-video-height");
      card.style.width = "";
      card.style.minWidth = "";
      card.style.maxWidth = "";
      card.style.flexBasis = "";
      card.style.height = "";
      video.style.width = "";
      video.style.minWidth = "";
      video.style.maxWidth = "";
      video.style.height = "";
    }
  });
}

function drawQuickVideoCanvases() {
  quickVideoEntries().forEach(([key, video, message]) => {
    if (!video) {
      return;
    }
    const sourceVideo = state.hiddenVideos[state.quickVideoIndexes[key]];
    video.dataset.emptyMessage = sourceVideo?.currentSrc ? "" : message;
  });
  drawHeadVideoCanvas();
}

function currentFrameNumber() {
  const video = state.hiddenVideos.find((item) => item && Number.isFinite(item.duration) && item.duration > 0);
  const length = state.current?.episode?.length || state.trajectory?.frames?.at(-1) || 0;
  if (!video || !length) {
    return 0;
  }
  return Math.max(0, Math.min(length, (video.currentTime / video.duration) * length));
}

function masterVideo() {
  return state.hiddenVideos.find((video) => video && Number.isFinite(video.duration) && video.duration > 0) || null;
}

function currentVideoRatio() {
  const video = masterVideo();
  if (!video) {
    return 0;
  }
  return Math.max(0, Math.min(1, video.currentTime / video.duration));
}

function updateProgressUI(ratio = currentVideoRatio()) {
  const normalized = Math.max(0, Math.min(1, Number.isFinite(ratio) ? ratio : 0));
  if (el.videoProgress && !state.isDraggingProgress) {
    el.videoProgress.value = String(Math.round(normalized * 1000));
  }
  if (el.videoTime) {
    el.videoTime.textContent = `${Math.round(normalized * 100)}%`;
  }
}

function setAllVideoProgress(ratio) {
  const normalized = Math.max(0, Math.min(1, Number.isFinite(ratio) ? ratio : 0));
  state.hiddenVideos.forEach((video) => {
    if (video && Number.isFinite(video.duration) && video.duration > 0) {
      video.currentTime = normalized * video.duration;
    }
  });
  updateProgressUI(normalized);
  drawHeadVideoCanvas();
  drawQuickVideoCanvases();
  drawGripperCurves();
  updateTrajectoryHighlight(true);
}

function syncVideoTimes(force = false) {
  if (state.isDraggingProgress) {
    drawHeadVideoCanvas();
    return;
  }
  const master = masterVideo();
  if (!master) {
    updateProgressUI(0);
    drawHeadVideoCanvas();
    return;
  }
  const ratio = currentVideoRatio();
  const now = performance.now();
  const rate = Math.max(1, currentPlaybackRate());
  const driftTolerance = Math.max(0.2, rate * 0.025);
  state.hiddenVideos.forEach((video) => {
    if (!video || video === master || !Number.isFinite(video.duration) || video.duration <= 0) {
      return;
    }
    const target = ratio * video.duration;
    const lastSyncAt = Number(video.dataset.lastAutoSyncAt || 0);
    const canAutoSync = now - lastSyncAt > 500;
    if (force || (canAutoSync && Math.abs(video.currentTime - target) > driftTolerance)) {
      video.dataset.lastAutoSyncAt = String(now);
      video.currentTime = target;
    }
  });
  updateProgressUI(ratio);
  drawHeadVideoCanvas();
}

function nearestFiniteIndex(values, index) {
  if (Number.isFinite(values[index])) {
    return index;
  }
  for (let offset = 1; offset < values.length; offset += 1) {
    const left = index - offset;
    const right = index + offset;
    if (left >= 0 && Number.isFinite(values[left])) {
      return left;
    }
    if (right < values.length && Number.isFinite(values[right])) {
      return right;
    }
  }
  return -1;
}

function drawCurve(canvas, values = [], frames = [], title = "", color = "#22c55e", fixedRange = null, hover = null) {
  const { ctx, width, height } = resizeCanvas(canvas);
  ctx.fillStyle = "#0b0f14";
  ctx.fillRect(0, 0, width, height);
  const valid = values.filter((value) => Number.isFinite(value));
  if (!valid.length) {
    ctx.fillStyle = "#9aa6b2";
    ctx.font = "13px sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("无曲线数据", width / 2, height / 2);
    return;
  }
  let min = fixedRange ? fixedRange[0] : Math.min(...valid);
  let max = fixedRange ? fixedRange[1] : Math.max(...valid);
  if (!fixedRange && Math.abs(max - min) < 1e-6) {
    min -= 0.5;
    max += 0.5;
  }
  const pad = { left: 42, right: 12, top: 18, bottom: 24 };
  const plotWidth = Math.max(1, width - pad.left - pad.right);
  const plotHeight = Math.max(1, height - pad.top - pad.bottom);

  const tickStep = fixedRange ? 0.01 : null;
  const tickValues = [];
  if (tickStep) {
    const tickCount = Math.round((max - min) / tickStep);
    for (let i = 0; i <= tickCount; i += 1) {
      tickValues.push(Number((min + i * tickStep).toFixed(2)));
    }
  } else {
    for (let i = 0; i <= 4; i += 1) {
      tickValues.push(min + ((max - min) * (4 - i)) / 4);
    }
  }

  ctx.lineWidth = 1;
  ctx.font = "10px sans-serif";
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  tickValues.forEach((tick) => {
    const y = pad.top + plotHeight - ((tick - min) / (max - min)) * plotHeight;
    ctx.strokeStyle = Math.abs(tick) < 1e-9 ? "#34404a" : "#26313b";
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(width - pad.right, y);
    ctx.stroke();
    ctx.fillStyle = "#8d9aa5";
    ctx.fillText(tick.toFixed(2), pad.left - 6, y);
  });

  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.beginPath();
  values.forEach((value, index) => {
    if (!Number.isFinite(value)) {
      return;
    }
    const x = pad.left + (plotWidth * index) / Math.max(1, values.length - 1);
    const clamped = Math.max(min, Math.min(max, value));
    const y = pad.top + plotHeight - ((clamped - min) / (max - min)) * plotHeight;
    if (index === 0) {
      ctx.moveTo(x, y);
    } else {
      ctx.lineTo(x, y);
    }
  });
  ctx.stroke();

  const currentFrame = currentFrameNumber();
  const maxFrame = frames.length ? frames[frames.length - 1] : values.length - 1;
  const ratio = maxFrame > 0 ? Math.max(0, Math.min(1, currentFrame / maxFrame)) : 0;
  const markerX = pad.left + ratio * plotWidth;
  ctx.strokeStyle = "#e5edf3";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(markerX, pad.top);
  ctx.lineTo(markerX, height - pad.bottom);
  ctx.stroke();

  ctx.fillStyle = "#b7c2cc";
  ctx.font = "12px sans-serif";
  ctx.textAlign = "left";
  ctx.fillText(title, pad.left, 13);

  if (!hover) {
    return;
  }
  const hoverX = Math.max(pad.left, Math.min(width - pad.right, hover.x));
  const hoverRatio = (hoverX - pad.left) / plotWidth;
  const rawIndex = Math.round(hoverRatio * Math.max(1, values.length - 1));
  const hoverIndex = nearestFiniteIndex(values, Math.max(0, Math.min(values.length - 1, rawIndex)));
  if (hoverIndex < 0) {
    return;
  }
  const value = values[hoverIndex];
  const frame = frames[hoverIndex] ?? hoverIndex;
  const x = pad.left + (plotWidth * hoverIndex) / Math.max(1, values.length - 1);
  const clamped = Math.max(min, Math.min(max, value));
  const y = pad.top + plotHeight - ((clamped - min) / (max - min)) * plotHeight;

  ctx.strokeStyle = "rgba(229, 237, 243, 0.72)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(x, pad.top);
  ctx.lineTo(x, height - pad.bottom);
  ctx.stroke();

  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.arc(x, y, 4, 0, Math.PI * 2);
  ctx.fill();

  const text = `frame ${frame}  ${value.toFixed(4)}`;
  ctx.font = "12px sans-serif";
  const textWidth = ctx.measureText(text).width;
  const boxWidth = textWidth + 14;
  const boxHeight = 24;
  const boxX = Math.min(width - pad.right - boxWidth, Math.max(pad.left, x + 10));
  const boxY = y > pad.top + boxHeight + 8 ? y - boxHeight - 8 : y + 10;
  ctx.fillStyle = "rgba(11, 15, 20, 0.88)";
  ctx.strokeStyle = "rgba(255, 255, 255, 0.18)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.roundRect(boxX, boxY, boxWidth, boxHeight, 5);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = "#e5edf3";
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  ctx.fillText(text, boxX + 7, boxY + boxHeight / 2);
}

function drawGripperCurves() {
  const trajectory = state.trajectory;
  if (!trajectory) {
    drawCanvasMessage(el.leftGripperCanvas, "等待轨迹数据");
    drawCanvasMessage(el.rightGripperCanvas, "等待轨迹数据");
    return;
  }
  drawCurve(el.leftGripperCanvas, trajectory.left?.gripper || [], trajectory.frames || [], "left gripper", "#facc15", [0, 0.1], state.curveHover.left);
  drawCurve(el.rightGripperCanvas, trajectory.right?.gripper || [], trajectory.frames || [], "right gripper", "#c084fc", [0, 0.1], state.curveHover.right);
}

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = src;
    script.async = true;
    script.addEventListener("load", resolve, { once: true });
    script.addEventListener("error", () => reject(new Error(`load failed: ${src}`)), { once: true });
    document.head.appendChild(script);
  });
}

async function loadTrajectoryRendererPlotlyLegacy() {
  if (window.Plotly) {
    Plotly3D = window.Plotly;
    el.trajectoryState.textContent = "等待数据";
    return;
  }
  try {
    await loadScript("/vendor/plotly.min.js");
  } catch (localError) {
    try {
      await loadScript("https://cdn.plot.ly/plotly-2.35.2.min.js");
    } catch (cdnError) {
      try {
        await loadScript("https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js");
      } catch (fallbackError) {
        console.warn("Plotly load failed", localError, cdnError, fallbackError);
        el.trajectoryState.textContent = "Plotly 加载失败";
        return;
      }
    }
  }
  Plotly3D = window.Plotly;
  el.trajectoryState.textContent = "等待数据";
}

async function loadTrajectoryRenderer() {
  try {
    const [threeModule, controlsModule] = await Promise.all([
      import("/vendor/three.module.min.js"),
      import("/vendor/OrbitControls.js"),
    ]);
    Three3D = threeModule;
    OrbitControls3D = controlsModule.OrbitControls;
    el.trajectoryState.textContent = "waiting for data";
  } catch (error) {
    console.warn("Three.js load failed", error);
    el.trajectoryState.textContent = "3D renderer failed";
  }
}

function validPoint(point) {
  return Array.isArray(point) && point.length >= 3 && point.every((value) => Number.isFinite(value));
}

function compactPoints(points = []) {
  return points.filter(validPoint);
}

function trajectorySeries(trajectory) {
  return TRAJECTORY_SERIES_CONFIG.map((config) => ({
    ...config,
    data: config.getData(trajectory) || {},
  }));
}

function trajectoryAxisRanges(trajectory) {
  const points = trajectorySeries(trajectory)
    .flatMap((series) => compactPoints(series.data.points || []));
  const fallback = [-1, 1];
  if (!points.length) {
    return { x: fallback, y: fallback, z: fallback };
  }
  const values = [0, 1, 2].map((axis) => points.map((point) => point[axis]));
  const ranges = values.map((axisValues) => {
    const min = Math.min(...axisValues);
    const max = Math.max(...axisValues);
    const span = Math.max(max - min, 0.08);
    const pad = Math.max(span * 0.08, 0.04);
    return [min - pad, max + pad];
  });
  return { x: ranges[0], y: ranges[1], z: ranges[2] };
}

function trajectoryTrace(name, points = [], color, width = 5, opacity = TRAJECTORY_BASE_OPACITY) {
  const valid = compactPoints(points);
  return {
    type: "scatter3d",
    mode: "lines",
    name,
    showlegend: false,
    x: valid.map((point) => point[0]),
    y: valid.map((point) => point[1]),
    z: valid.map((point) => point[2]),
    line: { color, width },
    opacity,
    hovertemplate: `${name}<br>x=%{x:.4f}<br>y=%{y:.4f}<br>z=%{z:.4f}<extra></extra>`,
  };
}

function endpointTrace(name, points = [], color) {
  const valid = compactPoints(points);
  if (!valid.length) {
    return null;
  }
  const start = valid[0];
  const end = valid[valid.length - 1];
  return {
    type: "scatter3d",
    mode: "markers",
    name: `${name} start/end`,
    showlegend: false,
    x: [start[0], end[0]],
    y: [start[1], end[1]],
    z: [start[2], end[2]],
    marker: { color, size: [4, 7], opacity: TRAJECTORY_BASE_OPACITY },
    hovertemplate: `${name}<br>x=%{x:.4f}<br>y=%{y:.4f}<br>z=%{z:.4f}<extra></extra>`,
  };
}

function trajectoryFlowTrace(name, color, width = 10, opacity = 0.82) {
  return {
    type: "scatter3d",
    mode: "lines",
    name,
    showlegend: false,
    x: [],
    y: [],
    z: [],
    line: { color, width },
    opacity,
    hoverinfo: "skip",
  };
}

function trajectoryNowTrace(name, color) {
  return {
    type: "scatter3d",
    mode: "markers",
    name,
    showlegend: false,
    x: [],
    y: [],
    z: [],
    marker: {
      color,
      size: 5,
      opacity: 1,
    },
    hovertemplate: `${name}<br>x=%{x:.4f}<br>y=%{y:.4f}<br>z=%{z:.4f}<extra></extra>`,
  };
}

function nearestValidPointIndex(points = [], index = 0) {
  const clamped = Math.max(0, Math.min(points.length - 1, index));
  if (validPoint(points[clamped])) {
    return clamped;
  }
  for (let offset = 1; offset < points.length; offset += 1) {
    const left = clamped - offset;
    const right = clamped + offset;
    if (left >= 0 && validPoint(points[left])) {
      return left;
    }
    if (right < points.length && validPoint(points[right])) {
      return right;
    }
  }
  return -1;
}

function trajectoryIndexAtFrame(frames = [], frame = 0, fallbackLength = 0) {
  if (!frames.length) {
    return Math.max(0, Math.min(Math.max(0, fallbackLength - 1), Math.round(frame)));
  }
  let left = 0;
  let right = frames.length - 1;
  while (left < right) {
    const middle = Math.ceil((left + right) / 2);
    if ((frames[middle] ?? middle) <= frame) {
      left = middle;
    } else {
      right = middle - 1;
    }
  }
  return left;
}

function trajectoryTrailStartIndex(frames = [], index = 0, fallbackLength = 0) {
  if (index <= 0) {
    return 0;
  }
  if (frames.length > 1) {
    const firstFrame = Number(frames[0]);
    const lastFrame = Number(frames[frames.length - 1]);
    if (Number.isFinite(firstFrame) && Number.isFinite(lastFrame) && lastFrame > firstFrame) {
      const indexedFrame = Number(frames[Math.min(index, frames.length - 1)]);
      const currentFrame = Number.isFinite(indexedFrame) ? indexedFrame : firstFrame;
      const cutoff = currentFrame - (lastFrame - firstFrame) * TRAJECTORY_ACTIVE_TRAIL_FRACTION;
      let left = 0;
      let right = Math.min(index, frames.length - 1);
      while (left < right) {
        const middle = Math.floor((left + right) / 2);
        const middleFrame = Number(frames[middle]);
        if ((Number.isFinite(middleFrame) ? middleFrame : middle) < cutoff) {
          left = middle + 1;
        } else {
          right = middle;
        }
      }
      return Math.max(0, Math.min(index - 1, left));
    }
  }
  const fallbackWindow = Math.max(
    1,
    Math.ceil(Math.max(1, fallbackLength - 1) * TRAJECTORY_ACTIVE_TRAIL_FRACTION),
  );
  return Math.max(0, index - fallbackWindow);
}

function trajectorySample(points = [], frames = [], frame = 0) {
  if (!points.length) {
    return { point: null, trail: [], index: -1 };
  }
  const rawIndex = trajectoryIndexAtFrame(frames, frame, points.length);
  const index = nearestValidPointIndex(points, rawIndex);
  if (index < 0) {
    return { point: null, trail: [], index: -1 };
  }
  const start = trajectoryTrailStartIndex(frames, index, points.length);
  const trail = [];
  for (let i = start; i <= index; i += 1) {
    if (validPoint(points[i])) {
      trail.push(points[i]);
    }
  }
  return { point: points[index], trail, index };
}

function validQuat(quat) {
  return Array.isArray(quat)
    && quat.length >= 4
    && quat.every((value) => Number.isFinite(value))
    && Math.hypot(quat[0], quat[1], quat[2], quat[3]) > 1e-6;
}

function normalizeQuat(quat) {
  const length = Math.hypot(quat[0], quat[1], quat[2], quat[3]) || 1;
  return [quat[0] / length, quat[1] / length, quat[2] / length, quat[3] / length];
}

function cross(a, b) {
  return [
    a[1] * b[2] - a[2] * b[1],
    a[2] * b[0] - a[0] * b[2],
    a[0] * b[1] - a[1] * b[0],
  ];
}

function rotateVectorByQuat(vector, quat) {
  const [w, x, y, z] = normalizeQuat(quat);
  const qvec = [x, y, z];
  const uv = cross(qvec, vector);
  const uuv = cross(qvec, uv);
  return [
    vector[0] + 2 * (w * uv[0] + uuv[0]),
    vector[1] + 2 * (w * uv[1] + uuv[1]),
    vector[2] + 2 * (w * uv[2] + uuv[2]),
  ];
}

function normalizeVector(vector) {
  const length = Math.hypot(vector[0], vector[1], vector[2]);
  if (!Number.isFinite(length) || length < 1e-6) {
    return null;
  }
  return [vector[0] / length, vector[1] / length, vector[2] / length];
}

function cameraFromDefaultViewDirection(trajectory) {
  const fallback = {
    up: { x: 0, y: 1, z: 0 },
    eye: { x: 1.35, y: 0.85, z: 1.35 },
  };
  const metadata = trajectory.metadata || {};
  const deviceType = String(trajectory.device_type || metadata.device_type || "").trim().toLowerCase();
  const normalizedDeviceType = deviceType.replace(/[^a-z0-9]+/g, "");
  let viewDirection = null;

  if (normalizedDeviceType === "inferencer1") {
    // inference_r1 uses a fixed world-space ray and does not depend on the head pose.
    viewDirection = normalizeVector([1, -1, 0]);
  } else {
    const points = trajectory.ego?.points || [];
    const quaternions = trajectory.ego?.quaternions || [];
    const firstPose = points
      .map((point, index) => ({ point, quat: quaternions[index] }))
      .find((item) => validPoint(item.point) && validQuat(item.quat));
    if (!firstPose) {
      return fallback;
    }
    const collectionMode = String(metadata.collection_mode || "").toLowerCase();
    const isTeleop = metadata.transform === "teleop_rx_minus_90"
      || deviceType.includes("teleoperation")
      || collectionMode.includes("teleoperation");
    const headLocalAxis = isTeleop ? [0, 0, 1] : [0, 0, -1];
    viewDirection = normalizeVector(rotateVectorByQuat(headLocalAxis, firstPose.quat));
  }

  if (!viewDirection) {
    return fallback;
  }
  const distance = 1.75;
  return {
    up: { x: 0, y: 1, z: 0 },
    eye: {
      x: -viewDirection[0] * distance,
      y: -viewDirection[1] * distance,
      z: -viewDirection[2] * distance,
    },
  };
}

function cloneTrajectoryCamera(camera) {
  if (!camera) {
    return null;
  }
  return JSON.parse(JSON.stringify(camera));
}

function currentTrajectoryCamera() {
  const camera = el.trajectoryCanvas?._fullLayout?.scene?.camera;
  return cloneTrajectoryCamera(camera);
}

function rememberTrajectoryCamera(camera = currentTrajectoryCamera()) {
  if (!camera || state.isRestoringTrajectoryCamera) {
    return;
  }
  state.trajectoryCamera = cloneTrajectoryCamera(camera);
  state.trajectoryCameraRevision += 1;
}

function relayoutTouchesTrajectoryCamera(eventData = {}) {
  return Object.keys(eventData || {}).some((key) => key === "scene.camera" || key.startsWith("scene.camera."));
}

function restoreTrajectoryCamera(camera) {
  if (!Plotly3D || !el.trajectoryCanvas || !camera) {
    return;
  }
  state.isRestoringTrajectoryCamera = true;
  Plotly3D.relayout(el.trajectoryCanvas, { "scene.camera": camera })
    .catch(() => {})
    .finally(() => {
      window.setTimeout(() => {
        state.isRestoringTrajectoryCamera = false;
      }, 0);
    });
}

function segmentTrace(name, segments, color) {
  return {
    type: "scatter3d",
    mode: "lines",
    name,
    showlegend: false,
    x: segments.x,
    y: segments.y,
    z: segments.z,
    line: { color, width: 8 },
    hoverinfo: "skip",
  };
}

function pushSegment(segments, origin, endpoint) {
  segments.x.push(origin[0], endpoint[0], null);
  segments.y.push(origin[1], endpoint[1], null);
  segments.z.push(origin[2], endpoint[2], null);
}

function currentPoseAxesSegments(point, quat) {
  const xSegments = { x: [], y: [], z: [] };
  const ySegments = { x: [], y: [], z: [] };
  const zSegments = { x: [], y: [], z: [] };
  if (!validPoint(point) || !validQuat(quat)) {
    return [xSegments, ySegments, zSegments];
  }
  const axisLength = 0.0275;
  [
    [[axisLength, 0, 0], xSegments],
    [[0, axisLength, 0], ySegments],
    [[0, 0, axisLength], zSegments],
  ].forEach(([axis, segments]) => {
    const rotated = rotateVectorByQuat(axis, quat);
    const endpoint = [
      point[0] + rotated[0],
      point[1] + rotated[1],
      point[2] + rotated[2],
    ];
    pushSegment(segments, point, endpoint);
  });
  return [xSegments, ySegments, zSegments];
}

function currentPoseAxesTraces(prefix) {
  return [
    segmentTrace(`${prefix} local x`, { x: [], y: [], z: [] }, "#ff4d4d"),
    segmentTrace(`${prefix} local y`, { x: [], y: [], z: [] }, "#35d06f"),
    segmentTrace(`${prefix} local z`, { x: [], y: [], z: [] }, "#38bdf8"),
  ];
}

function bindTrajectoryInteractionGuards() {
  if (!el.trajectoryCanvas) {
    return;
  }
  if (!state.trajectoryDomEventsBound) {
    state.trajectoryDomEventsBound = true;
    el.trajectoryCanvas.addEventListener("pointerdown", () => {
      state.isInteractingTrajectory = true;
    });
    window.addEventListener("pointerup", () => {
      if (!state.isInteractingTrajectory) {
        return;
      }
      window.setTimeout(() => {
        rememberTrajectoryCamera();
        state.isInteractingTrajectory = false;
      }, 120);
    });
    window.addEventListener("pointercancel", () => {
      rememberTrajectoryCamera();
      state.isInteractingTrajectory = false;
    });
    el.trajectoryCanvas.addEventListener("wheel", () => {
      state.isInteractingTrajectory = true;
      window.clearTimeout(state.trajectoryWheelTimer);
      window.setTimeout(() => rememberTrajectoryCamera(), 0);
      state.trajectoryWheelTimer = window.setTimeout(() => {
        rememberTrajectoryCamera();
        state.isInteractingTrajectory = false;
      }, 180);
    }, { passive: true });
  }
  if (!state.trajectoryPlotEventsBound && typeof el.trajectoryCanvas.on === "function") {
    state.trajectoryPlotEventsBound = true;
    el.trajectoryCanvas.on("plotly_relayout", (eventData) => {
      if (relayoutTouchesTrajectoryCamera(eventData)) {
        rememberTrajectoryCamera();
      }
    });
    el.trajectoryCanvas.on("plotly_relayouting", (eventData) => {
      if (relayoutTouchesTrajectoryCamera(eventData)) {
        rememberTrajectoryCamera();
      }
    });
  }
}

function renderTrajectory3DPlotlyLegacy(trajectory) {
  state.trajectory = trajectory;
  drawGripperCurves();
  if (!Plotly3D) {
    return;
  }
  const traces = [
    trajectoryTrace("左手轨迹 glow", trajectory.left?.points || [], "rgba(250, 204, 21, 0.28)", 13, 0.38),
    trajectoryTrace("左手轨迹", trajectory.left?.points || [], "#facc15", 4, 0.9),
    trajectoryTrace("右手轨迹 glow", trajectory.right?.points || [], "rgba(192, 132, 252, 0.28)", 13, 0.38),
    trajectoryTrace("右手轨迹", trajectory.right?.points || [], "#c084fc", 4, 0.9),
    endpointTrace("左手", trajectory.left?.points || [], "#facc15"),
    endpointTrace("右手", trajectory.right?.points || [], "#c084fc"),
  ].filter(Boolean);
  const highlightStart = traces.length;
  traces.push(
    trajectoryFlowTrace("left current glow", "rgba(250, 204, 21, 0.35)", 20, 0.55),
    trajectoryFlowTrace("left current flow", "#fef08a", 7, 0.98),
    trajectoryNowTrace("left current", "#f59e0b"),
    trajectoryFlowTrace("right current glow", "rgba(192, 132, 252, 0.35)", 20, 0.55),
    trajectoryFlowTrace("right current flow", "#e9d5ff", 7, 0.98),
    trajectoryNowTrace("right current", "#8b5cf6"),
    ...currentPoseAxesTraces("left current"),
    ...currentPoseAxesTraces("right current"),
  );
  state.trajectoryHighlightTraceIndexes = Array.from({ length: 12 }, (_, index) => highlightStart + index);
  state.lastTrajectoryHighlightFrame = null;
  state.lastTrajectoryHighlightAt = 0;
  state.trajectoryCamera = cloneTrajectoryCamera(cameraFromDefaultViewDirection(trajectory));
  state.trajectoryCameraRevision = 0;
  const axisRanges = trajectoryAxisRanges(trajectory);
  const axisStyle = {
    showbackground: true,
    backgroundcolor: "#0b0f14",
    gridcolor: "#1f2937",
    zerolinecolor: "#64748b",
    color: "#cbd5e1",
    titlefont: { color: "#cbd5e1", size: 12 },
    tickfont: { color: "#8d9aa5", size: 10 },
  };
  const layout = {
    uirevision: `episode-${state.currentIndex ?? "none"}`,
    margin: { l: 0, r: 0, t: 0, b: 0 },
    paper_bgcolor: "#0b0f14",
    plot_bgcolor: "#0b0f14",
    showlegend: false,
    scene: {
      uirevision: `episode-${state.currentIndex ?? "none"}`,
      aspectmode: "data",
      xaxis: { ...axisStyle, title: "x", range: axisRanges.x, autorange: false },
      yaxis: { ...axisStyle, title: "y ↑", range: axisRanges.y, autorange: false },
      zaxis: { ...axisStyle, title: "z", range: axisRanges.z, autorange: false },
      camera: state.trajectoryCamera,
    },
  };
  const config = {
    responsive: true,
    displaylogo: false,
    scrollZoom: true,
    modeBarButtonsToRemove: ["lasso2d", "select2d"],
  };
  bindTrajectoryInteractionGuards();
  Plotly3D.react(el.trajectoryCanvas, traces, layout, config)
    .then(() => {
      bindTrajectoryInteractionGuards();
      rememberTrajectoryCamera(state.trajectoryCamera);
      updateTrajectoryHighlight(true);
    })
    .catch(() => {});
  updateTrajectoryHighlight(true);
  el.trajectoryState.textContent = `${formatNumber(trajectory.total_rows)} frames · stride ${trajectory.stride}`;
}

function updateTrajectoryHighlightPlotlyLegacy(force = false) {
  if (!Plotly3D || !state.trajectory || !state.trajectoryHighlightTraceIndexes.length || !el.trajectoryCanvas) {
    return;
  }
  const frame = currentFrameNumber();
  const roundedFrame = Math.round(frame);
  const now = performance.now();
  if (!force && state.lastTrajectoryHighlightFrame === roundedFrame) {
    return;
  }
  if (!force && now - state.lastTrajectoryHighlightAt < 60) {
    return;
  }
  state.lastTrajectoryHighlightFrame = roundedFrame;
  state.lastTrajectoryHighlightAt = now;

  const frames = state.trajectory.frames || [];
  const left = trajectorySample(state.trajectory.left?.points || [], frames, frame);
  const right = trajectorySample(state.trajectory.right?.points || [], frames, frame);
  const leftAxes = currentPoseAxesSegments(left.point, state.trajectory.left?.quaternions?.[left.index]);
  const rightAxes = currentPoseAxesSegments(right.point, state.trajectory.right?.quaternions?.[right.index]);
  const dynamicSegments = [
    {
      x: left.trail.map((point) => point[0]),
      y: left.trail.map((point) => point[1]),
      z: left.trail.map((point) => point[2]),
    },
    {
      x: left.point ? [left.point[0]] : [],
      y: left.point ? [left.point[1]] : [],
      z: left.point ? [left.point[2]] : [],
    },
    {
      x: right.trail.map((point) => point[0]),
      y: right.trail.map((point) => point[1]),
      z: right.trail.map((point) => point[2]),
    },
    {
      x: right.point ? [right.point[0]] : [],
      y: right.point ? [right.point[1]] : [],
      z: right.point ? [right.point[2]] : [],
    },
    ...leftAxes,
    ...rightAxes,
  ];

  const cameraRevision = state.trajectoryCameraRevision;
  const cameraBeforeUpdate = cloneTrajectoryCamera(state.trajectoryCamera);
  Plotly3D.restyle(el.trajectoryCanvas, {
    x: dynamicSegments.map((segment) => segment.x),
    y: dynamicSegments.map((segment) => segment.y),
    z: dynamicSegments.map((segment) => segment.z),
  }, state.trajectoryHighlightTraceIndexes)
    .then(() => {
      if (state.isInteractingTrajectory) {
        return;
      }
      const camera = state.trajectoryCameraRevision === cameraRevision
        ? cameraBeforeUpdate
        : state.trajectoryCamera;
      restoreTrajectoryCamera(camera);
    })
    .catch(() => {});
}

function resizeTrajectoryPlotlyLegacy() {
  if (Plotly3D && el.trajectoryCanvas) {
    Plotly3D.Plots.resize(el.trajectoryCanvas);
  }
}

function disposeThreeMaterial(material) {
  if (Array.isArray(material)) {
    material.forEach((item) => disposeThreeMaterial(item));
    return;
  }
  material?.dispose?.();
}

function disposeThreeObject(object) {
  object?.traverse?.((child) => {
    child.geometry?.dispose?.();
    disposeThreeMaterial(child.material);
  });
}

function disposeTrajectoryView() {
  if (!trajectoryView) {
    return;
  }
  trajectoryView.controls?.dispose?.();
  disposeThreeObject(trajectoryView.scene);
  trajectoryView.renderer?.dispose?.();
  trajectoryView.renderer?.domElement?.remove?.();
  trajectoryView = null;
}

function pointToVector3(point) {
  return new Three3D.Vector3(point[0], point[1], point[2]);
}

function trajectoryBounds(trajectory) {
  const ranges = trajectoryAxisRanges(trajectory);
  const center = new Three3D.Vector3(
    (ranges.x[0] + ranges.x[1]) / 2,
    (ranges.y[0] + ranges.y[1]) / 2,
    (ranges.z[0] + ranges.z[1]) / 2,
  );
  const size = new Three3D.Vector3(
    Math.max(ranges.x[1] - ranges.x[0], 0.08),
    Math.max(ranges.y[1] - ranges.y[0], 0.08),
    Math.max(ranges.z[1] - ranges.z[0], 0.08),
  );
  return {
    ranges,
    center,
    size,
    span: Math.max(size.x, size.y, size.z, 0.08),
  };
}

function trajectoryContainerSize() {
  return {
    width: Math.max(1, el.trajectoryCanvas?.clientWidth || 640),
    height: Math.max(1, el.trajectoryCanvas?.clientHeight || 360),
  };
}

function applyTrajectoryRendererSize(view = trajectoryView) {
  if (!view) {
    return;
  }
  const { width, height } = trajectoryContainerSize();
  view.renderer.setSize(width, height, false);
  view.camera.aspect = width / height;
  view.camera.updateProjectionMatrix();
}

function compactVectorPoints(points = []) {
  const vectors = [];
  compactPoints(points).forEach((point) => {
    const vector = pointToVector3(point);
    const previous = vectors[vectors.length - 1];
    if (!previous || previous.distanceToSquared(vector) > 1e-12) {
      vectors.push(vector);
    }
  });
  return vectors;
}

function createPolylineCurve(points = []) {
  const vectors = compactVectorPoints(points);
  if (vectors.length < 2) {
    return null;
  }
  const curve = new Three3D.CurvePath();
  for (let index = 1; index < vectors.length; index += 1) {
    curve.add(new Three3D.LineCurve3(vectors[index - 1], vectors[index]));
  }
  return { curve, count: vectors.length };
}

function createTubeGeometry(points = [], radius = 0.002) {
  const path = createPolylineCurve(points);
  if (!path) {
    return null;
  }
  const tubularSegments = Math.max(3, Math.min(900, path.count * 2));
  return new Three3D.TubeGeometry(path.curve, tubularSegments, radius, 6, false);
}

function createMeshMaterial(color, opacity = 1, additive = false) {
  return new Three3D.MeshBasicMaterial({
    color,
    transparent: opacity < 1 || additive,
    opacity,
    blending: additive ? Three3D.AdditiveBlending : Three3D.NormalBlending,
    depthWrite: opacity >= 1 && !additive,
  });
}

function createTrailGradientMaterial(color) {
  return new Three3D.ShaderMaterial({
    uniforms: {
      trailColor: { value: new Three3D.Color(color) },
    },
    vertexShader: `
      varying float vTrailProgress;
      void main() {
        vTrailProgress = clamp(uv.x, 0.0, 1.0);
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: `
      uniform vec3 trailColor;
      varying float vTrailProgress;
      void main() {
        float alpha = clamp(vTrailProgress, 0.0, 1.0);
        if (alpha <= 0.001) discard;
        gl_FragColor = vec4(trailColor, alpha);
      }
    `,
    transparent: true,
    depthWrite: false,
    blending: Three3D.NormalBlending,
  });
}

function createTubeMesh(points, radius, material) {
  const geometry = createTubeGeometry(points, radius);
  const mesh = new Three3D.Mesh(geometry || new Three3D.BufferGeometry(), material);
  mesh.visible = Boolean(geometry);
  mesh.frustumCulled = false;
  return mesh;
}

function replaceObjectGeometry(object, geometry) {
  const previous = object.geometry;
  object.geometry = geometry || new Three3D.BufferGeometry();
  object.visible = Boolean(geometry);
  previous?.dispose?.();
}

function addTrajectoryTube(scene, points, series, radius) {
  const core = createTubeMesh(
    points,
    radius * series.radiusScale,
    createMeshMaterial(series.colors.core, series.opacity, false),
  );
  scene.add(core);
}

function addEndpointMarkers(scene, points = [], color, radius) {
  const valid = compactPoints(points);
  if (!valid.length) {
    return;
  }
  const geometry = new Three3D.SphereGeometry(radius, 16, 10);
  const start = new Three3D.Mesh(
    geometry,
    createMeshMaterial(color, TRAJECTORY_BASE_OPACITY, false),
  );
  start.position.copy(pointToVector3(valid[0]));
  scene.add(start);

  const end = new Three3D.Mesh(
    new Three3D.SphereGeometry(radius * 1.45, 18, 12),
    createMeshMaterial(color, TRAJECTORY_BASE_OPACITY, false),
  );
  end.position.copy(pointToVector3(valid[valid.length - 1]));
  scene.add(end);
}

function addSceneGuides(scene, bounds) {
  const gridSize = Math.max(bounds.span * 1.08, 0.12);
  const grid = new Three3D.GridHelper(gridSize, 10, 0x334155, 0x1f2937);
  grid.position.set(bounds.center.x, bounds.ranges.y[0], bounds.center.z);
  (Array.isArray(grid.material) ? grid.material : [grid.material]).forEach((material) => {
    material.transparent = true;
    material.opacity = 0.28;
  });
  scene.add(grid);

  const c = bounds.center;
  const positions = [
    bounds.ranges.x[0], c.y, c.z, bounds.ranges.x[1], c.y, c.z,
    c.x, bounds.ranges.y[0], c.z, c.x, bounds.ranges.y[1], c.z,
    c.x, c.y, bounds.ranges.z[0], c.x, c.y, bounds.ranges.z[1],
  ];
  const colors = [
    1, 0.25, 0.25, 1, 0.25, 0.25,
    0.25, 1, 0.45, 0.25, 1, 0.45,
    0.35, 0.75, 1, 0.35, 0.75, 1,
  ];
  const geometry = new Three3D.BufferGeometry();
  geometry.setAttribute("position", new Three3D.Float32BufferAttribute(positions, 3));
  geometry.setAttribute("color", new Three3D.Float32BufferAttribute(colors, 3));
  const material = new Three3D.LineBasicMaterial({
    vertexColors: true,
    transparent: true,
    opacity: 0.62,
  });
  scene.add(new Three3D.LineSegments(geometry, material));
}

function createAxisMesh(color, radius) {
  const mesh = new Three3D.Mesh(
    new Three3D.CylinderGeometry(radius, radius, 1, 8, 1, false),
    createMeshMaterial(color, 0.96, false),
  );
  mesh.frustumCulled = false;
  mesh.visible = false;
  return mesh;
}

function setAxisMeshSegment(mesh, segments) {
  let start = null;
  let end = null;
  for (let index = 0; index < segments.x.length; index += 3) {
    const a = [segments.x[index], segments.y[index], segments.z[index]];
    const b = [segments.x[index + 1], segments.y[index + 1], segments.z[index + 1]];
    if (validPoint(a) && validPoint(b)) {
      start = pointToVector3(a);
      end = pointToVector3(b);
      break;
    }
  }
  if (!start || !end) {
    mesh.visible = false;
    return;
  }
  const direction = end.clone().sub(start);
  const length = direction.length();
  if (!Number.isFinite(length) || length < 1e-8) {
    mesh.visible = false;
    return;
  }
  mesh.position.copy(start).add(end).multiplyScalar(0.5);
  mesh.quaternion.setFromUnitVectors(new Three3D.Vector3(0, 1, 0), direction.normalize());
  mesh.scale.set(1, length, 1);
  mesh.visible = true;
}

function createDynamicTrajectory(scene, color, radius, showCurrentAxes = false) {
  const flowCore = createTubeMesh([], radius, createTrailGradientMaterial(color));
  const axisRadius = Math.max(radius * 0.5, 0.00045);
  const axes = showCurrentAxes
    ? [
        createAxisMesh(0xff4d4d, axisRadius),
        createAxisMesh(0x35d06f, axisRadius),
        createAxisMesh(0x38bdf8, axisRadius),
      ]
    : [];
  scene.add(flowCore, ...axes);
  return {
    flowCore,
    axes,
    radii: {
      core: radius,
    },
  };
}

function updateDynamicTrajectory(dynamicTrajectory, sample, quat) {
  replaceObjectGeometry(
    dynamicTrajectory.flowCore,
    createTubeGeometry(sample.trail, dynamicTrajectory.radii.core),
  );
  const axes = currentPoseAxesSegments(sample.point, quat);
  dynamicTrajectory.axes.forEach((axis, index) => {
    setAxisMeshSegment(axis, axes[index]);
  });
}

function createTrajectoryView(trajectory) {
  disposeTrajectoryView();
  if (!Three3D || !OrbitControls3D || !el.trajectoryCanvas) {
    return null;
  }

  const bounds = trajectoryBounds(trajectory);
  const { width, height } = trajectoryContainerSize();
  const renderer = new Three3D.WebGLRenderer({
    antialias: true,
    alpha: false,
    powerPreference: "high-performance",
  });
  renderer.setClearColor(0x0b0f14, 1);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(width, height, false);
  if (Three3D.SRGBColorSpace) {
    renderer.outputColorSpace = Three3D.SRGBColorSpace;
  }
  renderer.domElement.className = "trajectory-three-canvas";
  el.trajectoryCanvas.replaceChildren(renderer.domElement);

  const scene = new Three3D.Scene();
  scene.background = new Three3D.Color(0x0b0f14);
  addSceneGuides(scene, bounds);

  const radius = Math.max(bounds.span * 0.0025, 0.0018);
  const markerRadius = Math.max(bounds.span * 0.011, 0.0045);
  const seriesList = trajectorySeries(trajectory);
  seriesList.forEach((series) => {
    const points = series.data.points || [];
    addTrajectoryTube(scene, points, series, radius);
    addEndpointMarkers(scene, points, series.colors.marker, markerRadius * series.endpointScale);
  });

  const camera = new Three3D.PerspectiveCamera(
    45,
    width / height,
    Math.max(bounds.span / 1000, 0.0005),
    Math.max(bounds.span * 30, 10),
  );
  camera.up.set(0, 1, 0);
  const cameraEye = cameraFromDefaultViewDirection(trajectory).eye;
  const eyeVector = new Three3D.Vector3(cameraEye.x, cameraEye.y, cameraEye.z);
  if (eyeVector.lengthSq() < 1e-8) {
    eyeVector.set(1.35, 0.85, 1.35);
  }
  eyeVector.normalize().multiplyScalar(Math.max(bounds.span * 0.9, 0.2));
  camera.position.copy(bounds.center).add(eyeVector);
  camera.lookAt(bounds.center);

  const controls = new OrbitControls3D(camera, renderer.domElement);
  controls.target.copy(bounds.center);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.screenSpacePanning = false;
  controls.minDistance = Math.max(bounds.span * 0.08, 0.03);
  controls.maxDistance = Math.max(bounds.span * 12, 1);
  controls.update();

  const dynamicSeries = Object.fromEntries(seriesList.map((series) => [
    series.id,
    createDynamicTrajectory(
      scene,
      series.colors.core,
      radius * series.radiusScale * 1.08,
      Boolean(series.showCurrentAxes),
    ),
  ]));

  return {
    scene,
    renderer,
    camera,
    controls,
    dynamicSeries,
  };
}

function renderTrajectory3D(trajectory) {
  state.trajectory = trajectory;
  drawGripperCurves();
  drawHeadVideoCanvas();
  if (!Three3D || !OrbitControls3D) {
    return;
  }
  trajectoryView = createTrajectoryView(trajectory);
  state.lastTrajectoryHighlightFrame = null;
  state.lastTrajectoryHighlightAt = 0;
  updateTrajectoryHighlight(true);
  renderTrajectoryScene();
  el.trajectoryState.textContent = `${formatNumber(trajectory.total_rows)} frames / stride ${trajectory.stride}`;
}

function updateTrajectoryHighlight(force = false) {
  if (!trajectoryView || !state.trajectory) {
    return;
  }
  const frame = currentFrameNumber();
  const roundedFrame = Math.round(frame);
  const now = performance.now();
  if (!force && state.lastTrajectoryHighlightFrame === roundedFrame) {
    return;
  }
  if (!force && now - state.lastTrajectoryHighlightAt < 60) {
    return;
  }
  state.lastTrajectoryHighlightFrame = roundedFrame;
  state.lastTrajectoryHighlightAt = now;

  const frames = state.trajectory.frames || [];
  trajectorySeries(state.trajectory).forEach((series) => {
    const dynamicTrajectory = trajectoryView.dynamicSeries[series.id];
    if (!dynamicTrajectory) {
      return;
    }
    const sample = trajectorySample(series.data.points || [], frames, frame);
    updateDynamicTrajectory(
      dynamicTrajectory,
      sample,
      series.data.quaternions?.[sample.index],
    );
  });
}

function renderTrajectoryScene() {
  if (!trajectoryView) {
    return;
  }
  trajectoryView.controls.update();
  trajectoryView.renderer.render(trajectoryView.scene, trajectoryView.camera);
}

function resizeTrajectoryPlot() {
  applyTrajectoryRendererSize();
  renderTrajectoryScene();
}

async function loadTrajectoryForEpisode(episodeIndex) {
  const requestId = state.trajectoryRequest + 1;
  state.trajectoryRequest = requestId;
  state.trajectory = null;
  state.lastTrajectoryHighlightFrame = null;
  disposeTrajectoryView();
  el.trajectoryState.textContent = "加载中";
  drawGripperCurves();
  try {
    const trajectory = await requestJson(apiUrl("/api/trajectory", { episode_index: episodeIndex, max_points: 900 }));
    if (state.trajectoryRequest !== requestId || state.currentIndex !== episodeIndex) {
      return;
    }
    renderTrajectory3D(trajectory);
  } catch (error) {
    if (state.trajectoryRequest !== requestId) {
      return;
    }
    el.trajectoryState.textContent = error.message || "轨迹加载失败";
    drawCanvasMessage(el.leftGripperCanvas, "轨迹加载失败");
    drawCanvasMessage(el.rightGripperCanvas, "轨迹加载失败");
  }
}

function scheduleTrajectoryForEpisode(episodeIndex) {
  if (state.trajectoryTimer) {
    window.clearTimeout(state.trajectoryTimer);
    state.trajectoryTimer = null;
  }
  if (episodeIndex === null || episodeIndex === undefined) {
    state.trajectory = null;
    state.lastTrajectoryHighlightFrame = null;
    disposeTrajectoryView();
    return;
  }
  state.trajectoryTimer = window.setTimeout(() => {
    state.trajectoryTimer = null;
    if (state.currentIndex === episodeIndex) {
      loadTrajectoryForEpisode(episodeIndex);
    }
  }, 120);
}

function renderCurrent(current) {
  state.current = current;
  state.currentIndex = current?.episode?.episode_index ?? null;
  state.trajectory = null;
  clearHeadHandOverlay();
  if (state.currentIndex === null) {
    resetNavigationAnchor();
  }
  renderHeader(current);
  renderCameraCanvases(current?.videos || []);
  renderHeadFrames(current).catch((error) => {
    if (el.headFramesStrip) {
      el.headFramesStrip.innerHTML = `<div class="frame-placeholder">${escapeHtml(error.message || "抽帧失败")}</div>`;
    }
  });
  renderLabelForm(current?.label || {});
  renderEpisodeList();
  if (state.currentIndex !== null) {
    scheduleTrajectoryForEpisode(state.currentIndex);
  }
}

function updateEpisodeInList(episodeIndex, label, episodeLabelSummary, episodeSummary = null) {
  state.episodes = state.episodes.map((episode) => {
    if (episode.episode_index !== episodeIndex) {
      return episode;
    }
    const nextEffectiveLabel = episodeSummary && Object.prototype.hasOwnProperty.call(episodeSummary, "effective_label")
      ? episodeSummary.effective_label
      : episodeLabelSummary && Object.prototype.hasOwnProperty.call(episodeLabelSummary, "effective_label")
        ? episodeLabelSummary.effective_label
        : episode.effective_label;
    return {
      ...episode,
      ...(episodeSummary || {}),
      status: normalizeStatus(episodeSummary?.status || label.status),
      issues: episodeSummary?.issues || label.issues || [],
      has_note: episodeSummary?.has_note ?? Boolean((label.note || "").trim()),
      label_count: episodeLabelSummary?.label_count ?? episode.label_count,
      label_users: episodeLabelSummary?.users ?? episode.label_users,
      all_statuses: episodeLabelSummary?.statuses ?? episode.all_statuses,
      effective_label: nextEffectiveLabel,
      locked_by: episodeSummary?.locked_by ?? episode.locked_by,
    };
  });
  renderEpisodeList();
}

function isEpisodeLocked(episode) {
  if (state.adminReview) {
    return false;
  }
  return Array.isArray(episode?.locked_by) && episode.locked_by.length > 0;
}

function selectableEpisode(preferLast = false) {
  const list = preferLast ? [...state.episodes].reverse() : state.episodes;
  return list.find((episode) => !isEpisodeLocked(episode)) || list[0] || null;
}

function selectableEpisodeNear(index = 0) {
  if (!state.episodes.length) {
    return null;
  }
  const start = Math.max(0, Math.min(state.episodes.length - 1, index));
  for (let offset = 0; offset < state.episodes.length; offset += 1) {
    const forward = start + offset;
    if (forward < state.episodes.length && !isEpisodeLocked(state.episodes[forward])) {
      return state.episodes[forward];
    }
    const backward = start - offset;
    if (backward >= 0 && !isEpisodeLocked(state.episodes[backward])) {
      return state.episodes[backward];
    }
  }
  return state.episodes[start] || state.episodes[0] || null;
}

function currentEpisodeVisibleInList() {
  return state.currentIndex !== null
    && state.episodes.some((episode) => episode.episode_index === state.currentIndex);
}

function currentListKey() {
  return [
    state.datasetPath,
    state.user,
    state.page,
    state.pageSize,
    state.adminReview ? state.status : "",
  ].join("\u001f");
}

function resetNavigationAnchor() {
  state.navAnchor = { listKey: currentListKey(), episodeIndex: null, listIndex: -1 };
}

function updateNavigationAnchor(episodeIndex) {
  state.navAnchor = {
    listKey: currentListKey(),
    episodeIndex,
    listIndex: state.episodes.findIndex((episode) => episode.episode_index === episodeIndex),
  };
}

function navigationAnchorIndex() {
  const key = currentListKey();
  const anchor = state.navAnchor || {};
  if (anchor.listKey === key && anchor.episodeIndex === state.currentIndex) {
    const anchoredEpisode = state.episodes[anchor.listIndex];
    if (anchoredEpisode?.episode_index === anchor.episodeIndex) {
      return anchor.listIndex;
    }
  }
  const index = state.episodes.findIndex((episode) => episode.episode_index === state.currentIndex);
  if (index >= 0) {
    updateNavigationAnchor(state.currentIndex);
  }
  return index;
}

function applyEpisodeListData(data) {
  applyDatasetPath(data.dataset_path);
  const responsePage = Math.max(1, Math.floor(Number(data.page) || 1));
  if (responsePage !== state.page) {
    state.page = responsePage;
    resetNavigationAnchor();
    window.localStorage.setItem(PAGE_STORAGE_KEY, String(state.page));
    syncBrowserUrl();
  }
  state.total = data.total;
  state.episodes = data.episodes || [];
  state.counts = data.counts;
  state.users = data.users || [];
  state.info = data.info;
  if (currentEpisodeVisibleInList()) {
    updateNavigationAnchor(state.currentIndex);
  }
  renderSummary(data.counts);
  renderEpisodeList();
  renderPager();
}

async function fetchCurrentEpisodeListData({ refresh = false } = {}) {
  return requestJson(apiUrl("/api/episodes", {
    page: state.page,
    page_size: state.pageSize,
    status: state.adminReview && state.status !== "all" ? state.status : "",
    refresh: refresh ? "1" : "",
  }));
}

async function loadEpisodes({ refresh = false, keepSelection = true, preferLast = false, selectEpisodeIndex = null } = {}) {
  const requestId = state.listRequest + 1;
  state.listRequest = requestId;
  if (!keepSelection) {
    state.currentIndex = null;
    state.current = null;
    resetNavigationAnchor();
  }
  applyDatasetPath(state.datasetPath);
  window.localStorage.setItem(USER_STORAGE_KEY, state.user);
  window.localStorage.setItem(PAGE_STORAGE_KEY, String(state.page));
  syncBrowserUrl();
  const data = await fetchCurrentEpisodeListData({ refresh });
  if (requestId !== state.listRequest) {
    return;
  }
  applyEpisodeListData(data);

  const targetIndex = selectEpisodeIndex === null ? null : Number(selectEpisodeIndex);
  if (Number.isInteger(targetIndex)) {
    if (state.episodes.some((episode) => episode.episode_index === targetIndex)) {
      await selectEpisode(targetIndex);
      scrollCurrentIntoView();
    } else if (state.episodes[0]) {
      await selectEpisode(state.episodes[0].episode_index);
    } else {
      renderCurrent(null);
    }
    return;
  }

  if (!keepSelection || state.currentIndex === null) {
    const next = selectableEpisode(preferLast);
    if (next) {
      await selectEpisode(next.episode_index);
    } else {
      renderCurrent(null);
    }
    return;
  }

  if (currentEpisodeVisibleInList()) {
    renderEpisodeList();
  } else if (state.episodes[0]) {
    await selectEpisode(state.episodes[0].episode_index);
  } else {
    renderCurrent(null);
  }
}

async function jumpToInitialEpisode() {
  if (state.initialEpisodeIndex === null) {
    await loadEpisodes({ keepSelection: false });
    return;
  }
  const targetIndex = state.initialEpisodeIndex;
  state.initialEpisodeIndex = null;
  const data = await requestJson(apiUrl("/api/episode_lookup", {
    q: String(targetIndex),
    page_size: state.pageSize,
    status: state.adminReview && state.status !== "all" ? state.status : "",
  }));
  if (data.match?.episode_index === targetIndex) {
    state.page = data.page || state.page;
    await loadEpisodes({ keepSelection: false, selectEpisodeIndex: targetIndex });
  } else {
    state.page = Math.max(1, Math.floor(targetIndex / state.pageSize) + 1);
    await loadEpisodes({ keepSelection: false, selectEpisodeIndex: targetIndex });
  }
}

async function selectEpisode(episodeIndex) {
  const requestId = state.episodeRequest + 1;
  state.episodeRequest = requestId;
  setSaveState("");
  updateNavigationAnchor(episodeIndex);
  const episodePath = state.adminReview ? "/api/admin/episode" : "/api/episode";
  const current = await requestJson(apiUrl(episodePath, { episode_index: episodeIndex }));
  const responseIndex = current.episode?.episode_index ?? current.episode_index;
  if (requestId !== state.episodeRequest || responseIndex !== episodeIndex) {
    return;
  }
  renderCurrent(current);
}

async function syncSharedState() {
  if (state.userActionPending > 0) {
    return;
  }
  if (state.syncInFlight) {
    return;
  }
  state.syncInFlight = true;
  try {
    const data = await fetchCurrentEpisodeListData();
    applyEpisodeListData(data);

    if (state.currentIndex !== null) {
      const statePath = state.adminReview ? "/api/admin/episode" : "/api/episode_state";
      const current = await requestJson(apiUrl(statePath, { episode_index: state.currentIndex }));
      const responseIndex = current.episode_index ?? current.episode?.episode_index;
      if (responseIndex !== state.currentIndex) {
        return;
      }
      state.counts = current.counts || state.counts;
      state.users = current.users || state.users;
      if (state.current) {
        state.current = {
          ...state.current,
          ...current,
          label: current.label,
          summary: current.summary,
        };
      }
      renderSummary(state.counts);
      renderLabelForm(current.label || {});
      renderHeader(state.current);
      updateEpisodeInList(
        state.currentIndex,
        current.label || {},
        current.episode_label_summary,
        current.summary,
      );
    }
  } catch (error) {
    console.debug("sync failed", error);
  } finally {
    state.syncInFlight = false;
  }
}

function releaseCurrentPresence() {
  if (state.adminReview) {
    return;
  }
  if (state.currentIndex === null) {
    return;
  }
  const payload = JSON.stringify({ episode_index: state.currentIndex, action: "release" });
  const blob = new Blob([payload], { type: "application/json" });
  navigator.sendBeacon?.(apiUrl("/api/presence"), blob);
}

function startSyncLoop() {
  if (state.syncTimer) {
    window.clearInterval(state.syncTimer);
  }
  state.syncTimer = window.setInterval(() => {
    if (!document.hidden) {
      syncSharedState();
    }
  }, 2000);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      releaseCurrentPresence();
    } else {
      syncSharedState();
    }
  });
  window.addEventListener("beforeunload", releaseCurrentPresence);
}

async function saveLabel(status = state.selectedStatus) {
  if (state.currentIndex === null) {
    return;
  }
  const requestId = state.labelRequest + 1;
  state.labelRequest = requestId;
  const episodeIndex = state.currentIndex;
  const finalStatus = STATUS_ORDER.includes(status) ? status : "pending";
  const previousStatus = normalizeStatus(state.current?.label?.status || state.current?.summary?.status || state.selectedStatus);
  const shouldPlayRejectOverlay = finalStatus === "reject" && previousStatus !== "reject";
  const shouldPlayReviveOverlay = finalStatus === "accept" && previousStatus !== "accept";
  const labelEffectRequest = ++state.labelEffectRequest;
  const payload = {
    episode_index: episodeIndex,
    status: finalStatus,
    issues: [],
    note: "",
  };
  state.selectedStatus = finalStatus;
  renderStatusButtons();
  setSaveState("保存中");
  const labelPath = state.adminReview ? "/api/admin/label" : "/api/label";
  const result = await requestJson(apiUrl(labelPath), {
    method: "POST",
    body: JSON.stringify(payload),
  });
  const stillCurrent = state.currentIndex === episodeIndex && requestId === state.labelRequest;
  state.counts = result.counts;
  state.users = result.users || state.users;
  renderSummary(result.counts);
  if (stillCurrent) {
    renderLabelForm(result.label);
  }
  if (stillCurrent && state.current) {
    state.current.label = result.label;
    state.current.summary = result.summary || {
      ...state.current.summary,
      status: normalizeStatus(result.label.status),
    };
  }
  if (stillCurrent) {
    renderHeader(state.current);
  }
  if (state.adminReview && state.status !== "all") {
    await loadEpisodes({ keepSelection: true });
  } else {
    updateEpisodeInList(episodeIndex, result.label, result.episode_label_summary, result.summary);
  }
  if (shouldPlayRejectOverlay) {
    const overlayRequest = ++state.rejectOverlayRequest;
    collectorNameForEpisode(payload.episode_index).then((collectorName) => {
      if (state.currentIndex === episodeIndex && overlayRequest === state.rejectOverlayRequest && labelEffectRequest === state.labelEffectRequest) {
        playRejectOverlay(collectorName);
      }
    });
  }
  if (shouldPlayReviveOverlay) {
    collectorNameForEpisode(payload.episode_index).then((collectorName) => {
      if (state.currentIndex === episodeIndex && labelEffectRequest === state.labelEffectRequest) {
        playReviveOverlay(collectorName);
      }
    });
  }
  if (stillCurrent) {
    setSaveState("已保存");
  }
}

async function clearLabel() {
  if (state.currentIndex === null) {
    return;
  }
  const requestId = state.labelRequest + 1;
  state.labelRequest = requestId;
  const episodeIndex = state.currentIndex;
  setSaveState("保存中");
  const result = await requestJson(apiUrl("/api/label"), {
    method: "POST",
    body: JSON.stringify({
      episode_index: episodeIndex,
      status: "unlabeled",
      issues: [],
      note: "",
    }),
  });
  const stillCurrent = state.currentIndex === episodeIndex && requestId === state.labelRequest;
  state.counts = result.counts;
  state.users = result.users || state.users;
  renderSummary(result.counts);
  if (stillCurrent) {
    renderLabelForm(result.label);
  }
  if (stillCurrent && state.current) {
    state.current.label = result.label;
    state.current.summary = result.summary || {
      ...state.current.summary,
      status: "pending",
    };
  }
  if (stillCurrent) {
    renderHeader(state.current);
  }
  updateEpisodeInList(episodeIndex, result.label, result.episode_label_summary, result.summary);
  if (stillCurrent) {
    setSaveState("已清除");
  }
}

async function cycleStatus(delta) {
  const current = STATUS_ORDER.includes(state.selectedStatus) ? state.selectedStatus : "pending";
  const index = STATUS_ORDER.indexOf(current);
  const nextIndex = Math.max(0, Math.min(STATUS_ORDER.length - 1, index + delta));
  if (nextIndex === index) {
    return;
  }
  const next = STATUS_ORDER[nextIndex];
  await saveLabel(next);
}

async function moveEpisode(delta) {
  if (!state.episodes.length) {
    return;
  }
  const position = navigationAnchorIndex();
  if (position < 0) {
    const next = selectableEpisode(delta < 0);
    if (next) {
      await selectEpisode(next.episode_index);
      scrollCurrentIntoView();
    }
    return;
  }
  let nextPosition = position + delta;
  while (nextPosition >= 0 && nextPosition < state.episodes.length) {
    const episode = state.episodes[nextPosition];
    if (!isEpisodeLocked(episode)) {
      await selectEpisode(episode.episode_index);
      scrollCurrentIntoView();
      return;
    }
    nextPosition += delta;
  }
  const pageCount = Math.max(1, Math.ceil(state.total / state.pageSize));
  if (delta > 0 && state.page < pageCount) {
    state.page += 1;
    await loadEpisodes({ keepSelection: false });
  } else if (delta < 0 && state.page > 1) {
    state.page -= 1;
    await loadEpisodes({ keepSelection: false, preferLast: true });
  }
}

function scrollCurrentIntoView() {
  const item = el.episodeList?.querySelector(`.episode-item[data-index="${state.currentIndex}"]`);
  item?.scrollIntoView({ block: "nearest" });
}

function focusEpisodeNavigation() {
  el.episodeList?.focus({ preventScroll: true });
}

function currentPlaybackRate() {
  const configured = Number(el.speedSelect?.value);
  if (Number.isFinite(configured) && configured > 0) {
    return configured;
  }
  return state.phone ? PHONE_DEFAULT_PLAYBACK_RATE : 1;
}

function applyPlaybackRate() {
  const rate = currentPlaybackRate();
  state.hiddenVideos.forEach((video) => {
    if (video) {
      video.playbackRate = rate;
    }
  });
}

function playAll() {
  applyPlaybackRate();
  syncVideoTimes(true);
  state.hiddenVideos.forEach((video) => {
    if (video) {
      video.play().catch(() => {});
    }
  });
}

function pauseAll() {
  state.hiddenVideos.forEach((video) => {
    if (video) {
      video.pause();
    }
  });
}

function restartAll() {
  setAllVideoProgress(0);
}

function downloadExport(kind) {
  const path = kind === "csv" ? "/api/export.csv" : "/api/export.jsonl";
  window.open(apiUrl(path), "_blank");
}

function debounce(fn, ms) {
  let timer = null;
  return (...args) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => fn(...args), ms);
  };
}

async function jumpToSearchResult() {
  const query = el.searchInput?.value.trim() || "";
  const requestId = state.searchRequest + 1;
  state.searchRequest = requestId;
  if (!query) {
    setSaveState("");
    return;
  }
  setSaveState("搜索中");
  const data = await requestJson(apiUrl("/api/episode_lookup", {
    q: query,
    page_size: state.pageSize,
    status: state.adminReview && state.status !== "all" ? state.status : "",
  }));
  if (state.searchRequest !== requestId) {
    return;
  }
  if (!data.match) {
    setSaveState(`未找到 ${query}`, true);
    return;
  }
  releaseCurrentPresence();
  state.page = data.page || 1;
  await loadEpisodes({ keepSelection: false, selectEpisodeIndex: data.match.episode_index });
  setSaveState(`已定位 ${data.match.episode_name || episodeName(data.match.episode_index)}`);
}

function curveRatioFromEvent(canvas, event) {
  const rect = canvas.getBoundingClientRect();
  const pad = { left: 42, right: 12 };
  const plotWidth = Math.max(1, rect.width - pad.left - pad.right);
  const x = event.clientX - rect.left;
  return Math.max(0, Math.min(1, (x - pad.left) / plotWidth));
}

function wristVideoInfo(side) {
  const videos = state.current?.videos || [];
  const normalized = videos.map((video, index) => ({
    video,
    index,
    text: `${video.camera || ""} ${video.key || ""}`.toLowerCase(),
  }));
  const exactNeedle = side === "left" ? "wrist_image_1" : "wrist_image_2";
  const sideNeedle = side === "left" ? "left" : "right";
  const exact = normalized.find((item) => item.text.includes(exactNeedle) || item.text.includes(sideNeedle));
  if (exact) {
    return exact;
  }
  const wrists = normalized.filter((item) => item.text.includes("wrist"));
  return wrists[side === "left" ? 0 : 1] || wrists[0] || null;
}

function formatVideoTime(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) {
    return "0:00";
  }
  const rounded = Math.floor(seconds);
  const minutes = Math.floor(rounded / 60);
  const rest = String(rounded % 60).padStart(2, "0");
  return `${minutes}:${rest}`;
}

function modalVideoRatio() {
  const video = el.modalVideo;
  if (!video || !Number.isFinite(video.duration) || video.duration <= 0) {
    return 0;
  }
  return Math.max(0, Math.min(1, video.currentTime / video.duration));
}

function updateModalVideoUI(ratio = modalVideoRatio()) {
  const normalized = Math.max(0, Math.min(1, Number.isFinite(ratio) ? ratio : 0));
  if (el.modalVideoProgress && !state.isDraggingModalProgress) {
    el.modalVideoProgress.value = String(Math.round(normalized * 1000));
  }
  if (el.modalVideoTime && el.modalVideo) {
    el.modalVideoTime.textContent = `${formatVideoTime(el.modalVideo.currentTime)} / ${formatVideoTime(el.modalVideo.duration)}`;
  }
  if (el.modalPlayPauseButton && el.modalVideo) {
    el.modalPlayPauseButton.textContent = el.modalVideo.paused ? "播放" : "暂停";
  }
}

function setModalVideoProgress(ratio, syncMain = false, keepPaused = false) {
  const normalized = Math.max(0, Math.min(1, Number.isFinite(ratio) ? ratio : 0));
  if (el.modalVideo && Number.isFinite(el.modalVideo.duration) && el.modalVideo.duration > 0) {
    el.modalVideo.currentTime = normalized * el.modalVideo.duration;
  }
  if (keepPaused) {
    el.modalVideo?.pause();
  }
  updateModalVideoUI(normalized);
  if (syncMain) {
    setAllVideoProgress(normalized);
  }
}

function closeWristVideoModal() {
  if (!el.videoModal || el.videoModal.hidden) {
    return;
  }
  el.modalVideo?.pause();
  el.modalVideo?.removeAttribute("src");
  el.modalVideo?.load();
  el.videoModal.hidden = true;
  state.modalVideoSide = null;
  state.isDraggingModalProgress = false;
}

function openWristVideoModal(side, ratio) {
  const item = wristVideoInfo(side);
  if (!item || !el.videoModal || !el.modalVideo) {
    setSaveState(`${side === "left" ? "左" : "右"}腕部视频不存在`, true);
    return;
  }
  state.modalVideoSide = side;
  const sideLabel = side === "left" ? "左腕" : "右腕";
  el.modalVideoTitle.textContent = `${sideLabel}视频 · ${item.video.camera || item.video.key || ""}`;
  el.videoModal.hidden = false;
  el.modalVideo.pause();
  el.modalVideo.removeAttribute("src");
  el.modalVideo.load();
  el.modalVideo.src = item.video.url;
  el.modalVideo.loop = true;
  el.modalVideo.muted = true;
  el.modalVideo.playbackRate = 1;
  el.modalVideo.addEventListener("loadedmetadata", () => {
    el.modalVideo.playbackRate = 1;
    setModalVideoProgress(ratio, false, true);
    el.modalVideo.pause();
    updateModalVideoUI(ratio);
  }, { once: true });
  updateModalVideoUI(ratio);
}

function bindCurveHover(canvas, side) {
  if (!canvas) {
    return;
  }
  canvas.addEventListener("mousemove", (event) => {
    const rect = canvas.getBoundingClientRect();
    state.curveHover[side] = {
      x: event.clientX - rect.left,
      y: event.clientY - rect.top,
    };
    drawGripperCurves();
  });
  canvas.addEventListener("mouseleave", () => {
    state.curveHover[side] = null;
    drawGripperCurves();
  });
  canvas.addEventListener("click", (event) => {
    if (state.phone && performance.now() < state.phoneSuppressClickUntil) {
      return;
    }
    openWristVideoModal(side, curveRatioFromEvent(canvas, event));
  });
}

function setPhoneDrawerOpen(open) {
  if (!el.phoneDrawer) {
    return;
  }
  el.phoneDrawer.classList.toggle("open", open);
  el.phoneDrawer.setAttribute("aria-hidden", open ? "false" : "true");
  el.phoneSettingsButton?.setAttribute("aria-expanded", open ? "true" : "false");
  document.body.classList.toggle("phone-drawer-open", open);
}

function shouldIgnorePhoneSwipe(event) {
  const target = event.target;
  if (!(target instanceof Element)) {
    return true;
  }
  if (el.videoModal && !el.videoModal.hidden) {
    return true;
  }
  return Boolean(target.closest(
    "button, input, select, textarea, a, .phone-drawer, .trajectory-panel, .video-modal",
  ));
}

function startPhoneSwipe(event, point, id) {
  if (!point || shouldIgnorePhoneSwipe(event)) {
    state.phoneSwipe = null;
    return;
  }
  state.phoneSwipe = {
    id,
    x: point.clientX,
    y: point.clientY,
    time: performance.now(),
  };
}

function finishPhoneSwipe(point, id) {
  const swipe = state.phoneSwipe;
  if (!point || !swipe || swipe.id !== id) {
    return;
  }
  state.phoneSwipe = null;
  const dx = point.clientX - swipe.x;
  const dy = point.clientY - swipe.y;
  const absX = Math.abs(dx);
  const absY = Math.abs(dy);
  const elapsed = performance.now() - swipe.time;
  if (elapsed > 1200 || Math.max(absX, absY) < 42 || Math.max(absX, absY) / Math.max(1, Math.min(absX, absY)) < 1.25) {
    return;
  }
  state.phoneSuppressClickUntil = performance.now() + 450;
  if (absX > absY) {
    enqueueUserAction(() => cycleStatus(dx > 0 ? 1 : -1));
  } else {
    enqueueUserAction(() => moveEpisode(dy < 0 ? 1 : -1));
  }
}

function cancelPhoneSwipe(id = null) {
  if (id === null || state.phoneSwipe?.id === id) {
    state.phoneSwipe = null;
  }
}

function bindPhoneControls() {
  if (!state.phone) {
    return;
  }
  el.phoneSettingsButton?.addEventListener("click", () => setPhoneDrawerOpen(true));
  el.phoneDrawerCloseButton?.addEventListener("click", () => setPhoneDrawerOpen(false));
  el.phoneDrawer?.addEventListener("click", (event) => {
    if (event.target.closest("[data-phone-drawer-close]")) {
      setPhoneDrawerOpen(false);
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      setPhoneDrawerOpen(false);
    }
  });

  const surface = document.querySelector(".phone-main");
  surface?.addEventListener("pointerdown", (event) => {
    if (event.pointerType === "touch") {
      return;
    }
    startPhoneSwipe(event, event, event.pointerId);
  }, { passive: true });
  surface?.addEventListener("pointerup", (event) => {
    if (event.pointerType === "touch") {
      return;
    }
    finishPhoneSwipe(event, event.pointerId);
  }, { passive: true });
  surface?.addEventListener("pointercancel", (event) => {
    if (event.pointerType !== "touch") {
      cancelPhoneSwipe(event.pointerId);
    }
  }, { passive: true });
  surface?.addEventListener("touchstart", (event) => {
    const touch = event.changedTouches?.[0] || event.touches?.[0];
    startPhoneSwipe(event, touch, touch?.identifier ?? "touch");
  }, { passive: true });
  surface?.addEventListener("touchend", (event) => {
    const swipe = state.phoneSwipe;
    const changed = Array.from(event.changedTouches || []);
    const touch = changed.find((item) => item.identifier === swipe?.id) || changed[0];
    finishPhoneSwipe(touch, touch?.identifier ?? "touch");
  }, { passive: true });
  surface?.addEventListener("touchcancel", (event) => {
    const swipe = state.phoneSwipe;
    const changed = Array.from(event.changedTouches || []);
    const touch = changed.find((item) => item.identifier === swipe?.id) || changed[0];
    cancelPhoneSwipe(touch?.identifier ?? "touch");
  }, { passive: true });
}

function bindEvents() {
  if (el.datasetInput) {
    el.datasetInput.value = state.datasetPath;
  }
  if (el.userInput) {
    el.userInput.value = state.user;
  }
  if (el.statusFilter) {
    el.statusFilter.value = state.status;
  }
  bindCurveHover(el.leftGripperCanvas, "left");
  bindCurveHover(el.rightGripperCanvas, "right");

  async function applyUserInput() {
    const nextUser = el.userInput.value.trim() || "default";
    if (nextUser === state.user) {
      return;
    }
    releaseCurrentPresence();
    state.user = nextUser;
    window.localStorage.setItem(USER_STORAGE_KEY, state.user);
    await saveUserSession();
    state.page = 1;
    await runWithErrors(() => loadEpisodes({ keepSelection: false }));
  }

  const debouncedApplyUserInput = debounce(() => {
    runWithErrors(applyUserInput);
  }, 360);
  el.userInput?.addEventListener("input", debouncedApplyUserInput);
  el.userInput?.addEventListener("change", () => {
    runWithErrors(applyUserInput);
  });
  el.userInput?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      runWithErrors(applyUserInput);
      focusEpisodeNavigation();
    }
  });
  el.setUserButton?.addEventListener("click", async () => {
    await runWithErrors(applyUserInput);
  });

  el.loadDatasetButton?.addEventListener("click", () => runWithErrors(async () => {
    const nextDatasetPath = el.datasetInput?.value.trim();
    if (!nextDatasetPath) {
      setSaveState("数据集路径不能为空", true);
      return;
    }
    releaseCurrentPresence();
    const settings = await requestJson(apiUrl("/api/settings"), {
      method: "POST",
      body: JSON.stringify({ dataset_path: nextDatasetPath }),
    });
    applyDatasetPath(settings.dataset_path);
    state.page = 1;
    await loadEpisodes({ refresh: true, keepSelection: false });
  }));

  el.refreshButton?.addEventListener("click", async () => {
    await runWithErrors(() => loadEpisodes({ refresh: true }));
  });

  el.statusFilter?.addEventListener("change", async () => {
    state.status = el.statusFilter.value;
    state.page = 1;
    await runWithErrors(() => loadEpisodes({ keepSelection: false }));
  });

  const debouncedEpisodeSearch = debounce(() => {
    runWithErrors(jumpToSearchResult);
  }, 420);
  el.searchInput?.addEventListener("input", debouncedEpisodeSearch);
  el.searchInput?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      runWithErrors(jumpToSearchResult);
    }
  });

  el.episodeList?.addEventListener("click", (event) => {
    const button = event.target.closest(".episode-item");
    if (!button) {
      return;
    }
    enqueueUserAction(() => selectEpisode(Number(button.dataset.index)));
  });

  el.prevPageButton?.addEventListener("click", () => {
    if (state.page > 1) {
      enqueueUserAction(async () => {
        releaseCurrentPresence();
        state.page -= 1;
        await loadEpisodes({ keepSelection: false });
      });
    }
  });

  el.nextPageButton?.addEventListener("click", () => {
    const pageCount = Math.max(1, Math.ceil(state.total / state.pageSize));
    if (state.page < pageCount) {
      enqueueUserAction(async () => {
        releaseCurrentPresence();
        state.page += 1;
        await loadEpisodes({ keepSelection: false });
      });
    }
  });

  el.exportJsonlButton?.addEventListener("click", () => downloadExport("jsonl"));
  el.exportCsvButton?.addEventListener("click", () => downloadExport("csv"));

  el.playAllButton?.addEventListener("click", playAll);
  el.pauseAllButton?.addEventListener("click", pauseAll);
  el.restartAllButton?.addEventListener("click", restartAll);
  el.speedSelect?.addEventListener("change", applyPlaybackRate);
  el.videoProgress?.addEventListener("pointerdown", () => {
    state.isDraggingProgress = true;
  });
  el.videoProgress?.addEventListener("input", () => {
    state.isDraggingProgress = true;
    setAllVideoProgress(Number(el.videoProgress.value) / 1000);
  });
  el.videoProgress?.addEventListener("change", () => {
    state.isDraggingProgress = false;
    setAllVideoProgress(Number(el.videoProgress.value) / 1000);
  });
  el.closeVideoModalButton?.addEventListener("click", closeWristVideoModal);
  el.videoModal?.addEventListener("click", (event) => {
    if (event.target === el.videoModal) {
      closeWristVideoModal();
    }
  });
  el.modalPlayPauseButton?.addEventListener("click", () => {
    if (!el.modalVideo) {
      return;
    }
    if (el.modalVideo.paused) {
      el.modalVideo.playbackRate = 1;
      el.modalVideo.play().catch(() => {});
    } else {
      el.modalVideo.pause();
    }
    updateModalVideoUI();
  });
  el.modalVideo?.addEventListener("timeupdate", () => {
    const ratio = modalVideoRatio();
    updateModalVideoUI(ratio);
  });
  el.modalVideo?.addEventListener("play", () => updateModalVideoUI());
  el.modalVideo?.addEventListener("pause", () => updateModalVideoUI());
  el.modalVideoProgress?.addEventListener("pointerdown", () => {
    state.isDraggingModalProgress = true;
    el.modalVideo?.pause();
  });
  el.modalVideoProgress?.addEventListener("input", () => {
    state.isDraggingModalProgress = true;
    setModalVideoProgress(Number(el.modalVideoProgress.value) / 1000, false, true);
  });
  el.modalVideoProgress?.addEventListener("change", () => {
    state.isDraggingModalProgress = false;
    setModalVideoProgress(Number(el.modalVideoProgress.value) / 1000, false, true);
  });
  window.addEventListener("pointerup", () => {
    if (state.isDraggingProgress && el.videoProgress) {
      state.isDraggingProgress = false;
      setAllVideoProgress(Number(el.videoProgress.value) / 1000);
    }
    if (state.isDraggingModalProgress && el.modalVideoProgress) {
      state.isDraggingModalProgress = false;
      setModalVideoProgress(Number(el.modalVideoProgress.value) / 1000, false, true);
    }
  });

  el.rejectButton?.addEventListener("click", () => enqueueUserAction(() => saveLabel("reject")));
  el.pendingButton?.addEventListener("click", () => enqueueUserAction(() => saveLabel("pending")));
  el.acceptButton?.addEventListener("click", () => enqueueUserAction(() => saveLabel("accept")));
  el.saveButton?.addEventListener("click", () => enqueueUserAction(() => saveLabel(state.selectedStatus)));
  el.clearButton?.addEventListener("click", () => enqueueUserAction(clearLabel));

  window.addEventListener("pointerdown", primeEffectAudio, { once: true, capture: true });
  window.addEventListener("keydown", primeEffectAudio, { once: true, capture: true });

  document.addEventListener("keydown", async (event) => {
    if (event.key === "Escape" && el.videoModal && !el.videoModal.hidden) {
      event.preventDefault();
      closeWristVideoModal();
      return;
    }
    if (event.ctrlKey || event.metaKey || event.altKey) {
      return;
    }
    const arrowKeys = new Set(["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp"]);
    if (arrowKeys.has(event.key)) {
      event.preventDefault();
      event.stopPropagation();
    }
    if (state.adminReview && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
      return;
    }
    if (event.key === "ArrowRight") {
      enqueueUserAction(() => cycleStatus(1));
    } else if (event.key === "ArrowLeft") {
      enqueueUserAction(() => cycleStatus(-1));
    } else if (event.key === "ArrowDown") {
      enqueueUserAction(() => moveEpisode(1));
    } else if (event.key === "ArrowUp") {
      enqueueUserAction(() => moveEpisode(-1));
    } else if (event.key.toLowerCase() === "r") {
      const target = event.target;
      const editing = ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
      if (editing) {
        return;
      }
      event.preventDefault();
      enqueueUserAction(() => saveLabel("reject"));
    } else if (event.key.toLowerCase() === "p") {
      const target = event.target;
      const editing = ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
      if (editing) {
        return;
      }
      event.preventDefault();
      enqueueUserAction(() => saveLabel("pending"));
    } else if (event.key.toLowerCase() === "a") {
      const target = event.target;
      const editing = ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
      if (editing) {
        return;
      }
      event.preventDefault();
      enqueueUserAction(() => saveLabel("accept"));
    } else if (event.key === " ") {
      const target = event.target;
      const editing = ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
      if (editing) {
        return;
      }
      event.preventDefault();
      const anyPlaying = state.hiddenVideos.some((video) => video && !video.paused);
      if (anyPlaying) {
        pauseAll();
      } else {
        playAll();
      }
    }
  }, { capture: true });

  window.addEventListener("resize", () => {
    setHeadVideoSize(state.headVideoAspect);
    syncQuickVideoLayout();
    drawHeadVideoCanvas();
    drawQuickVideoCanvases();
    drawGripperCurves();
    resizeTrajectoryPlot();
  });
  window.visualViewport?.addEventListener("resize", () => {
    syncQuickVideoLayout();
    drawQuickVideoCanvases();
    resizeTrajectoryPlot();
  });
  bindPhoneControls();
}

async function runWithErrors(fn) {
  try {
    await fn();
  } catch (error) {
    setSaveState(error.message || String(error), true);
  }
}

function enqueueUserAction(fn) {
  state.userActionPending += 1;
  const run = state.userActionQueue
    .catch(() => {})
    .then(async () => {
      await runWithErrors(fn);
    })
    .finally(() => {
      state.userActionPending = Math.max(0, state.userActionPending - 1);
    });
  state.userActionQueue = run;
  return run;
}

function animationLoop(now = 0) {
  if (!state.lastPlaybackUiAt || now - state.lastPlaybackUiAt >= 83) {
    state.lastPlaybackUiAt = now;
    syncVideoTimes(false);
    drawGripperCurves();
    updateTrajectoryHighlight(false);
  }
  renderTrajectoryScene(now);
  window.requestAnimationFrame(animationLoop);
}

async function main() {
  initElements();
  initRejectAssets();
  initReviveAssets();
  renderTrajectoryLegends();
  renderIssueOptions();
  await loadUserSession();
  bindEvents();
  drawCanvasMessage(el.leftGripperCanvas, "等待轨迹数据");
  drawCanvasMessage(el.rightGripperCanvas, "等待轨迹数据");
  await loadTrajectoryRenderer();
  animationLoop();
  await runWithErrors(() => jumpToInitialEpisode());
  startSyncLoop();
}

main();
