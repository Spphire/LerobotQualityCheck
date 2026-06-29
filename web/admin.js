const DEFAULT_DATASET = "/mnt/nm_dataset/dataset/giftbox_0628_1912episodes";
const params = new URLSearchParams(window.location.search);
const state = {
  dataset: params.get("dataset") || window.localStorage.getItem("lqcp.dataset") || DEFAULT_DATASET,
  token: params.get("token") || window.localStorage.getItem("lqcp.token") || "",
  reviewStatus: params.get("status") || "all",
  reviewPage: 1,
  reviewPageSize: 40,
  reviewTotal: 0,
  reviewEpisodes: [],
  selectedEpisode: null,
};

const el = {};
[
  "datasetPath",
  "refreshButton",
  "totalCount",
  "markedCount",
  "acceptCount",
  "rejectCount",
  "pendingCount",
  "updatedAt",
  "usersBody",
  "activeCount",
  "activeBody",
  "recentCount",
  "recentBody",
  "reviewStatusFilter",
  "reviewPrevButton",
  "reviewNextButton",
  "reviewPageInfo",
  "reviewEpisodeList",
  "reviewSelectedStatus",
  "reviewSelectedTitle",
  "reviewSelectedMeta",
  "reviewRejectButton",
  "reviewPendingButton",
  "reviewAcceptButton",
  "reviewMessage",
].forEach((id) => {
  el[id] = document.getElementById(id);
});

function apiUrl(path, extra = {}) {
  const next = new URLSearchParams();
  next.set("dataset", state.dataset);
  next.set("user", "admin");
  if (state.token) {
    next.set("token", state.token);
  }
  Object.entries(extra).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      next.set(key, value);
    }
  });
  return `${path}?${next.toString()}`;
}

