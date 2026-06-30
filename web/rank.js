const params = new URLSearchParams(window.location.search);
const state = {
  token: params.get("token") || window.localStorage.getItem("lqcp.token") || "",
  user: params.get("user") || window.localStorage.getItem("lqcp.user") || "admin",
  expandedCollector: "",
  collectorItems: [],
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
  "unlabeledSummary",
  "unlabeledEpisodeList",
  "markedUpdatedAt",
  "rejectRateUpdatedAt",
  "collectorUpdatedAt",
  "markedRankBody",
  "rejectRateRankBody",
  "collectorRejectRateRankBody",
].forEach((id) => {
  el[id] = document.getElementById(id);
});

function apiUrl(path) {
  const next = new URLSearchParams();
  next.set("user", state.user);
  if (state.token) {
    next.set("token", state.token);
  }
  return `${path}?${next.toString()}`;
}

function contextUrl(path) {
  const next = new URLSearchParams();
  next.set("user", state.user);
  if (state.token) {
    next.set("token", state.token);
  }
  return `${path}?${next.toString()}`;
}

function reviewUrl(episodeIndex) {
  const next = new URLSearchParams();
  next.set("episode_index", String(episodeIndex));
  next.set("user", state.user);
  if (state.token) {
    next.set("token", state.token);
  }
  return `/?${next.toString()}`;
}

function syncNavigationLinks() {
  document.querySelectorAll("[data-context-link]").forEach((link) => {
    const path = link.getAttribute("data-context-link");
    if (path) {
      link.href = contextUrl(path);
    }
  });
}

