const DEFAULT_DATASET = "/mnt/nm_dataset/dataset/giftbox_0628_1912episodes";
const STATUS_ORDER = ["reject", "pending", "accept"];

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
const USER_STORAGE_KEY = IS_ADMIN_REVIEW ? "lqcp.adminReview.user" : "lqcp.user";
const PAGE_STORAGE_KEY = IS_ADMIN_REVIEW ? "lqcp.adminReview.page" : "lqcp.page";

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

const state = {
  adminReview: IS_ADMIN_REVIEW,
  token: tokenFromUrl || window.localStorage.getItem("lqcp.token") || "",
  user: userFromUrl || storedUser || defaultUser,
  datasetPath: datasetFromUrl || DEFAULT_DATASET,
  page: Number.isInteger(initialPage) && initialPage > 0 ? initialPage : 1,
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
  trajectory: null,
  trajectoryRequest: 0,
  trajectoryHighlightTraceIndexes: [],
  lastTrajectoryHighlightFrame: null,
  lastTrajectoryHighlightAt: 0,
  trajectoryDomEventsBound: false,
  trajectoryPlotEventsBound: false,
  isInteractingTrajectory: false,
  isRestoringTrajectoryCamera: false,
  trajectoryCamera: null,
  trajectoryCameraRevision: 0,
  trajectoryWheelTimer: null,
  framesRequest: 0,
  searchRequest: 0,
  lastPlaybackUiAt: 0,
};

let Plotly3D = null;