async function requestJson(path, options = {}) {
  const response = await fetch(path, {
    ...options,
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

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
}

function formatNumber(value) {
  return new Intl.NumberFormat("zh-CN").format(Number(value || 0));
}

function formatTime(value) {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("zh-CN", { hour12: false });
}

function statusText(status) {
  return { accept: "接收", reject: "拒绝", pending: "待审" }[status] || status || "-";
}

function normalizeStatus(status) {
  return status === "accept" || status === "reject" ? status : "pending";
}

function episodeName(index) {
  return `episode_${String(index).padStart(6, "0")}`;
}

function emptyRow(colspan, text) {
  return `<tr><td class="empty-row" colspan="${colspan}">${escapeHtml(text)}</td></tr>`;
}

function renderMetrics(counts = {}) {
  el.totalCount.textContent = formatNumber(counts.total);
  el.markedCount.textContent = formatNumber(counts.marked);
  el.acceptCount.textContent = formatNumber(counts.accept);
  el.rejectCount.textContent = formatNumber(counts.reject);
  el.pendingCount.textContent = formatNumber(counts.pending);
}

function renderUsers(users = []) {
  const sorted = [...users].sort((a, b) => {
    const markedDiff = (b.counts?.marked || 0) - (a.counts?.marked || 0);
    return markedDiff || String(a.user).localeCompare(String(b.user));
  });
  if (!sorted.length) {
    el.usersBody.innerHTML = emptyRow(7, "暂无用户标注");
    return;
  }
  el.usersBody.innerHTML = sorted.map((item) => {
    const counts = item.counts || {};
    return `
      <tr>
        <td>${escapeHtml(item.user)}</td>
        <td>${formatNumber(counts.marked)}</td>
        <td class="status-text accept">${formatNumber(counts.accept)}</td>
        <td class="status-text reject">${formatNumber(counts.reject)}</td>
        <td class="status-text pending">${formatNumber(counts.pending)}</td>
        <td>${formatNumber(item.event_count)}</td>
        <td>${escapeHtml(formatTime(item.last_event_at))}</td>
      </tr>
    `;
  }).join("");
}

function renderActive(active = []) {
  el.activeCount.textContent = formatNumber(active.length);
  if (!active.length) {
    el.activeBody.innerHTML = emptyRow(2, "暂无占用");
    return;
  }
  el.activeBody.innerHTML = active.map((item) => `
    <tr>
      <td>${escapeHtml(item.episode_name || `episode_${String(item.episode_index).padStart(6, "0")}`)}</td>
      <td>${escapeHtml((item.users || []).join(", "))}</td>
    </tr>
  `).join("");
}

function renderReviewPager() {
  const pageCount = Math.max(1, Math.ceil(state.reviewTotal / state.reviewPageSize));
  el.reviewPageInfo.textContent = `${state.reviewPage} / ${pageCount}`;
  el.reviewPrevButton.disabled = state.reviewPage <= 1;
  el.reviewNextButton.disabled = state.reviewPage >= pageCount;
}

function renderReviewList() {
  if (!state.reviewEpisodes.length) {
    el.reviewEpisodeList.innerHTML = `<div class="empty-row">暂无匹配 episode</div>`;
    return;
  }
  el.reviewEpisodeList.innerHTML = state.reviewEpisodes.map((episode) => {
    const status = normalizeStatus(episode.status);
    const active = state.selectedEpisode?.episode_index === episode.episode_index ? "active" : "";
    const task = episode.task_description || episode.task_annotation || (episode.tasks || []).join(" / ");
    const locks = Array.isArray(episode.locked_by) ? episode.locked_by.filter(Boolean) : [];
    return `
      <button class="admin-episode-item ${active}" data-index="${episode.episode_index}" type="button">
        <span>
          <span class="admin-episode-name">${escapeHtml(episode.episode_name || episodeName(episode.episode_index))}</span>
          <span class="admin-episode-task">${escapeHtml(task || "-")}</span>
          <span class="admin-episode-meta">${formatNumber(episode.length)} frames · ${formatNumber(episode.video_count)} videos</span>
        </span>
        <span class="admin-episode-side">
          <span class="status-pill ${status}">${escapeHtml(statusText(status))}</span>
          ${locks.length ? `<span class="admin-lock-note">锁 ${escapeHtml(locks.join(", "))}</span>` : ""}
        </span>
      </button>
    `;
  }).join("");
}

function renderReviewDetail() {
  const episode = state.selectedEpisode;
  if (!episode) {
    el.reviewSelectedStatus.className = "status-pill pending";
    el.reviewSelectedStatus.textContent = "待审";
    el.reviewSelectedTitle.textContent = "选择一个 episode";
    el.reviewSelectedMeta.textContent = "";
    [el.reviewRejectButton, el.reviewPendingButton, el.reviewAcceptButton].forEach((button) => {
      button.disabled = true;
      button.classList.remove("active");
    });
    return;
  }
  const status = normalizeStatus(episode.status);
  const task = episode.task_description || episode.task_annotation || (episode.tasks || []).join(" / ");
  const locks = Array.isArray(episode.locked_by) && episode.locked_by.length
    ? ` · 当前占用 ${episode.locked_by.join(", ")}`
    : "";
  el.reviewSelectedStatus.className = `status-pill ${status}`;
  el.reviewSelectedStatus.textContent = statusText(status);
  el.reviewSelectedTitle.textContent = episode.episode_name || episodeName(episode.episode_index);
  el.reviewSelectedMeta.textContent = `index ${episode.episode_index} · ${formatNumber(episode.length)} frames${locks}${task ? ` · ${task}` : ""}`;
  [
    ["reject", el.reviewRejectButton],
    ["pending", el.reviewPendingButton],
    ["accept", el.reviewAcceptButton],
  ].forEach(([buttonStatus, button]) => {
    button.disabled = false;
    button.classList.toggle("active", status === buttonStatus);
  });
}

function renderRecent(labels = []) {
  el.recentCount.textContent = formatNumber(labels.length);
  if (!labels.length) {
    el.recentBody.innerHTML = emptyRow(4, "暂无标注");
    return;
  }
  el.recentBody.innerHTML = labels.slice(0, 80).map((label) => `
    <tr>
      <td>${escapeHtml(label.episode_name || `episode_${String(label.episode_index).padStart(6, "0")}`)}</td>
      <td class="status-text ${escapeHtml(label.status)}">${escapeHtml(statusText(label.status))}</td>
      <td>${escapeHtml(label.user || label.annotator || "-")}</td>
      <td>${escapeHtml(formatTime(label.updated_at))}</td>
    </tr>
  `).join("");
}

async function loadAdmin() {
  const data = await requestJson(apiUrl("/api/admin"));
  state.dataset = data.dataset_path || state.dataset;
  window.localStorage.setItem("lqcp.dataset", state.dataset);
  el.datasetPath.textContent = `${data.dataset_id} · ${data.dataset_path}`;
  el.updatedAt.textContent = `更新 ${formatTime(data.generated_at)}`;
  renderMetrics(data.counts || {});
  renderUsers(data.users || []);
  renderActive(data.active || []);
  renderRecent(data.recent_labels || []);
}

function selectReviewEpisode(episodeIndex) {
  state.selectedEpisode = state.reviewEpisodes.find((episode) => episode.episode_index === episodeIndex) || null;
  renderReviewList();
  renderReviewDetail();
}

async function loadReviewEpisodes({ keepSelection = true } = {}) {
  const data = await requestJson(apiUrl("/api/episodes", {
    page: state.reviewPage,
    page_size: state.reviewPageSize,
    status: state.reviewStatus !== "all" ? state.reviewStatus : "",
  }));
  state.dataset = data.dataset_path || state.dataset;
  state.reviewTotal = data.total || 0;
  state.reviewEpisodes = data.episodes || [];
  const previousIndex = state.selectedEpisode?.episode_index;
  if (keepSelection && previousIndex !== undefined) {
    state.selectedEpisode = state.reviewEpisodes.find((episode) => episode.episode_index === previousIndex) || null;
  }
  if (!state.selectedEpisode) {
    state.selectedEpisode = state.reviewEpisodes[0] || null;
  }
  renderReviewPager();
  renderReviewList();
  renderReviewDetail();
}

async function refreshAll({ keepSelection = true } = {}) {
  await loadAdmin();
  await loadReviewEpisodes({ keepSelection });
}

async function saveReviewStatus(status) {
  const episode = state.selectedEpisode;
  if (!episode) {
    return;
  }
  el.reviewMessage.textContent = "保存中";
  el.reviewMessage.style.color = "var(--muted)";
  const result = await requestJson(apiUrl("/api/admin/label"), {
    method: "POST",
    body: JSON.stringify({
      episode_index: episode.episode_index,
      status,
    }),
  });
  const summary = result.summary || {};
  state.selectedEpisode = {
    ...episode,
    ...summary,
    status: normalizeStatus(result.label?.status || summary.status || status),
  };
  el.reviewMessage.textContent = `已保存 ${state.selectedEpisode.episode_name || episodeName(episode.episode_index)}`;
  renderReviewDetail();
  await refreshAll({ keepSelection: true });
}

function scheduleRefresh() {
  window.setInterval(() => {
    refreshAll({ keepSelection: true }).catch((error) => {
      el.updatedAt.textContent = error.message || String(error);
    });
  }, 10000);
}

el.refreshButton.addEventListener("click", () => {
  refreshAll({ keepSelection: true }).catch((error) => {
    el.updatedAt.textContent = error.message || String(error);
  });
});

el.reviewStatusFilter.value = state.reviewStatus;
el.reviewStatusFilter.addEventListener("change", () => {
  state.reviewStatus = el.reviewStatusFilter.value;
  state.reviewPage = 1;
  el.reviewMessage.textContent = "";
  refreshAll({ keepSelection: false }).catch((error) => {
    el.reviewMessage.textContent = error.message || String(error);
    el.reviewMessage.style.color = "var(--red)";
  });
});

el.reviewPrevButton.addEventListener("click", () => {
  if (state.reviewPage <= 1) {
    return;
  }
  state.reviewPage -= 1;
  el.reviewMessage.textContent = "";
  loadReviewEpisodes({ keepSelection: false }).catch((error) => {
    el.reviewMessage.textContent = error.message || String(error);
    el.reviewMessage.style.color = "var(--red)";
  });
});

el.reviewNextButton.addEventListener("click", () => {
  const pageCount = Math.max(1, Math.ceil(state.reviewTotal / state.reviewPageSize));
  if (state.reviewPage >= pageCount) {
    return;
  }
  state.reviewPage += 1;
  el.reviewMessage.textContent = "";
  loadReviewEpisodes({ keepSelection: false }).catch((error) => {
    el.reviewMessage.textContent = error.message || String(error);
    el.reviewMessage.style.color = "var(--red)";
  });
});

el.reviewEpisodeList.addEventListener("click", (event) => {
  const button = event.target.closest(".admin-episode-item");
  if (!button) {
    return;
  }
  selectReviewEpisode(Number(button.dataset.index));
});

[
  ["reject", el.reviewRejectButton],
  ["pending", el.reviewPendingButton],
  ["accept", el.reviewAcceptButton],
].forEach(([status, button]) => {
  button.addEventListener("click", () => {
    saveReviewStatus(status).catch((error) => {
      el.reviewMessage.textContent = error.message || String(error);
      el.reviewMessage.style.color = "var(--red)";
    });
  });
});

refreshAll({ keepSelection: false }).then(scheduleRefresh).catch((error) => {
  el.updatedAt.textContent = error.message || String(error);
});
