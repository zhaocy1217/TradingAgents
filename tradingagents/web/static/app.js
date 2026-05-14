async function requestJson(url, options = {}) {
  const resp = await fetch(url, options);
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(text || `HTTP ${resp.status}`);
  }
  return resp.json();
}

function el(id) {
  return document.getElementById(id);
}

let activeJobId = null;

function setBusy(active, stage = "", message = "") {
  const overlay = el("busyOverlay");
  if (!overlay) return;
  if (active) {
    document.body.classList.add("app-busy");
    overlay.classList.remove("hidden");
    el("busyStage").textContent = stage || "后台处理中";
    el("busyMessage").textContent = message || "请稍候，分析期间已锁定页面操作。";
  } else {
    document.body.classList.remove("app-busy");
    overlay.classList.add("hidden");
    activeJobId = null;
  }
}

async function pollJob(jobId, onUpdate) {
  while (true) {
    const job = await requestJson(`/api/analyze/jobs/${jobId}`);
    onUpdate(job);
    if (job.status === "completed" || job.status === "failed") {
      return job;
    }
    await new Promise((resolve) => setTimeout(resolve, 1500));
  }
}

function renderItems(containerId, items, renderFn) {
  const container = el(containerId);
  container.innerHTML = "";
  if (!items || items.length === 0) {
    container.innerHTML = '<div class="list-item">暂无数据</div>';
    return;
  }
  for (const item of items) {
    const row = document.createElement("div");
    row.className = "list-item";
    row.innerHTML = renderFn(item);
    container.appendChild(row);
  }
}

async function loadFavorites() {
  const data = await requestJson("/api/favorites");
  renderItems("favoritesList", data.items, (item) => {
    return `
      <div><strong>${item.symbol}</strong> ${item.company_name} ${item.note || ""}</div>
      <button data-symbol="${item.symbol}" class="remove-fav-btn">删除</button>
    `;
  });
  document.querySelectorAll(".remove-fav-btn").forEach((btn) => {
    btn.onclick = async () => {
      await requestJson(`/api/favorites/${btn.dataset.symbol}`, { method: "DELETE" });
      await loadFavorites();
    };
  });
}

async function searchSymbols() {
  const q = el("symbolQuery").value.trim();
  if (!q) return;
  const data = await requestJson(`/api/symbols/search?q=${encodeURIComponent(q)}`);
  renderItems("symbolResults", data.items, (item) => {
    return `
      <div>
        <strong>${item.symbol}</strong> ${item.company_name}
        <small>[${item.source}]</small>
      </div>
      <div>
        <button class="pick-symbol-btn" data-symbol="${item.symbol}" data-name="${item.company_name}">使用</button>
        <button class="add-fav-btn" data-symbol="${item.symbol}" data-name="${item.company_name}">收藏</button>
      </div>
    `;
  });
  document.querySelectorAll(".pick-symbol-btn").forEach((btn) => {
    btn.onclick = () => {
      el("symbolInput").value = btn.dataset.symbol;
    };
  });
  document.querySelectorAll(".add-fav-btn").forEach((btn) => {
    btn.onclick = async () => {
      await requestJson("/api/favorites", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          symbol: btn.dataset.symbol,
          company_name: btn.dataset.name,
          note: "",
        }),
      });
      await loadFavorites();
    };
  });
}