const el = {};

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
    "hiddenVideos",
  ].forEach((id) => {
    el[id] = $(id);
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
      link.href = urlWithContext(path);
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
  el.totalCount.textContent = formatNumber(total);
  el.markedCount.textContent = formatNumber(marked);
  el.rejectCount.textContent = formatNumber(counts?.reject || 0);
  el.allMarkedCount.textContent = formatNumber(counts?.all_marked || 0);
  const percent = total > 0 ? Math.round((marked / total) * 100) : 0;
  el.progressBar.style.width = `${percent}%`;
}

function renderEpisodeList() {
  if (!state.episodes.length) {
    el.episodeList.innerHTML = `<div class="empty-state">没有匹配的 episode</div>`;
    return;
  }
  el.episodeList.innerHTML = state.episodes.map((episode) => {
    const active = episode.episode_index === state.currentIndex ? "active" : "";
    const status = statusClass(episode.status);
    const task = episode.task_description || episode.task_annotation || (episode.tasks || []).join(" / ");
    const sub = `${formatNumber(episode.length)} frames · ${episode.video_count || 0} videos`;
    const labelCount = episode.label_count ? `${episode.label_count} 人` : "";
    const lockedBy = Array.isArray(episode.locked_by) ? episode.locked_by.filter(Boolean) : [];
    const lockText = lockedBy.length ? (lockedBy.length === 1 ? `锁 ${lockedBy[0]}` : `锁 ${lockedBy.length}`) : "";
    const lockTitle = lockedBy.length ? `正在查看: ${lockedBy.join(", ")}` : "";
    return `
      <button class="episode-item ${active}" data-index="${episode.episode_index}" type="button">
        <span class="episode-main">
          <span class="episode-name">${escapeHtml(episode.episode_name)}</span>
          <span class="episode-task">${escapeHtml(task || "-")}</span>
          <span class="episode-sub">${escapeHtml(sub)}</span>
        </span>
        <span class="status-stack">
          <span class="status-pill ${status}">${escapeHtml(statusLabel(status))}</span>
          ${lockText ? `<span class="lock-pill" title="${escapeHtml(lockTitle)}">${escapeHtml(lockText)}</span>` : ""}
          <span class="label-count">${escapeHtml(labelCount)}</span>
        </span>
      </button>
    `;
  }).join("");
}

function renderPager() {
  const pageCount = Math.max(1, Math.ceil(state.total / state.pageSize));
  el.pageInfo.textContent = `${state.page} / ${pageCount}`;
  el.prevPageButton.disabled = state.page <= 1;
  el.nextPageButton.disabled = state.page >= pageCount;
}

function renderHeader(current) {
  const summary = current?.summary;
  const episode = current?.episode;
  const label = current?.label || {};
  const status = statusClass(label.status || summary?.status);
  el.currentStatus.className = `status-pill ${status}`;
  el.currentStatus.textContent = statusLabel(status);
  if (!episode) {
    el.episodeTitle.textContent = "选择一个 episode";
    el.episodeMeta.textContent = "";
    return;
  }
  el.episodeTitle.textContent = episode.episode_name || episodeName(episode.episode_index);
  const task = episode.task_description || episode.task_annotation || (episode.tasks || []).join(" / ");
  const bits = [
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
    button.classList.toggle("active", state.selectedStatus === status);
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
  state.headVideoIndex = findHeadVideoIndex(videos);
  state.quickVideoIndexes = {
    left: findWristVideoIndex(videos, "left"),
    head: state.headVideoIndex,
    right: findWristVideoIndex(videos, "right"),
  };
  setHeadVideoSize(16 / 9);
  if (!videos.length) {
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
      video.playbackRate = Number(el.speedSelect.value || 1);
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

function drawHeadVideoCanvas() {
  return;
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
  quickVideoEntries().forEach(([key, quickVideo]) => {
    if (state.quickVideoIndexes[key] === index && quickVideo) {
      quickVideo.style.setProperty("--video-aspect", `${video.videoWidth} / ${video.videoHeight}`);
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
    return;
  }
  const master = masterVideo();
  if (!master) {
    updateProgressUI(0);
    return;
  }
  const ratio = currentVideoRatio();
  const now = performance.now();
  const rate = Math.max(1, Number(el.speedSelect?.value || 1));
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
  drawCurve(el.leftGripperCanvas, trajectory.left?.gripper || [], trajectory.frames || [], "left gripper", "#22c55e", [0, 0.1], state.curveHover.left);
  drawCurve(el.rightGripperCanvas, trajectory.right?.gripper || [], trajectory.frames || [], "right gripper", "#ef4444", [0, 0.1], state.curveHover.right);
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

async function loadTrajectoryRenderer() {
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

function validPoint(point) {
  return Array.isArray(point) && point.length >= 3 && point.every((value) => Number.isFinite(value));
}

function compactPoints(points = []) {
  return points.filter(validPoint);
}

function trajectoryTrace(name, points = [], color) {
  const valid = compactPoints(points);
  return {
    type: "scatter3d",
    mode: "lines",
    name,
    x: valid.map((point) => point[0]),
    y: valid.map((point) => point[1]),
    z: valid.map((point) => point[2]),
    line: { color, width: 5 },
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
    marker: { color, size: [4, 7], opacity: 0.95 },
    hovertemplate: `${name}<br>x=%{x:.4f}<br>y=%{y:.4f}<br>z=%{z:.4f}<extra></extra>`,
  };
}

function trajectoryFlowTrace(name, color) {
  return {
    type: "scatter3d",
    mode: "lines",
    name,
    showlegend: false,
    x: [],
    y: [],
    z: [],
    line: { color, width: 10 },
    opacity: 0.82,
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
      color: "#f8fafc",
      size: 7,
      opacity: 1,
      line: { color, width: 5 },
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

function trajectorySample(points = [], frames = [], frame = 0) {
  if (!points.length) {
    return { point: null, trail: [] };
  }
  const rawIndex = trajectoryIndexAtFrame(frames, frame, points.length);
  const index = nearestValidPointIndex(points, rawIndex);
  if (index < 0) {
    return { point: null, trail: [] };
  }
  const start = Math.max(0, index - 14);
  const trail = [];
  for (let i = start; i <= index; i += 1) {
    if (validPoint(points[i])) {
      trail.push(points[i]);
    }
  }
  return { point: points[index], trail };
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

function cameraFromHeadMinusZ(trajectory) {
  const points = trajectory.ego?.points || [];
  const quaternions = trajectory.ego?.quaternions || [];
  const firstPose = points
    .map((point, index) => ({ point, quat: quaternions[index] }))
    .find((item) => validPoint(item.point) && validQuat(item.quat));
  const fallback = {
    up: { x: 0, y: 1, z: 0 },
    eye: { x: 1.35, y: 0.85, z: 1.35 },
  };
  if (!firstPose) {
    return fallback;
  }
  const headMinusZ = normalizeVector(rotateVectorByQuat([0, 0, -1], firstPose.quat));
  if (!headMinusZ) {
    return fallback;
  }
  const distance = 1.75;
  return {
    up: { x: 0, y: 1, z: 0 },
    eye: {
      x: -headMinusZ[0] * distance,
      y: -headMinusZ[1] * distance,
      z: -headMinusZ[2] * distance,
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
    line: { color, width: 4 },
    hoverinfo: "skip",
  };
}

function pushSegment(segments, origin, endpoint) {
  segments.x.push(origin[0], endpoint[0], null);
  segments.y.push(origin[1], endpoint[1], null);
  segments.z.push(origin[2], endpoint[2], null);
}

function poseAxesTraces(points = [], quaternions = []) {
  const available = points
    .map((point, index) => ({ point, quat: quaternions[index], index }))
    .filter((item) => validPoint(item.point) && validQuat(item.quat));
  if (!available.length) {
    return [];
  }
  const count = Math.min(7, available.length);
  const picked = new Set();
  for (let i = 0; i < count; i += 1) {
    picked.add(Math.round((i * (available.length - 1)) / Math.max(1, count - 1)));
  }
  const axisLength = 0.055;
  const xSegments = { x: [], y: [], z: [] };
  const ySegments = { x: [], y: [], z: [] };
  const zSegments = { x: [], y: [], z: [] };
  [...picked].forEach((pickedIndex) => {
    const { point, quat } = available[pickedIndex];
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
  });
  return [
    segmentTrace("local x", xSegments, "#ff4d4d"),
    segmentTrace("local y", ySegments, "#35d06f"),
    segmentTrace("local z", zSegments, "#38bdf8"),
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

function renderTrajectory3D(trajectory) {
  state.trajectory = trajectory;
  drawGripperCurves();
  if (!Plotly3D) {
    return;
  }
  const traces = [
    trajectoryTrace("左手", trajectory.left?.points || [], "#22c55e"),
    trajectoryTrace("右手", trajectory.right?.points || [], "#ef4444"),
    endpointTrace("左手", trajectory.left?.points || [], "#22c55e"),
    endpointTrace("右手", trajectory.right?.points || [], "#ef4444"),
    ...poseAxesTraces(trajectory.left?.points || [], trajectory.left?.quaternions || []),
    ...poseAxesTraces(trajectory.right?.points || [], trajectory.right?.quaternions || []),
  ].filter(Boolean);
  const highlightStart = traces.length;
  traces.push(
    trajectoryFlowTrace("left current flow", "rgba(134, 239, 172, 0.95)"),
    trajectoryNowTrace("left current", "#22c55e"),
    trajectoryFlowTrace("right current flow", "rgba(252, 165, 165, 0.95)"),
    trajectoryNowTrace("right current", "#ef4444"),
  );
  state.trajectoryHighlightTraceIndexes = [highlightStart, highlightStart + 1, highlightStart + 2, highlightStart + 3];
  state.lastTrajectoryHighlightFrame = null;
  state.lastTrajectoryHighlightAt = 0;
  state.trajectoryCamera = cloneTrajectoryCamera(cameraFromHeadMinusZ(trajectory));
  state.trajectoryCameraRevision = 0;
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
      xaxis: { ...axisStyle, title: "x" },
      yaxis: { ...axisStyle, title: "y ↑" },
      zaxis: { ...axisStyle, title: "z" },
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

function updateTrajectoryHighlight(force = false) {
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
  const trailX = (sample) => sample.trail.map((point) => point[0]);
  const trailY = (sample) => sample.trail.map((point) => point[1]);
  const trailZ = (sample) => sample.trail.map((point) => point[2]);
  const pointX = (sample) => (sample.point ? [sample.point[0]] : []);
  const pointY = (sample) => (sample.point ? [sample.point[1]] : []);
  const pointZ = (sample) => (sample.point ? [sample.point[2]] : []);

  const cameraRevision = state.trajectoryCameraRevision;
  const cameraBeforeUpdate = cloneTrajectoryCamera(state.trajectoryCamera);
  Plotly3D.restyle(el.trajectoryCanvas, {
    x: [trailX(left), pointX(left), trailX(right), pointX(right)],
    y: [trailY(left), pointY(left), trailY(right), pointY(right)],
    z: [trailZ(left), pointZ(left), trailZ(right), pointZ(right)],
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

function resizeTrajectoryPlot() {
  if (Plotly3D && el.trajectoryCanvas) {
    Plotly3D.Plots.resize(el.trajectoryCanvas);
  }
}

async function loadTrajectoryForEpisode(episodeIndex) {
  const requestId = state.trajectoryRequest + 1;
  state.trajectoryRequest = requestId;
  state.trajectory = null;
  state.trajectoryHighlightTraceIndexes = [];
  state.lastTrajectoryHighlightFrame = null;
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

function renderCurrent(current) {
  state.current = current;
  state.currentIndex = current?.episode?.episode_index ?? null;
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
    loadTrajectoryForEpisode(state.currentIndex);
  }
}

function updateEpisodeInList(episodeIndex, label, episodeLabelSummary, episodeSummary = null) {
  state.episodes = state.episodes.map((episode) => {
    if (episode.episode_index !== episodeIndex) {
      return episode;
    }
    return {
      ...episode,
      ...(episodeSummary || {}),
      status: normalizeStatus(episodeSummary?.status || label.status),
      issues: episodeSummary?.issues || label.issues || [],
      has_note: episodeSummary?.has_note ?? Boolean((label.note || "").trim()),
      label_count: episodeLabelSummary?.label_count ?? episode.label_count,
      label_users: episodeLabelSummary?.users ?? episode.label_users,
      all_statuses: episodeLabelSummary?.statuses ?? episode.all_statuses,
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

async function selectEpisode(episodeIndex) {
  setSaveState("");
  updateNavigationAnchor(episodeIndex);
  const episodePath = state.adminReview ? "/api/admin/episode" : "/api/episode";
  const current = await requestJson(apiUrl(episodePath, { episode_index: episodeIndex }));
  renderCurrent(current);
}

async function syncSharedState() {
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
  const finalStatus = STATUS_ORDER.includes(status) ? status : "pending";
  const payload = {
    episode_index: state.currentIndex,
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
  state.counts = result.counts;
  state.users = result.users || state.users;
  renderSummary(result.counts);
  renderLabelForm(result.label);
  if (state.current) {
    state.current.label = result.label;
    state.current.summary = result.summary || {
      ...state.current.summary,
      status: normalizeStatus(result.label.status),
    };
  }
  renderHeader(state.current);
  if (state.adminReview && state.status !== "all") {
    await loadEpisodes({ keepSelection: true });
  } else {
    updateEpisodeInList(state.currentIndex, result.label, result.episode_label_summary, result.summary);
  }
  setSaveState("已保存");
}

async function clearLabel() {
  if (state.currentIndex === null) {
    return;
  }
  setSaveState("保存中");
  const result = await requestJson(apiUrl("/api/label"), {
    method: "POST",
    body: JSON.stringify({
      episode_index: state.currentIndex,
      status: "unlabeled",
      issues: [],
      note: "",
    }),
  });
  state.counts = result.counts;
  state.users = result.users || state.users;
  renderSummary(result.counts);
  renderLabelForm(result.label);
  if (state.current) {
    state.current.label = result.label;
    state.current.summary = result.summary || {
      ...state.current.summary,
      status: "pending",
    };
  }
  renderHeader(state.current);
  updateEpisodeInList(state.currentIndex, result.label, result.episode_label_summary, result.summary);
  setSaveState("已清除");
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
  const item = el.episodeList.querySelector(`.episode-item[data-index="${state.currentIndex}"]`);
  item?.scrollIntoView({ block: "nearest" });
}

function focusEpisodeNavigation() {
  el.episodeList?.focus({ preventScroll: true });
}

function applyPlaybackRate() {
  const rate = Number(el.speedSelect.value || 1);
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
  const query = el.searchInput.value.trim();
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

function setModalVideoProgress(ratio, syncMain = true, keepPaused = false) {
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
  pauseAll();
  setAllVideoProgress(ratio);
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
    openWristVideoModal(side, curveRatioFromEvent(canvas, event));
  });
}

function bindEvents() {
  if (el.datasetInput) {
    el.datasetInput.value = state.datasetPath;
  }
  el.userInput.value = state.user;
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
  el.userInput.addEventListener("input", debouncedApplyUserInput);
  el.userInput.addEventListener("change", () => {
    runWithErrors(applyUserInput);
  });
  el.userInput.addEventListener("keydown", (event) => {
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
  el.searchInput.addEventListener("input", debouncedEpisodeSearch);
  el.searchInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      runWithErrors(jumpToSearchResult);
    }
  });

  el.episodeList.addEventListener("click", async (event) => {
    const button = event.target.closest(".episode-item");
    if (!button) {
      return;
    }
    await runWithErrors(() => selectEpisode(Number(button.dataset.index)));
  });

  el.prevPageButton.addEventListener("click", async () => {
    if (state.page > 1) {
      releaseCurrentPresence();
      state.page -= 1;
      await runWithErrors(() => loadEpisodes({ keepSelection: false }));
    }
  });

  el.nextPageButton.addEventListener("click", async () => {
    const pageCount = Math.max(1, Math.ceil(state.total / state.pageSize));
    if (state.page < pageCount) {
      releaseCurrentPresence();
      state.page += 1;
      await runWithErrors(() => loadEpisodes({ keepSelection: false }));
    }
  });

  el.exportJsonlButton?.addEventListener("click", () => downloadExport("jsonl"));
  el.exportCsvButton?.addEventListener("click", () => downloadExport("csv"));

  el.playAllButton.addEventListener("click", playAll);
  el.pauseAllButton.addEventListener("click", pauseAll);
  el.restartAllButton.addEventListener("click", restartAll);
  el.speedSelect.addEventListener("change", applyPlaybackRate);
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
    if (!state.isDraggingModalProgress) {
      setAllVideoProgress(ratio);
    }
  });
  el.modalVideo?.addEventListener("play", () => updateModalVideoUI());
  el.modalVideo?.addEventListener("pause", () => updateModalVideoUI());
  el.modalVideoProgress?.addEventListener("pointerdown", () => {
    state.isDraggingModalProgress = true;
    el.modalVideo?.pause();
  });
  el.modalVideoProgress?.addEventListener("input", () => {
    state.isDraggingModalProgress = true;
    setModalVideoProgress(Number(el.modalVideoProgress.value) / 1000, true, true);
  });
  el.modalVideoProgress?.addEventListener("change", () => {
    state.isDraggingModalProgress = false;
    setModalVideoProgress(Number(el.modalVideoProgress.value) / 1000, true, true);
  });
  window.addEventListener("pointerup", () => {
    if (state.isDraggingProgress && el.videoProgress) {
      state.isDraggingProgress = false;
      setAllVideoProgress(Number(el.videoProgress.value) / 1000);
    }
    if (state.isDraggingModalProgress && el.modalVideoProgress) {
      state.isDraggingModalProgress = false;
      setModalVideoProgress(Number(el.modalVideoProgress.value) / 1000, true, true);
    }
  });

  el.rejectButton.addEventListener("click", () => runWithErrors(() => saveLabel("reject")));
  el.pendingButton.addEventListener("click", () => runWithErrors(() => saveLabel("pending")));
  el.acceptButton.addEventListener("click", () => runWithErrors(() => saveLabel("accept")));
  el.saveButton?.addEventListener("click", () => runWithErrors(() => saveLabel(state.selectedStatus)));
  el.clearButton?.addEventListener("click", () => runWithErrors(clearLabel));

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
      await runWithErrors(() => cycleStatus(1));
    } else if (event.key === "ArrowLeft") {
      await runWithErrors(() => cycleStatus(-1));
    } else if (event.key === "ArrowDown") {
      await runWithErrors(() => moveEpisode(1));
    } else if (event.key === "ArrowUp") {
      await runWithErrors(() => moveEpisode(-1));
    } else if (event.key.toLowerCase() === "r") {
      const target = event.target;
      const editing = ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
      if (editing) {
        return;
      }
      event.preventDefault();
      await runWithErrors(() => saveLabel("reject"));
    } else if (event.key.toLowerCase() === "p") {
      const target = event.target;
      const editing = ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
      if (editing) {
        return;
      }
      event.preventDefault();
      await runWithErrors(() => saveLabel("pending"));
    } else if (event.key.toLowerCase() === "a") {
      const target = event.target;
      const editing = ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
      if (editing) {
        return;
      }
      event.preventDefault();
      await runWithErrors(() => saveLabel("accept"));
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
    drawHeadVideoCanvas();
    drawQuickVideoCanvases();
    drawGripperCurves();
    resizeTrajectoryPlot();
  });
}

async function runWithErrors(fn) {
  try {
    await fn();
  } catch (error) {
    setSaveState(error.message || String(error), true);
  }
}

function animationLoop(now = 0) {
  if (!state.lastPlaybackUiAt || now - state.lastPlaybackUiAt >= 83) {
    state.lastPlaybackUiAt = now;
    syncVideoTimes(false);
    drawGripperCurves();
    updateTrajectoryHighlight(false);
  }
  window.requestAnimationFrame(animationLoop);
}

async function main() {
  initElements();
  renderIssueOptions();
  await loadUserSession();
  bindEvents();
  drawCanvasMessage(el.leftGripperCanvas, "等待轨迹数据");
  drawCanvasMessage(el.rightGripperCanvas, "等待轨迹数据");
  await loadTrajectoryRenderer();
  animationLoop();
  await runWithErrors(() => loadEpisodes({ keepSelection: false }));
  startSyncLoop();
}

main();
