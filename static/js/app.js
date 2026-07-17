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
    conflict: {
      columns: [],
      records: [],
      filters: {},
      conflictCount: 0,
      fixCount: null,
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
    ["startCapacityBtn", "startPhysicalBtn", "startLoweffBtn", "startZeroFlowBtn", "checkConflictBtn", "fixConflictBtn"]
      .forEach((id) => {
        const el = $(id);
        if (el) el.disabled = busy;
      });
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
      }
    } catch (err) {
      setBusy(false);
      clearInterval(state.pollTimer);
      state.pollTimer = null;
      appendLog([`轮询失败: ${err.message}`]);
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
      const job = await api(path, { method: "POST" });
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

  function switchTab(name) {
    document.querySelectorAll(".tab").forEach((t) => {
      t.classList.toggle("active", t.dataset.tab === name);
    });
    document.querySelectorAll(".tab-panel").forEach((p) => {
      p.classList.toggle("active", p.id === `panel-${name}`);
    });
    if (name === "cog") loadCog();
    if (name === "loweff") loadLoweff().catch(() => {});
    if (name === "zeroflow") loadZeroFlow().catch(() => {});
    if (name === "physquery") loadPhysQuery({ resetOffset: false }).catch(() => {});
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

  async function loadZeroFlow() {
    const sheet = $("zeroFlowSheet").value;
    const keyword = $("zeroFlowKeyword").value.trim();
    const risk = $("zeroFlowRisk").value;
    const status = $("zeroFlowStatus").value;
    const qs = new URLSearchParams({ sheet, keyword, risk, status, limit: "500" });
    const data = await api(`/api/zero-low-flow/view?${qs}`);
    const thead = $("zeroFlowTable").querySelector("thead");
    const tbody = $("zeroFlowTable").querySelector("tbody");
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
          if (c === "风险等级" && v) {
            td.dataset.risk = String(v);
          }
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
    }

    const shown = data.records.length;
    const meta = $("zeroFlowMeta");
    if (meta) {
      meta.textContent = `文件 ${data.file} · 共 ${data.total} 条，当前显示 ${shown} 条`;
    }
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
      ["CGI", "共站同覆盖名", "物理站名", "小区名称", "使用频段"].forEach((k) => {
        const td = document.createElement("td");
        td.textContent = row[k] == null ? "" : String(row[k]);
        tr.appendChild(td);
      });
      const actions = document.createElement("td");
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
    $("startCapacityBtn").onclick = () => startJob("/api/jobs/capacity");
    $("startPhysicalBtn").onclick = () => startJob("/api/jobs/physical");
    $("startLoweffBtn").onclick = () => startJob("/api/jobs/loweff");
    $("startZeroFlowBtn").onclick = () => startJob("/api/jobs/zero-low-flow", { reloadZeroFlow: true });
    $("loadZeroFlowBtn").onclick = () => loadZeroFlow().catch((e) => alert(e.message));
    $("zeroFlowSheet").onchange = () => loadZeroFlow().catch(() => {});
    $("zeroFlowRisk").onchange = () => loadZeroFlow().catch(() => {});
    $("zeroFlowStatus").onchange = () => loadZeroFlow().catch(() => {});
    $("zeroFlowKeyword").onkeydown = (e) => {
      if (e.key === "Enter") loadZeroFlow().catch((err) => alert(err.message));
    };
    $("loadLoweffBtn").onclick = () => loadLoweff().catch((e) => alert(e.message));
    $("loweffSheet").onchange = () => loadLoweff().catch(() => {});
    if ($("loweffType")) $("loweffType").onchange = () => loadLoweff().catch(() => {});
    if ($("loweffBand")) $("loweffBand").onchange = () => loadLoweff().catch(() => {});
    $("loweffKeyword").onkeydown = (e) => {
      if (e.key === "Enter") loadLoweff().catch((err) => alert(err.message));
    };

    $("checkConflictBtn").onclick = () =>
      startJob("/api/jobs/physical/conflicts/check", { clearConflicts: true });

    $("fixConflictBtn").onclick = async () => {
      if (!confirm("确认自动修正扇区冲突？")) return;
      startJob("/api/jobs/physical/conflicts/fix", { clearConflicts: true });
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

  async function init() {
    bindEvents();
    setProgress(0, "就绪");
    try {
      const current = await api("/api/jobs/current");
      if (current.job && (current.job.status === "running" || current.job.status === "pending")) {
        state.jobId = current.job.id;
        setBusy(true);
        state.pollTimer = setInterval(pollJob, 1000);
        await pollJob();
      } else if (
        current.job &&
        current.job.status === "success" &&
        current.job.result_data &&
        current.job.result_data.conflict_count !== undefined
      ) {
        renderConflictTable(current.job.result_data);
      }
      await refreshStatus();
    } catch (err) {
      appendLog([`初始化失败: ${err.message}`]);
    }
  }

  init();
})();