async function analyzeOne(event) {
  event.preventDefault();
  const payload = {
    symbol: el("symbolInput").value.trim() || null,
    analysis_date: el("analysisDateInput").value.trim() || null,
  };
  setBusy(true, "提交任务", "正在创建分析任务...");
  const submitted = await requestJson("/api/analyze/submit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  activeJobId = submitted.job_id;
  const result = await pollJob(submitted.job_id, (job) => {
    el("analyzeResult").textContent = JSON.stringify(job, null, 2);
    el("busyStage").textContent = `阶段: ${job.stage || "running"}`;
    el("busyMessage").textContent = job.message || "后台分析中，请勿操作页面。";
  });
  setBusy(false);
  if (result.status === "failed") {
    throw new Error(result.error || "分析失败");
  }
  el("analyzeResult").textContent = JSON.stringify(result.result, null, 2);
  await loadReports();
}

async function analyzeTop(event) {
  event.preventDefault();
  const payload = {
    top_n: Number(el("topNInput").value || 10),
    analysis_date: el("topDateInput").value.trim() || null,
  };
  setBusy(true, "提交Top任务", "正在创建批量分析任务...");
  const submitted = await requestJson("/api/analyze/top/submit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  activeJobId = submitted.job_id;
  const result = await pollJob(submitted.job_id, (job) => {
    el("topAnalyzeResult").textContent = JSON.stringify(job, null, 2);
    el("busyStage").textContent = `阶段: ${job.stage || "running"}`;
    el("busyMessage").textContent = job.message || "后台批量分析中，请勿操作页面。";
  });
  setBusy(false);
  if (result.status === "failed") {
    throw new Error(result.error || "批量分析失败");
  }
  el("topAnalyzeResult").textContent = JSON.stringify(result.result, null, 2);
  await loadReports();
}

let hotChart;
function drawHotChart(items) {
  const grouped = {};
  for (const item of items) {
    const key = item.rank_date;
    grouped[key] = (grouped[key] || 0) + Number(item.hot_score || 0);
  }
  const labels = Object.keys(grouped).sort();
  const values = labels.map((d) => grouped[d]);

  const ctx = el("hotChart");
  if (!ctx) return;
  if (hotChart) hotChart.destroy();
  hotChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [
        {
          label: "每日热度总分",
          data: values,
        },
      ],
    },
  });
}

async function loadHot() {
  const data = await requestJson("/api/hot/week");
  renderItems("hotList", data.items.slice(0, 30), (item) => {
    return `<div><strong>${item.symbol}</strong> ${item.company_name} - ${item.hot_score.toFixed(2)} (${item.rank_date})</div>`;
  });
  drawHotChart(data.items);
}

async function refreshHot() {
  await requestJson("/api/hot/refresh", { method: "POST" });
  await loadHot();
}

async function loadReports() {
  const q = (el("reportQuery")?.value || "").trim();
  const data = await requestJson(`/api/reports?query=${encodeURIComponent(q)}`);
  renderItems("reportList", data.items, (item) => {
    return `
      <div>
        <strong>${item.symbol}</strong> ${item.company_name}
        <small>${item.analysis_date} / ${item.signal}</small>
      </div>
      <a href="/reports/${item.id}" target="_blank">打开</a>
    `;
  });
}

function wireEvents() {
  el("searchBtn").onclick = () => searchSymbols().catch((e) => alert(e.message));
  el("refreshFavBtn").onclick = () => loadFavorites().catch((e) => alert(e.message));
  el("refreshHotBtn").onclick = () => refreshHot().catch((e) => alert(e.message));
  el("searchReportBtn").onclick = () => loadReports().catch((e) => alert(e.message));
  el("analyzeForm").onsubmit = (e) =>
    analyzeOne(e).catch((err) => {
      setBusy(false);
      alert(err.message);
    });
  el("topAnalyzeForm").onsubmit = (e) =>
    analyzeTop(e).catch((err) => {
      setBusy(false);
      alert(err.message);
    });
  el("cancelJobBtn").onclick = () => {
    if (!activeJobId) return;
    requestJson(`/api/analyze/jobs/${activeJobId}/cancel`, { method: "POST" })
      .then((resp) => {
        const job = resp.job || {};
        el("busyStage").textContent = `阶段: ${job.stage || "cancelling"}`;
        el("busyMessage").textContent = job.message || "取消请求已发送";
      })
      .catch((e) => alert(e.message));
  };
}

async function bootstrap() {
  wireEvents();
  await Promise.all([loadFavorites(), loadHot(), loadReports()]);
}

bootstrap().catch((err) => {
  // eslint-disable-next-line no-console
  console.error(err);
});
