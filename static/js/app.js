(() => {
  const state = {
    jobId: null,
    pollTimer: null,
    cogEditingCgi: null,
    physQuery: {
      source: "raw",
      offset: 0,
      limit: 200,
      total: 0,
      metaLoaded: false,
    },
    capResults: {
      offset: 0,
      limit: 200,
      total: 0,
    },
    conflict: {
      columns: [],
      records: [],
      filters: {},
      conflictCount: 0,
      fixCount: null,
    },
    multiweek: {
      weeks: [],
      selected: new Set(),
    },
    zeroFlow: {
      columns: [],
      records: [],
      filters: {},
      total: 0,
      file: "",
    },
  };

  const $ = (id) => document.getElementById(id);

  async function api(path, options = {}) {
    const res = await fetch(path, options);
    const contentType = res.headers.get("content-type") || "";
    let body = null;
    if (contentType.includes("application/json")) {
      body = await res.json();
    } else {
      body = await res.text();
    }
    if (!res.ok) {
      const detail = body && body.detail ? body.detail : body || res.statusText;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    // Unwrap SuccessResponse / ErrorResponse envelopes from FastAPI routers
    if (body && typeof body === "object" && "ok" in body) {
      if (body.ok === true && "data" in body) {
        return body.data;
      }
      if (body.ok === false && body.error) {
        const err = body.error;
        throw new Error(err.detail || err.message || JSON.stringify(err));
      }
    }
    return body;
  }

  function setProgress(pct, message) {
    $("progressFill").style.width = `${pct}%`;
    $("progressPct").textContent = `${pct}%`;
    $("progressLabel").textContent = message || "就绪";
  }

  function appendLog(lines) {
    const box = $("logBox");
    if (!Array.isArray(lines) || !lines.length) return;
    const existing = box.textContent ? box.textContent.split("\n") : [];
    const merged = existing.concat(lines.filter((l) => !existing.includes(l)));
    box.textContent = merged.slice(-800).join("\n");
    box.scrollTop = box.scrollHeight;
  }

  function setBusy(busy) {
    ["startCapacityBtn", "startPhysicalBtn", "startNrmSyncBtn", "startLoweffBtn", "startZeroFlowBtn", "startZeroFlow5gBtn", "checkConflictBtn", "fixConflictBtn",
     "multiweekStartBtn", "multiweekRefreshBtn"]
      .forEach((id) => {
        const el = $(id);
        if (el) el.disabled = busy;
      });
    const mwFileInput = $("multiweekFileInput");
    if (mwFileInput) mwFileInput.disabled = busy;
  }

  function renderResultLinks(files) {
    const box = $("resultLinks");
    box.innerHTML = "";
    (files || []).forEach((name) => {
      const a = document.createElement("a");
      a.href = `/api/data/outputs/${encodeURIComponent(name)}`;
      a.textContent = `下载 ${name}`;
      a.download = name;
      box.appendChild(a);
    });
  }

  function updateConflictMeta(visibleCount) {
    const meta = $("conflictMeta");
    if (!meta) return;
    const total = state.conflict.conflictCount || 0;
    const loaded = (state.conflict.records || []).length;
    const fixCount = state.conflict.fixCount;
    let metaText;
    if (!total) {
      metaText = "未发现扇区冲突";
    } else {
      metaText = `发现 ${total} 条扇区冲突，已加载 ${loaded} 条`;
      if (visibleCount != null && visibleCount !== loaded) {
        metaText += `，筛选后 ${visibleCount} 条`;
      }
    }
    if (fixCount != null && fixCount > 0) {
      metaText += `；已修正 ${fixCount} 条`;
    }
    meta.textContent = metaText;
  }

  const CONFLICT_ENUM_COLUMNS = new Set(["站点类型"]);

  function uniqueColumnValues(column) {
    const seen = new Set();
    const values = [];
    (state.conflict.records || []).forEach((row) => {
      const raw = row[column];
      const text = raw == null ? "" : String(raw).trim();
      if (!text || seen.has(text)) return;
      seen.add(text);
      values.push(text);
    });
    return values.sort((a, b) => a.localeCompare(b, "zh-CN"));
  }

  function getFilteredConflictRecords() {
    const { records, filters } = state.conflict;
    const active = Object.entries(filters || {}).filter(([, v]) => v && String(v).trim());
    if (!active.length) return records || [];
    return (records || []).filter((row) =>
      active.every(([col, raw]) => {
        const needle = String(raw).trim();
        const value = row[col];
        const hay = value == null ? "" : String(value);
        if (CONFLICT_ENUM_COLUMNS.has(col)) {
          return hay === needle;
        }
        return hay.toLowerCase().includes(needle.toLowerCase());
      })
    );
  }

  function renderConflictBody() {
    const table = $("conflictTable");
    if (!table) return;
    const tbody = table.querySelector("tbody");
    tbody.innerHTML = "";
    const columns = state.conflict.columns || [];
    const filtered = getFilteredConflictRecords();
    updateConflictMeta(filtered.length);

    if (!columns.length) return;

    if (!filtered.length) {
      const tr = document.createElement("tr");
      tr.className = "empty-row";
      const td = document.createElement("td");
      td.colSpan = Math.max(columns.length, 1);
      td.textContent = (state.conflict.records || []).length
        ? "无匹配记录，请调整表头筛选"
        : "暂无冲突明细";
      tr.appendChild(td);
      tbody.appendChild(tr);
      return;
    }

    filtered.forEach((row) => {
      const tr = document.createElement("tr");
      columns.forEach((c) => {
        const td = document.createElement("td");
        const v = row[c];
        td.textContent = v == null ? "" : String(v);
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
  }

  function renderConflictTable(resultData) {
    const section = $("conflictSection");
    const table = $("conflictTable");
    if (!section || !table) return;

    if (!resultData || resultData.conflict_count === undefined) {
      section.classList.add("hidden");
      state.conflict = {
        columns: [],
        records: [],
        filters: {},
        conflictCount: 0,
        fixCount: null,
      };
      return;
    }

    section.classList.remove("hidden");
    state.conflict.columns = resultData.columns || [];
    state.conflict.records = resultData.records || [];
    state.conflict.filters = {};
    state.conflict.conflictCount = resultData.conflict_count || 0;
    state.conflict.fixCount =
      resultData.fix_count != null ? resultData.fix_count : null;

    const thead = table.querySelector("thead");
    thead.innerHTML = "";

    const columns = state.conflict.columns;
    if (!columns.length) {
      renderConflictBody();
      return;
    }

    const trLabel = document.createElement("tr");
    columns.forEach((c) => {
      const th = document.createElement("th");
      th.textContent = c;
      trLabel.appendChild(th);
    });
    thead.appendChild(trLabel);

    const trFilter = document.createElement("tr");
    trFilter.className = "filter-row";
    columns.forEach((c) => {
      const th = document.createElement("th");
      const applyFilter = (val) => {
        if (val && String(val).trim()) {
          state.conflict.filters[c] = val;
        } else {
          delete state.conflict.filters[c];
        }
        renderConflictBody();
      };

      if (CONFLICT_ENUM_COLUMNS.has(c)) {
        const select = document.createElement("select");
        select.className = "th-filter th-filter-select";
        select.setAttribute("aria-label", `筛选 ${c}`);
        select.dataset.column = c;

        const allOpt = document.createElement("option");
        allOpt.value = "";
        allOpt.textContent = "全部";
        select.appendChild(allOpt);

        uniqueColumnValues(c).forEach((v) => {
          const opt = document.createElement("option");
          opt.value = v;
          opt.textContent = v;
          select.appendChild(opt);
        });

        select.addEventListener("change", () => applyFilter(select.value));
        th.appendChild(select);
      } else {
        const input = document.createElement("input");
        input.type = "search";
        input.className = "th-filter";
        input.placeholder = "筛选";
        input.setAttribute("aria-label", `筛选 ${c}`);
        input.dataset.column = c;
        input.addEventListener("input", () => applyFilter(input.value));
        th.appendChild(input);
      }
      trFilter.appendChild(th);
    });
    thead.appendChild(trFilter);

    renderConflictBody();
  }

  function renderStatusList(el, items) {
    el.innerHTML = "";
    items.forEach((item) => {
      const li = document.createElement("li");
      const dot = document.createElement("span");
      dot.className = "status-dot " + (item.found ? "ok" : item.optional ? "warn" : "bad");
      const label = document.createElement("span");
      label.className = "label";
      label.textContent = item.label || item.key;
      const value = document.createElement("span");
      value.className = "value " + (item.found ? "ok" : item.optional ? "warn" : "bad");
      if (item.found) {
        value.textContent = item.count > 1 ? `已找到 ${item.count} 个` : "已找到";
      } else {
        value.textContent = item.optional ? "未找到(可选)" : "未找到";
      }
      li.appendChild(dot);
      li.appendChild(label);
      li.appendChild(value);
      el.appendChild(li);
    });
  }

  async function refreshStatus() {
    // 若 HTMX 可用，触发其局部刷新（由 #statusPanel 的 hx-trigger 监听 statusChanged）
    if (window.htmx && document.getElementById("statusPanel")) {
      document.body.dispatchEvent(new CustomEvent("statusChanged"));
      return;
    }
    // 离线/HTMX 未加载时的回退：JSON + 手动重建 DOM
    const status = await api("/api/data/status");
    renderStatusList($("capacityStatus"), status.capacity);
    renderStatusList($("physicalStatus"), status.physical);

    const outputs = await api("/api/data/outputs");
    const list = $("outputList");
    list.innerHTML = "";
    (outputs.files || []).slice(0, 12).forEach((f) => {
      const li = document.createElement("li");
      const icon = document.createElement("span");
      icon.className = "status-dot ok";
      const label = document.createElement("span");
      label.className = "label";
      label.textContent = f.name;
      const link = document.createElement("a");
      link.href = `/api/data/outputs/${encodeURIComponent(f.name)}`;
      link.textContent = "下载";
      link.download = f.name;
      li.appendChild(icon);
      li.appendChild(label);
      li.appendChild(link);
      list.appendChild(li);
    });
  }

  async function pollJob() {
    if (!state.jobId) return;
    try {
      const job = await api(`/api/jobs/${state.jobId}`);
      setProgress(job.progress || 0, job.message || job.status);
      appendLog(job.logs || []);
      renderResultLinks(job.result_files || []);
      if (job.result_data && job.result_data.conflict_count !== undefined) {
        renderConflictTable(job.result_data);
      }
      if (job.status === "running" || job.status === "pending") {
        setBusy(true);
        return;
      }
      setBusy(false);
      clearInterval(state.pollTimer);
      state.pollTimer = null;
      const finishedType = job.kind || job.type || job.name || "";
      state.jobId = null;
      await refreshStatus();
      if (job.status === "failed") {
        setProgress(job.progress || 0, job.error || "失败");
      } else if (job.status === "success") {
        const files = job.result_files || [];
        const isLoweff =
          finishedType === "loweff" ||
          files.some((f) => String(f).includes("低效小区结果"));
        const isZeroFlow =
          finishedType === "zero_low_flow" ||
          files.some((f) => String(f).includes("零低流量风险小区"));
        if (isLoweff) {
          try {
            await loadLoweff();
          } catch (err) {
            appendLog([`自动加载低效结果失败: ${err.message}`]);
          }
        }
        if (isZeroFlow) {
          try {
            await loadZeroFlow();
          } catch (err) {
            appendLog([`自动加载零低流量结果失败: ${err.message}`]);
          }
        }
        // After capacity job, refresh the persisted results summary
        if (finishedType === "capacity") {
          try {
            await loadCapresSummary();
          } catch (err) {
            appendLog([`刷新合成结果摘要失败: ${err.message}`]);
          }
        }
      }
    } catch (err) {
      setBusy(false);
      clearInterval(state.pollTimer);
      state.pollTimer = null;
      appendLog([`轮询失败: ${err.message}`]);
    }
  }

  async function startZeroFlowWithFiles(fileList, network) {
    if (!fileList || !fileList.length) return;
    const formData = new FormData();
    for (const f of fileList) formData.append("files", f);
    const label = network === "5g" ? "5G" : "4G";
    try {
      setBusy(true);
      $("logBox").textContent = "";
      setProgress(0, `导入 ${fileList.length} 个${label}文件...`);
      renderResultLinks([]);
      // Step 1: upload files to data/
      const uploadRes = await api("/api/data/upload", { method: "POST", body: formData });
      appendLog([`已上传 ${uploadRes.count} 个文件: ${(uploadRes.saved || []).join(", ")}`]);
      // Step 2: start zero-low-flow job with uploaded file paths
      setProgress(10, `启动${label}零低流量分析...`);
      const job = await api("/api/jobs/start/zero-low-flow", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_paths: uploadRes.saved, network: network || "4g" }),
      });
      state.jobId = job.id;
      if (state.pollTimer) clearInterval(state.pollTimer);
      state.pollTimer = setInterval(pollJob, 1000);
      await pollJob();
    } catch (err) {
      setBusy(false);
      appendLog([`启动失败: ${err.message}`]);
      setProgress(0, "启动失败");
    } finally {
      const inputId = network === "5g" ? "zeroFlow5gFileInput" : "zeroFlowFileInput";
      $(inputId).value = "";
    }
  }

  async function startJob(path, options = {}) {
    try {
      setBusy(true);
      $("logBox").textContent = "";
      setProgress(0, "任务启动中...");
      renderResultLinks([]);
      if (options.clearConflicts) {
        renderConflictTable(null);
      }
      const fetchOpts = { method: "POST" };
      if (options.body) {
        fetchOpts.headers = { "Content-Type": "application/json" };
        fetchOpts.body = JSON.stringify(options.body);
      }
      const job = await api(path, fetchOpts);
      state.jobId = job.id;
      if (state.pollTimer) clearInterval(state.pollTimer);
      state.pollTimer = setInterval(pollJob, 1000);
      await pollJob();
    } catch (err) {
      setBusy(false);
      appendLog([`启动失败: ${err.message}`]);
      setProgress(0, "启动失败");
    }
  }

  function renderCapresKpi(summary) {
    const box = $("capresKpi");
    if (!box) return;
    box.innerHTML = "";
    if (!summary || !summary.tables) return;
    summary.tables.forEach((t) => {
      const card = document.createElement("div");
      card.className = "kpi-card";
      const label = document.createElement("div");
      label.className = "kpi-label";
      label.textContent = t.label;
      const value = document.createElement("div");
      value.className = "kpi-value";
      value.textContent = t.row_count > 0 ? `${t.row_count} 条` : "暂无数据";
      card.appendChild(label);
      card.appendChild(value);
      box.appendChild(card);
    });
  }

  async function loadCapresSummary() {
    try {
      const data = await api("/api/capacity-results/summary");
      renderCapresKpi(data);
    } catch (err) {
      appendLog([`加载容量表摘要失败: ${err.message}`]);
    }
  }

  async function loadCapresults(options = {}) {
    const resetOffset = options.resetOffset !== false;
    if (resetOffset) state.capResults.offset = 0;

    const table = $("capresTable").value;
    const keyword = $("capresKeyword").value.trim();
    const qs = new URLSearchParams({
      table,
      keyword,
      limit: String(state.capResults.limit),
      offset: String(state.capResults.offset),
    });
    const data = await api(`/api/capacity-results/view?${qs}`);
    state.capResults.total = data.total || 0;

    const thead = $("capresTable_el").querySelector("thead");
    const tbody = $("capresTable_el").querySelector("tbody");
    thead.innerHTML = "";
    tbody.innerHTML = "";

    const trh = document.createElement("tr");
    (data.columns || []).forEach((c) => {
      const th = document.createElement("th");
      th.textContent = c;
      trh.appendChild(th);
    });
    thead.appendChild(trh);

    if (!data.records.length) {
      const tr = document.createElement("tr");
      tr.className = "empty-row";
      const td = document.createElement("td");
      td.colSpan = Math.max((data.columns || []).length, 1);
      td.textContent = "暂无合成结果，请先运行容量表合成";
      tr.appendChild(td);
      tbody.appendChild(tr);
    } else {
      data.records.forEach((row) => {
        const tr = document.createElement("tr");
        data.columns.forEach((c) => {
          const td = document.createElement("td");
          const v = row[c];
          td.textContent = v == null ? "" : String(v);
          if (c === "CGI" || c === "NCGI") td.className = "mono";
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
    }

    const shown = data.records.length;
    const from = data.total ? state.capResults.offset + 1 : 0;
    const to = state.capResults.offset + shown;
    const meta = $("capresMeta");
    if (meta) {
      meta.textContent = `${data.label} · 共 ${data.total} 条，当前显示 ${from}-${to}`;
    }
    const pageInfo = $("capresPageInfo");
    if (pageInfo) {
      const page = Math.floor(state.capResults.offset / state.capResults.limit) + 1;
      const pages = Math.max(1, Math.ceil(state.capResults.total / state.capResults.limit));
      pageInfo.textContent = `第 ${page} / ${pages} 页`;
    }
    if ($("capresPrevBtn")) $("capresPrevBtn").disabled = state.capResults.offset <= 0;
    if ($("capresNextBtn")) {
      $("capresNextBtn").disabled =
        state.capResults.offset + state.capResults.limit >= state.capResults.total;
    }
  }

  function switchTab(name) {
    document.querySelectorAll(".tab").forEach((t) => {
      t.classList.toggle("active", t.dataset.tab === name);
    });
    document.querySelectorAll(".tab-panel").forEach((p) => {
      p.classList.toggle("active", p.id === `panel-${name}`);
    });
    if (name === "capresults") {
      loadCapresSummary().catch(() => {});
      loadCapresults({ resetOffset: false }).catch(() => {});
    }
    if (name === "cog") loadCog();
    if (name === "loweff") loadLoweff().catch(() => {});
    if (name === "zeroflow") loadZeroFlow().catch(() => {});
    if (name === "physquery") loadPhysQuery({ resetOffset: false }).catch(() => {});
    if (name === "multiweek") loadWeekFiles().catch(() => {});
  }



  function fillSelect(el, values, allLabel) {
    if (!el) return;
    const prev = el.value;
    el.innerHTML = "";
    const all = document.createElement("option");
    all.value = "";
    all.textContent = allLabel;
    el.appendChild(all);
    (values || []).forEach((v) => {
      const opt = document.createElement("option");
      opt.value = v;
      opt.textContent = v;
      el.appendChild(opt);
    });
    if (prev && [...el.options].some((o) => o.value === prev)) {
      el.value = prev;
    }
  }

  function syncPhysQueryAggFilters(source) {
    const isAgg = source === "agg";
    ["pqRegion", "pqCoverLayer", "pqCoSite"].forEach((id) => {
      const el = $(id);
      if (!el) return;
      el.classList.toggle("hidden", !isAgg);
      if (!isAgg) el.value = "";
    });
  }

  async function loadPhysQueryMeta(force = false) {
    const source = $("pqSource").value;
    if (!force && state.physQuery.metaLoaded && state.physQuery.source === source) {
      return;
    }
    const data = await api(`/api/physical-query/meta?source=${encodeURIComponent(source)}`);
    fillSelect($("pqNet"), data.filters["网络制式"] || [], "全部制式");
    fillSelect($("pqBand"), data.filters["BAND"] || [], "全部频段");
    fillSelect($("pqVendor"), data.filters["厂家"] || [], "全部厂家");
    fillSelect($("pqSiteType"), data.filters["站点类型"] || [], "全部站点类型");
    fillSelect($("pqStatus"), data.filters["网元状态"] || [], "全部网元状态");
    fillSelect($("pqCoverType"), data.filters["覆盖类型"] || [], "全部覆盖类型");
    fillSelect($("pqGrid"), data.filters["路测网格"] || [], "全部路测网格");
    fillSelect($("pqRegion"), data.filters["区域"] || [], "全部区域");
    fillSelect($("pqCoverLayer"), data.filters["覆盖层"] || [], "全部覆盖层");
    fillSelect($("pqCoSite"), data.filters["共站制式情况"] || [], "全部共站制式");
    syncPhysQueryAggFilters(source);
    state.physQuery.source = source;
    state.physQuery.metaLoaded = true;
    const meta = $("pqMeta");
    if (meta && !state.physQuery.total) {
      meta.textContent = `表 ${data.table} · 共 ${data.total} 条`;
    }
  }

  async function loadPhysQuery(options = {}) {
    const resetOffset = options.resetOffset !== false;
    if (resetOffset) state.physQuery.offset = 0;

    await loadPhysQueryMeta(options.forceMeta === true);

    const source = $("pqSource").value;
    const qs = new URLSearchParams({
      source,
      keyword: $("pqKeyword").value.trim(),
      net: $("pqNet").value,
      band: $("pqBand").value,
      vendor: $("pqVendor").value,
      site_type: $("pqSiteType").value,
      status: $("pqStatus").value,
      cover_type: $("pqCoverType").value,
      grid: $("pqGrid").value,
      region: $("pqRegion") ? $("pqRegion").value : "",
      cover_layer: $("pqCoverLayer") ? $("pqCoverLayer").value : "",
      co_site: $("pqCoSite") ? $("pqCoSite").value : "",
      limit: String(state.physQuery.limit),
      offset: String(state.physQuery.offset),
    });

    const data = await api(`/api/physical-query/view?${qs}`);
    state.physQuery.total = data.total || 0;
    state.physQuery.source = source;

    const thead = $("pqTable").querySelector("thead");
    const tbody = $("pqTable").querySelector("tbody");
    thead.innerHTML = "";
    tbody.innerHTML = "";

    const trh = document.createElement("tr");
    (data.columns || []).forEach((c) => {
      const th = document.createElement("th");
      th.textContent = c;
      trh.appendChild(th);
    });
    thead.appendChild(trh);

    if (!data.records.length) {
      const tr = document.createElement("tr");
      tr.className = "empty-row";
      const td = document.createElement("td");
      td.colSpan = Math.max((data.columns || []).length, 1);
      td.textContent = "无匹配记录";
      tr.appendChild(td);
      tbody.appendChild(tr);
    } else {
      data.records.forEach((row) => {
        const tr = document.createElement("tr");
        data.columns.forEach((c) => {
          const td = document.createElement("td");
          const v = row[c];
          td.textContent = v == null ? "" : String(v);
          if (c === "CGI") td.className = "mono";
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
    }

    const shown = data.records.length;
    const from = state.physQuery.total ? state.physQuery.offset + 1 : 0;
    const to = state.physQuery.offset + shown;
    const meta = $("pqMeta");
    if (meta) {
      meta.textContent = `表 ${data.table} · 共 ${data.total} 条，当前显示 ${from}-${to}`;
    }
    const pageInfo = $("pqPageInfo");
    if (pageInfo) {
      const page = Math.floor(state.physQuery.offset / state.physQuery.limit) + 1;
      const pages = Math.max(1, Math.ceil(state.physQuery.total / state.physQuery.limit));
      pageInfo.textContent = `第 ${page} / ${pages} 页`;
    }
    if ($("pqPrevBtn")) $("pqPrevBtn").disabled = state.physQuery.offset <= 0;
    if ($("pqNextBtn")) {
      $("pqNextBtn").disabled =
        state.physQuery.offset + state.physQuery.limit >= state.physQuery.total;
    }
  }

  function getZeroFlowFilteredRecords() {
    const { records, filters } = state.zeroFlow;
    const active = Object.entries(filters || {}).filter(([, v]) => v && String(v).trim());
    if (!active.length) return records || [];
    return (records || []).filter((row) =>
      active.every(([col, raw]) => {
        const needle = String(raw).trim().toLowerCase();
        const value = row[col];
        const hay = value == null ? "" : String(value);
        return hay.toLowerCase().includes(needle);
      })
    );
  }

  function renderZeroFlowBody() {
    const table = $("zeroFlowTable");
    if (!table) return;
    const tbody = table.querySelector("tbody");
    tbody.innerHTML = "";
    const columns = state.zeroFlow.columns || [];
    const filtered = getZeroFlowFilteredRecords();

    if (!columns.length) return;

    if (!filtered.length) {
      const tr = document.createElement("tr");
      tr.className = "empty-row";
      const td = document.createElement("td");
      td.colSpan = Math.max(columns.length, 1);
      td.textContent = (state.zeroFlow.records || []).length
        ? "无匹配记录，请调整表头筛选"
        : "暂无数据";
      tr.appendChild(td);
      tbody.appendChild(tr);
      return;
    }

    filtered.forEach((row) => {
      const tr = document.createElement("tr");
      columns.forEach((c) => {
        const td = document.createElement("td");
        const v = row[c];
        td.textContent = v == null ? "" : String(v);
        if (c === "风险等级" && v) {
          td.dataset.risk = String(v);
        }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
  }

  function renderZeroFlowTable(data) {
    const table = $("zeroFlowTable");
    if (!table) return;
    const thead = table.querySelector("thead");

    state.zeroFlow.columns = data.columns || [];
    state.zeroFlow.records = data.records || [];
    state.zeroFlow.filters = {};
    state.zeroFlow.total = data.total || 0;
    state.zeroFlow.file = data.file || "";

    thead.innerHTML = "";
    const columns = state.zeroFlow.columns;

    const trLabel = document.createElement("tr");
    columns.forEach((c) => {
      const th = document.createElement("th");
      th.textContent = c;
      trLabel.appendChild(th);
    });
    thead.appendChild(trLabel);

    const trFilter = document.createElement("tr");
    trFilter.className = "filter-row";
    columns.forEach((c) => {
      const th = document.createElement("th");
      const input = document.createElement("input");
      input.type = "search";
      input.className = "th-filter";
      input.placeholder = "筛选";
      input.setAttribute("aria-label", `筛选 ${c}`);
      input.dataset.column = c;
      input.addEventListener("input", () => {
        const val = input.value;
        if (val && val.trim()) {
          state.zeroFlow.filters[c] = val;
        } else {
          delete state.zeroFlow.filters[c];
        }
        renderZeroFlowBody();
        updateZeroFlowMeta();
      });
      th.appendChild(input);
      trFilter.appendChild(th);
    });
    thead.appendChild(trFilter);

    renderZeroFlowBody();
    updateZeroFlowMeta();
  }

  function updateZeroFlowMeta() {
    const meta = $("zeroFlowMeta");
    if (!meta) return;
    const total = state.zeroFlow.total || 0;
    const filtered = getZeroFlowFilteredRecords().length;
    const loaded = (state.zeroFlow.records || []).length;
    const file = state.zeroFlow.file || "";
    let text = `文件 ${file} · 共 ${total} 条，已加载 ${loaded} 条`;
    if (filtered !== loaded) text += `，筛选后 ${filtered} 条`;
    meta.textContent = text;
  }

  async function loadZeroFlow() {
    const sheet = $("zeroFlowSheet").value;
    const keyword = $("zeroFlowKeyword").value.trim();
    const risk = $("zeroFlowRisk").value;
    const status = $("zeroFlowStatus").value;
    const qs = new URLSearchParams({ sheet, keyword, risk, status, limit: "500" });
    const data = await api(`/api/zero-low-flow/view?${qs}`);
    renderZeroFlowTable(data);
  }

  function renderLoweffKpi(summary) {
    const box = $("loweffKpi");
    if (!box) return;
    box.innerHTML = "";
    if (!summary || !Object.keys(summary).length) {
      box.classList.add("hidden");
      return;
    }
    box.classList.remove("hidden");
    const cards = [
      { key: "5G低效数", label: "5G低效数" },
      { key: "5G零效益数", label: "5G零效益" },
      { key: "5G低效益数", label: "5G低效益" },
      { key: "5G低效占比", label: "5G低效占比", format: "pct" },
      { key: "4G低效数", label: "4G低效数" },
      { key: "低效总数", label: "低效总数" },
    ];
    cards.forEach((item) => {
      if (!(item.key in summary)) return;
      const card = document.createElement("div");
      card.className = "kpi-card";
      const label = document.createElement("div");
      label.className = "kpi-label";
      label.textContent = item.label;
      const value = document.createElement("div");
      value.className = "kpi-value";
      let v = summary[item.key];
      if (item.format === "pct" && v != null && v !== "") {
        const n = Number(v);
        v = Number.isFinite(n) ? `${(n * 100).toFixed(2)}%` : v;
      } else if (typeof v === "number") {
        v = Number.isInteger(v) ? String(v) : v.toFixed(2);
      }
      value.textContent = v == null ? "-" : String(v);
      card.appendChild(label);
      card.appendChild(value);
      box.appendChild(card);
    });
  }

  function syncLoweffFilters(sheet) {
    const typeEl = $("loweffType");
    const bandEl = $("loweffBand");
    const is5g = sheet === "5g";
    if (typeEl) {
      typeEl.disabled = !is5g;
      if (!is5g) typeEl.value = "";
    }
    if (bandEl) {
      bandEl.disabled = !is5g;
      if (!is5g) bandEl.value = "";
    }
  }

  async function loadLoweff() {
    const sheet = $("loweffSheet").value;
    const keyword = $("loweffKeyword").value.trim();
    const lowType = $("loweffType") ? $("loweffType").value : "";
    const band = $("loweffBand") ? $("loweffBand").value : "";
    syncLoweffFilters(sheet);
    const qs = new URLSearchParams({ sheet, keyword, limit: "500" });
    if (lowType) qs.set("low_type", lowType);
    if (band) qs.set("band", band);
    const data = await api(`/api/loweff/view?${qs}`);
    renderLoweffKpi(data.summary || {});

    const download = $("loweffDownloadLink");
    if (download) {
      download.classList.remove("hidden");
      download.href = `/api/data/download/${encodeURIComponent(data.file || "低效小区结果.xlsx")}`;
    }

    const thead = $("loweffTable").querySelector("thead");
    const tbody = $("loweffTable").querySelector("tbody");
    thead.innerHTML = "";
    tbody.innerHTML = "";
    const trh = document.createElement("tr");
    data.columns.forEach((c) => {
      const th = document.createElement("th");
      th.textContent = c;
      trh.appendChild(th);
    });
    thead.appendChild(trh);

    if (!data.records.length) {
      const tr = document.createElement("tr");
      tr.className = "empty-row";
      const td = document.createElement("td");
      td.colSpan = Math.max(data.columns.length, 1);
      td.textContent = "无匹配记录";
      tr.appendChild(td);
      tbody.appendChild(tr);
    } else {
      data.records.forEach((row) => {
        const tr = document.createElement("tr");
        data.columns.forEach((c) => {
          const td = document.createElement("td");
          const v = row[c];
          td.textContent = v == null ? "" : String(v);
          if (c === "低效类型" && v) {
            td.dataset.loweffType = String(v);
          }
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
    }

    const shown = data.records.length;
    const meta = $("loweffMeta");
    if (meta) {
      const sheetLabel = {
        summary: "统计汇总",
        "5g": "5G低效明细",
        "4g": "4G低效明细",
        "4g_all": "全量4G小区评估",
      }[sheet] || sheet;
      meta.textContent = `文件 ${data.file} · ${sheetLabel} · 共 ${data.total} 条，当前显示 ${shown} 条`;
    }
  }

  async function loadCog(q = "") {
    const qs = new URLSearchParams({ limit: "200", offset: "0" });
    if (q) qs.set("q", q);
    const data = await api(`/api/cog?${qs}`);
    const tbody = $("cogTable").querySelector("tbody");
    tbody.innerHTML = "";
    data.records.forEach((row) => {
      const tr = document.createElement("tr");
      const isActive = row.is_active !== false && row.is_active !== 0;
      if (!isActive) tr.classList.add("row-inactive");
      ["CGI", "共站同覆盖名", "物理站名", "小区名称", "使用频段"].forEach((k) => {
        const td = document.createElement("td");
        td.textContent = row[k] == null ? "" : String(row[k]);
        if (!isActive) td.classList.add("text-muted");
        tr.appendChild(td);
      });
      // 状态列
      const statusTd = document.createElement("td");
      const badge = document.createElement("span");
      badge.className = isActive ? "badge badge-active" : "badge badge-inactive";
      badge.textContent = isActive ? "激活" : "已停用";
      statusTd.appendChild(badge);
      tr.appendChild(statusTd);
      // 操作列
      const actions = document.createElement("td");
      actions.className = "actions-cell";
      const toggleBtn = document.createElement("button");
      toggleBtn.className = isActive ? "btn btn-toggle-off" : "btn btn-toggle-on";
      toggleBtn.type = "button";
      toggleBtn.textContent = isActive ? "去激活" : "激活";
      toggleBtn.onclick = async () => {
        try {
          await api(`/api/cog/active`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ cgi: row.CGI, active: !isActive }),
          });
          await loadCog($("cogSearch").value.trim());
        } catch (err) {
          alert(err.message);
        }
      };
      const editBtn = document.createElement("button");
      editBtn.className = "btn text";
      editBtn.type = "button";
      editBtn.textContent = "编辑";
      editBtn.style.color = "var(--primary)";
      editBtn.onclick = () => openCogDialog(row);
      const delBtn = document.createElement("button");
      delBtn.className = "btn danger-text";
      delBtn.type = "button";
      delBtn.textContent = "删除";
      delBtn.onclick = async () => {
        if (!confirm(`确认删除 ${row.CGI} ?`)) return;
        await api(`/api/cog/${encodeURIComponent(row.CGI)}`, { method: "DELETE" });
        await loadCog($("cogSearch").value.trim());
      };
      actions.appendChild(toggleBtn);
      actions.appendChild(editBtn);
      actions.appendChild(delBtn);
      tr.appendChild(actions);
      tbody.appendChild(tr);
    });
    $("cogMeta").textContent = `共 ${data.total} 条，当前显示 ${data.records.length} 条`;
  }

  function openCogDialog(row) {
    const form = $("cogForm");
    state.cogEditingCgi = row ? row.CGI : null;
    $("cogDialogTitle").textContent = row ? "编辑记录" : "新增记录";
    [...form.elements].forEach((el) => {
      if (!el.name) return;
      el.value = row && row[el.name] != null ? row[el.name] : "";
      if (el.name === "CGI") el.readOnly = !!row;
    });
    $("cogDialog").showModal();
  }

  async function saveCog(ev) {
    ev.preventDefault();
    const form = $("cogForm");
    if (form.returnValue === "cancel" || ev.submitter?.value === "cancel") {
      $("cogDialog").close();
      return;
    }
    const payload = {};
    [...form.elements].forEach((el) => {
      if (!el.name) return;
      if (el.value === "") return;
      payload[el.name] = el.type === "number" ? Number(el.value) : el.value;
    });
    try {
      if (state.cogEditingCgi) {
        const { CGI, ...rest } = payload;
        await api(`/api/cog/${encodeURIComponent(state.cogEditingCgi)}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(rest),
        });
      } else {
        await api("/api/cog", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      }
      $("cogDialog").close();
      await loadCog($("cogSearch").value.trim());
    } catch (err) {
      alert(err.message);
    }
  }

  function bindEvents() {
    document.querySelectorAll(".tab").forEach((tab) => {
      tab.addEventListener("click", () => switchTab(tab.dataset.tab));
    });

    $("refreshStatusBtn").onclick = () => refreshStatus().catch((e) => alert(e.message));
    $("clearLogBtn").onclick = () => { $("logBox").textContent = ""; };
    $("startCapacityBtn").onclick = () => startJob("/api/jobs/start/capacity");
    $("startPhysicalBtn").onclick = () => startJob("/api/jobs/start/physical");
    if ($("startNrmSyncBtn")) {
      $("startNrmSyncBtn").onclick = () => startJob("/api/jobs/start/nrm-sync");
    }
    $("startLoweffBtn").onclick = () => startJob("/api/jobs/start/loweff");
    $("startZeroFlowBtn").onclick = () => startJob("/api/jobs/start/zero-low-flow", { reloadZeroFlow: true });
    if ($("startZeroFlow5gBtn")) {
      $("startZeroFlow5gBtn").onclick = () => startJob("/api/jobs/start/zero-low-flow", { body: { network: "5g" } });
    }
    $("zeroFlowFileInput").onchange = (e) => startZeroFlowWithFiles(e.target.files);
    if ($("zeroFlow5gFileInput")) {
      $("zeroFlow5gFileInput").onchange = (e) => startZeroFlow5gWithFiles(e.target.files);
    }
    $("loadZeroFlowBtn").onclick = () => loadZeroFlow().catch((e) => alert(e.message));
    $("zeroFlowSheet").onchange = () => loadZeroFlow().catch(() => {});
    $("zeroFlowRisk").onchange = () => loadZeroFlow().catch(() => {});
    $("zeroFlowStatus").onchange = () => loadZeroFlow().catch(() => {});
    $("zeroFlowKeyword").onkeydown = (e) => {
      if (e.key === "Enter") loadZeroFlow().catch((err) => alert(err.message));
    };
    // Multi-week buttons
    if ($("multiweekRefreshBtn")) {
      $("multiweekRefreshBtn").onclick = () => loadWeekFiles().catch((e) => alert(e.message));
    }
    if ($("multiweekStartBtn")) {
      $("multiweekStartBtn").onclick = () => startMultiWeekLoweff();
    }
    if ($("multiweekFileInput")) {
      $("multiweekFileInput").onchange = (e) => uploadMultiWeekFiles(e.target.files);
    }
    $("loadLoweffBtn").onclick = () => loadLoweff().catch((e) => alert(e.message));
    $("loweffSheet").onchange = () => loadLoweff().catch(() => {});
    if ($("loweffType")) $("loweffType").onchange = () => loadLoweff().catch(() => {});
    if ($("loweffBand")) $("loweffBand").onchange = () => loadLoweff().catch(() => {});
    $("loweffKeyword").onkeydown = (e) => {
      if (e.key === "Enter") loadLoweff().catch((err) => alert(err.message));
    };

    $("checkConflictBtn").onclick = () =>
      startJob("/api/jobs/start/conflicts/check", { clearConflicts: true });

    $("fixConflictBtn").onclick = async () => {
      if (!confirm("确认自动修正扇区冲突？")) return;
      startJob("/api/jobs/start/conflicts/fix", { clearConflicts: true });
    };

    $("uploadInput").onchange = async (e) => {
      const files = [...e.target.files];
      if (!files.length) return;
      const fd = new FormData();
      files.forEach((f) => fd.append("files", f));
      try {
        const res = await api("/api/data/upload", { method: "POST", body: fd });
        appendLog([`已上传 ${res.count} 个文件: ${(res.saved || []).join(", ")}`]);
        await refreshStatus();
      } catch (err) {
        alert(err.message);
      } finally {
        e.target.value = "";
      }
    };


    // Capacity results tab
    $("capresTable").onchange = () => loadCapresults({ resetOffset: true }).catch(() => {});
    $("capresSearchBtn").onclick = () => loadCapresults({ resetOffset: true }).catch((e) => alert(e.message));
    $("capresReloadBtn").onclick = () => {
      loadCapresSummary().catch(() => {});
      loadCapresults({ resetOffset: true }).catch((e) => alert(e.message));
    };
    $("capresKeyword").onkeydown = (e) => {
      if (e.key === "Enter") loadCapresults({ resetOffset: true }).catch((err) => alert(err.message));
    };
    $("capresPrevBtn").onclick = () => {
      state.capResults.offset = Math.max(0, state.capResults.offset - state.capResults.limit);
      loadCapresults({ resetOffset: false }).catch((e) => alert(e.message));
    };
    $("capresNextBtn").onclick = () => {
      if (state.capResults.offset + state.capResults.limit >= state.capResults.total) return;
      state.capResults.offset += state.capResults.limit;
      loadCapresults({ resetOffset: false }).catch((e) => alert(e.message));
    };

    $("pqSearchBtn").onclick = () => loadPhysQuery({ resetOffset: true }).catch((e) => alert(e.message));
    $("pqReloadBtn").onclick = () =>
      loadPhysQuery({ resetOffset: true, forceMeta: true }).catch((e) => alert(e.message));
    $("pqSource").onchange = () => {
      state.physQuery.metaLoaded = false;
      loadPhysQuery({ resetOffset: true, forceMeta: true }).catch((e) => alert(e.message));
    };
    ["pqNet", "pqBand", "pqVendor", "pqSiteType", "pqStatus", "pqCoverType", "pqGrid", "pqRegion", "pqCoverLayer", "pqCoSite"]
      .forEach((id) => {
        const el = $(id);
        if (el) el.onchange = () => loadPhysQuery({ resetOffset: true }).catch(() => {});
      });
    $("pqKeyword").onkeydown = (e) => {
      if (e.key === "Enter") loadPhysQuery({ resetOffset: true }).catch((err) => alert(err.message));
    };
    $("pqPrevBtn").onclick = () => {
      state.physQuery.offset = Math.max(0, state.physQuery.offset - state.physQuery.limit);
      loadPhysQuery({ resetOffset: false }).catch((e) => alert(e.message));
    };
    $("pqNextBtn").onclick = () => {
      if (state.physQuery.offset + state.physQuery.limit >= state.physQuery.total) return;
      state.physQuery.offset += state.physQuery.limit;
      loadPhysQuery({ resetOffset: false }).catch((e) => alert(e.message));
    };

    $("cogSearchBtn").onclick = () => loadCog($("cogSearch").value.trim());
    $("cogReloadBtn").onclick = () => loadCog($("cogSearch").value.trim());
    $("cogAddBtn").onclick = () => openCogDialog(null);
    $("cogForm").onsubmit = saveCog;
    $("cogImportInput").onchange = async (e) => {
      const file = e.target.files[0];
      if (!file) return;
      const fd = new FormData();
      fd.append("file", file);
      try {
        const replace = confirm("是否清空后全量导入？取消则为追加/覆盖同 CGI");
        const res = await api(`/api/cog/import?replace=${replace ? "true" : "false"}`, {
          method: "POST",
          body: fd,
        });
        appendLog([`共站同覆盖导入 ${res.imported} 条`]);
        await loadCog();
      } catch (err) {
        alert(err.message);
      } finally {
        e.target.value = "";
      }
    };
  }

  // ===== 主题切换（浅色 / 深色 / 跟随系统）=====
  const THEME_KEY = "capphys-theme";
  const ICON_SUN =
    '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>';
  const ICON_MOON =
    '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>';

  function getThemePref() {
    const p = localStorage.getItem(THEME_KEY);
    return p === "light" || p === "dark" ? p : "auto";
  }

  function effectiveTheme(pref) {
    if (pref === "light" || pref === "dark") return pref;
    return window.matchMedia &&
      window.matchMedia("(prefers-color-scheme: light)").matches
      ? "light"
      : "dark";
  }

  function applyTheme(pref) {
    const eff = effectiveTheme(pref);
    document.documentElement.setAttribute("data-theme", eff);
    document.documentElement.setAttribute("data-theme-pref", pref);
    const btn = $("themeToggleBtn");
    const menu = $("themeMenu");
    if (btn) btn.innerHTML = eff === "light" ? ICON_SUN : ICON_MOON;
    if (menu) {
      menu.querySelectorAll("li[data-pref]").forEach((li) => {
        const active = li.dataset.pref === pref;
        li.classList.toggle("active", active);
        li.setAttribute("aria-checked", active ? "true" : "false");
      });
    }
  }

  function initThemeSwitcher() {
    applyTheme(getThemePref());

    // 跟随系统：监听系统主题变化
    if (window.matchMedia) {
      const mq = window.matchMedia("(prefers-color-scheme: light)");
      const handler = () => {
        if (getThemePref() === "auto") applyTheme("auto");
      };
      if (mq.addEventListener) mq.addEventListener("change", handler);
      else if (mq.addListener) mq.addListener(handler);
    }

    const btn = $("themeToggleBtn");
    const menu = $("themeMenu");
    if (!btn || !menu) return;

    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const willOpen = menu.hidden;
      menu.hidden = !willOpen;
      btn.setAttribute("aria-expanded", String(willOpen));
    });

    menu.addEventListener("click", (e) => {
      const li = e.target.closest("li[data-pref]");
      if (!li) return;
      const pref = li.dataset.pref;
      localStorage.setItem(THEME_KEY, pref);
      applyTheme(pref);
      menu.hidden = true;
      btn.setAttribute("aria-expanded", "false");
    });

    menu.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !menu.hidden) {
        menu.hidden = true;
        btn.setAttribute("aria-expanded", "false");
        btn.focus();
      }
    });

    // 点击外部关闭
    document.addEventListener("click", (e) => {
      if (menu.hidden) return;
      if (!e.target.closest(".theme-switcher")) {
        menu.hidden = true;
        btn.setAttribute("aria-expanded", "false");
      }
    });
  }

  // ── Multi-week 4G evaluation ──────────────────────────────────────────

  async function loadWeekFiles() {
    const box = $("multiweekWeekList");
    box.innerHTML = '<p class="muted" style="font-size:13px">正在扫描...</p>';
    state.multiweek.weeks = [];
    state.multiweek.selected.clear();

    try {
      const weeks = await api("/api/jobs/multi-week/weeks");
      state.multiweek.weeks = weeks || [];

      if (!weeks || !weeks.length) {
        box.innerHTML = '<p class="muted" style="font-size:13px">未找到可用的「重要场景-周」文件，请先将文件放入 data/ 目录</p>';
        $("multiweekStartBtn").disabled = true;
        return;
      }

      box.innerHTML = "";
      const ul = document.createElement("ul");
      ul.style.cssText = "list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:6px";

      weeks.forEach((w, i) => {
        const li = document.createElement("li");
        li.style.cssText = "display:flex;align-items:center;gap:8px;padding:6px 10px;border:1px solid var(--border);border-radius:6px;cursor:pointer;transition:background .15s";
        li.innerHTML = `<input type="checkbox" id="mw-week-${i}" data-idx="${i}" style="cursor:pointer" />
          <label for="mw-week-${i}" style="cursor:pointer;flex:1">
            <strong>${w.period_label}</strong>
            <span class="muted" style="font-size:12px;margin-left:6px">${w.rel_path}</span>
          </label>`;
        const cb = li.querySelector("input");
        cb.addEventListener("change", () => {
          if (cb.checked) {
            state.multiweek.selected.add(i);
            li.style.background = "var(--primary-bg, rgba(37,99,235,.08))";
          } else {
            state.multiweek.selected.delete(i);
            li.style.background = "";
          }
          $("multiweekStartBtn").disabled = state.multiweek.selected.size < 2;
        });
        ul.appendChild(li);
      });
      box.appendChild(ul);

      const hint = document.createElement("p");
      hint.className = "muted";
      hint.style.cssText = "font-size:12px;margin-top:6px";
      hint.textContent = `共 ${weeks.length} 个周期文件，至少选择 2 个开始评估`;
      box.appendChild(hint);
    } catch (err) {
      box.innerHTML = `<p style="color:var(--danger);font-size:13px">扫描失败: ${err.message}</p>`;
    }
  }

  async function startMultiWeekLoweff() {
    const selected = [...state.multiweek.selected];
    if (selected.length < 2) {
      alert("请至少选择 2 个周期文件");
      return;
    }
    const paths = selected.map((i) => state.multiweek.weeks[i].path);

    try {
      setBusy(true);
      $("logBox").textContent = "";
      setProgress(0, "多周期评估启动中...");
      renderResultLinks([]);

      const job = await api("/api/jobs/start/multi-week-loweff", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ week_file_paths: paths }),
      });
      state.jobId = job.id;
      if (state.pollTimer) clearInterval(state.pollTimer);
      state.pollTimer = setInterval(pollJob, 1000);
      await pollJob();
    } catch (err) {
      setBusy(false);
      appendLog([`启动失败: ${err.message}`]);
      setProgress(0, "启动失败");
    }
  }

  async function uploadMultiWeekFiles(fileList) {
    if (!fileList || !fileList.length) return;
    const formData = new FormData();
    for (const f of fileList) formData.append("files", f);
    try {
      setBusy(true);
      $("logBox").textContent = "";
      setProgress(0, `导入 ${fileList.length} 个周文件...`);
      renderResultLinks([]);
      const uploadRes = await api("/api/data/upload", { method: "POST", body: formData });
      appendLog([`已上传 ${uploadRes.count} 个文件: ${(uploadRes.saved || []).join(", ")}`]);
      setProgress(50, "刷新可用周文件列表...");
      await loadWeekFiles();
      appendLog(["周文件列表已刷新，可勾选后开始评估"]);
      setProgress(100, "导入完成");
    } catch (err) {
      appendLog([`导入失败: ${err.message}`]);
      setProgress(0, "导入失败");
    } finally {
      $("multiweekFileInput").value = "";
      setBusy(false);
    }
  }

  async function init() {
    bindEvents();
    initThemeSwitcher();
    setProgress(0, "就绪");
    try {
      const current = await api("/api/jobs/current");
      if (current && (current.status === "running" || current.status === "pending")) {
        state.jobId = current.id;
        setBusy(true);
        state.pollTimer = setInterval(pollJob, 1000);
        await pollJob();
      } else if (
        current &&
        current.status === "success" &&
        current.result_data &&
        current.result_data.conflict_count !== undefined
      ) {
        renderConflictTable(current.result_data);
      }
      await refreshStatus();
    } catch (err) {
      appendLog([`初始化失败: ${err.message}`]);
    }
  }

  init();
})();