async function requestJson(path) {
  const response = await fetch(path, {
    headers: state.token ? { "X-LQCP-Token": state.token } : {},
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

function formatPercent(value) {
  return `${(Number(value || 0) * 100).toFixed(1)}%`;
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

function rankBadge(index) {
  const className = index < 3 ? `rank-medal rank-${index + 1}` : "rank-medal";
  return `<span class="${className}">${index + 1}</span>`;
}

function renderMarkedRank(items = []) {
  if (!items.length) {
    el.markedRankBody.innerHTML = emptyRow(7, "暂无用户标注");
    return;
  }
  el.markedRankBody.innerHTML = items.map((item, index) => `
    <tr>
      <td>${rankBadge(index)}</td>
      <td>${escapeHtml(item.user)}</td>
      <td><strong>${formatNumber(item.marked)}</strong></td>
      <td class="status-text accept">${formatNumber(item.accept)}</td>
      <td class="status-text reject">${formatNumber(item.reject)}</td>
      <td>${formatPercent(item.reject_rate)}</td>
      <td>${escapeHtml(formatTime(item.last_event_at))}</td>
    </tr>
  `).join("");
}

function renderRejectRateRank(items = []) {
  if (!items.length) {
    el.rejectRateRankBody.innerHTML = emptyRow(7, "暂无用户标注");
    return;
  }
  el.rejectRateRankBody.innerHTML = items.map((item, index) => `
    <tr>
      <td>${rankBadge(index)}</td>
      <td>${escapeHtml(item.user)}</td>
      <td><strong>${formatPercent(item.reject_rate)}</strong></td>
      <td class="status-text reject">${formatNumber(item.reject)}</td>
      <td>${formatNumber(item.marked)}</td>
      <td class="status-text accept">${formatNumber(item.accept)}</td>
      <td>${escapeHtml(formatTime(item.last_event_at))}</td>
    </tr>
  `).join("");
}

function renderCollectorRejectRateRank(items = []) {
  state.collectorItems = items;
  if (!items.length) {
    el.collectorRejectRateRankBody.innerHTML = emptyRow(7, "暂无采集人数据");
    return;
  }
  el.collectorRejectRateRankBody.innerHTML = items.map((item, index) => {
    const collector = item.collector || "";
    const expanded = state.expandedCollector === collector;
    const episodes = item.rejected_episodes || [];
    const detailRows = expanded
      ? `
        <tr class="collector-detail-row">
          <td colspan="7">
            <div class="collector-detail">
              ${episodes.length ? episodes.map((episode) => `
                <a class="collector-episode-link" href="${reviewUrl(episode.episode_index)}">
                  <span>${escapeHtml(episode.episode_name || `episode_${String(episode.episode_index).padStart(6, "0")}`)}</span>
                  <span>index ${formatNumber(episode.episode_index)}</span>
                  <span>${escapeHtml(episode.user || "-")}</span>
                  <span>${escapeHtml(formatTime(episode.updated_at))}</span>
                </a>
              `).join("") : '<div class="collector-detail-empty">暂无拒绝 episode</div>'}
            </div>
          </td>
        </tr>
      `
      : "";
    return `
    <tr>
      <td>${rankBadge(index)}</td>
      <td>
        <button class="collector-toggle" type="button" data-index="${index}" aria-expanded="${expanded ? "true" : "false"}">
          ${escapeHtml(collector)}
        </button>
      </td>
      <td><strong>${formatPercent(item.reject_rate)}</strong></td>
      <td class="status-text reject">${formatNumber(item.reject)}</td>
      <td>${formatNumber(item.marked)}</td>
      <td class="status-text accept">${formatNumber(item.accept)}</td>
      <td class="status-text pending">${formatNumber(item.pending)}</td>
    </tr>
    ${detailRows}
  `;
  }).join("");
}

function renderUnlabeledEpisodes(items = [], total = items.length) {
  el.unlabeledSummary.textContent = `${formatNumber(total)} 条未标注`;
  if (!items.length) {
    el.unlabeledEpisodeList.innerHTML = '<div class="unlabeled-complete">当前没有未标注 episode</div>';
    return;
  }
  el.unlabeledEpisodeList.innerHTML = items.map((episode) => `
    <a class="unlabeled-chip" href="${reviewUrl(episode.episode_index)}" title="${escapeHtml(episode.episode_uuid || "")}">
      <strong>${escapeHtml(episode.episode_name || `episode_${String(episode.episode_index).padStart(6, "0")}`)}</strong>
      <span>index ${formatNumber(episode.episode_index)}</span>
    </a>
  `).join("");
}

async function loadRank() {
  const data = await requestJson(apiUrl("/api/rank"));
  syncNavigationLinks();
  const collectorCache = data.collector_cache || {};
  const cacheText = collectorCache.total
    ? `采集人缓存 ${formatNumber(collectorCache.known)}/${formatNumber(collectorCache.total)}，队列 ${formatNumber(collectorCache.queued)}`
    : "采集人缓存待建立";
  el.datasetPath.textContent = `${data.dataset_id} / ${data.dataset_path} / ${cacheText}`;
  const updatedText = `更新 ${formatTime(data.generated_at)}`;
  el.markedUpdatedAt.textContent = updatedText;
  el.rejectRateUpdatedAt.textContent = updatedText;
  el.collectorUpdatedAt.textContent = updatedText;
  renderMetrics(data.counts || {});
  renderUnlabeledEpisodes(data.unlabeled_episodes || [], data.unlabeled_count || 0);
  renderMarkedRank(data.rankings?.marked || []);
  renderRejectRateRank(data.rankings?.reject_rate || []);
  renderCollectorRejectRateRank(data.rankings?.collector_reject_rate || []);
}

function scheduleRefresh() {
  window.setInterval(() => {
    loadRank().catch((error) => {
      el.markedUpdatedAt.textContent = error.message || String(error);
      el.rejectRateUpdatedAt.textContent = error.message || String(error);
      el.collectorUpdatedAt.textContent = error.message || String(error);
    });
  }, 10000);
}

el.refreshButton.addEventListener("click", () => {
  loadRank().catch((error) => {
    el.markedUpdatedAt.textContent = error.message || String(error);
    el.rejectRateUpdatedAt.textContent = error.message || String(error);
    el.collectorUpdatedAt.textContent = error.message || String(error);
  });
});

el.collectorRejectRateRankBody.addEventListener("click", (event) => {
  const button = event.target.closest(".collector-toggle");
  if (!button) {
    return;
  }
  const item = state.collectorItems[Number(button.dataset.index)];
  const collector = item?.collector || "";
  state.expandedCollector = state.expandedCollector === collector ? "" : collector;
  renderCollectorRejectRateRank(state.collectorItems);
});

syncNavigationLinks();
loadRank().then(scheduleRefresh).catch((error) => {
  el.markedUpdatedAt.textContent = error.message || String(error);
  el.rejectRateUpdatedAt.textContent = error.message || String(error);
  el.collectorUpdatedAt.textContent = error.message || String(error);
});
