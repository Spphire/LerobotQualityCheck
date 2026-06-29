const DEFAULT_DATASET = "/mnt/nm_dataset/dataset/giftbox_0628_1912episodes";
const params = new URLSearchParams(window.location.search);
const state = {
  dataset: params.get("dataset") || window.localStorage.getItem("lqcp.dataset") || DEFAULT_DATASET,
  token: params.get("token") || window.localStorage.getItem("lqcp.token") || "",
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
].forEach((id) => {
  el[id] = document.getElementById(id);
});

function apiUrl(path) {
  const next = new URLSearchParams();
  next.set("dataset", state.dataset);
  next.set("user", "admin");
  if (state.token) {
    next.set("token", state.token);
  }
  return `${path}?${next.toString()}`;
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
  const response = await fetch(apiUrl("/api/admin"));
  if (!response.ok) {
    throw new Error(`加载失败 ${response.status}`);
  }
  const data = await response.json();
  state.dataset = data.dataset_path || state.dataset;
  window.localStorage.setItem("lqcp.dataset", state.dataset);
  el.datasetPath.textContent = `${data.dataset_id} · ${data.dataset_path}`;
  el.updatedAt.textContent = `更新 ${formatTime(data.generated_at)}`;
  renderMetrics(data.counts || {});
  renderUsers(data.users || []);
  renderActive(data.active || []);
  renderRecent(data.recent_labels || []);
}

function scheduleRefresh() {
  window.setInterval(() => {
    loadAdmin().catch((error) => {
      el.updatedAt.textContent = error.message || String(error);
    });
  }, 10000);
}

el.refreshButton.addEventListener("click", () => {
  loadAdmin().catch((error) => {
    el.updatedAt.textContent = error.message || String(error);
  });
});

loadAdmin().then(scheduleRefresh).catch((error) => {
  el.updatedAt.textContent = error.message || String(error);
});
