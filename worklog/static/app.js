/* Boot, filters, KPIs, live refresh and URL state. */
window.Worklog = window.Worklog || {};

(function (W) {
  const POLL_MS = 10000;
  const FILTERS = [
    ["campaign", "Campaign"],
    ["modelFamily", "Model group"],
    ["task", "Task"],
    ["family", "Benchmark"],
    ["language", "Language"],
  ];
  const GROUP_ORDER = ["baseline", "melt", "smurf", "other"];
  const TASK_LABELS = { asr: "ASR", st: "ST", audio_mcq: "QA" };

  let snapshot = null;
  let lastRefresh = null;
  const state = { metric: {} };

  function readHash() {
    const params = new URLSearchParams(location.hash.replace(/^#/, ""));
    for (const [key] of FILTERS) state[key] = params.get(key) || "";
    state.heatmap = params.get("heatmap") || "";
    state.metric = {};
    state.common = {};
    for (const [key, value] of params.entries()) {
      if (key.startsWith("metric-") && value) state.metric[key.slice("metric-".length)] = value;
      if (key.startsWith("common-") && value === "1") state.common[key.slice("common-".length)] = true;
    }
    state.showAllCheckpoints = params.get("showAll") === "1";
  }

  function writeHash() {
    const params = new URLSearchParams();
    for (const [key] of FILTERS) if (state[key]) params.set(key, state[key]);
    for (const [task, name] of Object.entries(state.metric || {})) {
      if (name) params.set(`metric-${task}`, name);
    }
    for (const [task, on] of Object.entries(state.common || {})) {
      if (on) params.set(`common-${task}`, "1");
    }
    if (state.showAllCheckpoints) params.set("showAll", "1");
    if (state.heatmap) params.set("heatmap", state.heatmap);
    const hash = params.toString();
    history.replaceState(null, "", hash ? `#${hash}` : location.pathname + location.search);
  }

  function option(value, label, selected) {
    return `<option value="${W.escape(value)}"${value === selected ? " selected" : ""}>${W.escape(label)}</option>`;
  }

  function choices(key) {
    if (!snapshot) return [];
    if (key === "campaign") return snapshot.filters.campaigns;
    if (key === "modelFamily") {
      const present = new Set(Object.values(snapshot.model_families));
      return GROUP_ORDER.filter((kind) => present.has(kind)).map((kind) => [kind, W.familyLabel(kind)]);
    }
    if (key === "task") return Object.keys(snapshot.matrix).map((task) => [task, TASK_LABELS[task] || task]);
    if (key === "family") return snapshot.filters.families;
    if (key === "language") return snapshot.filters.languages;
    return [];
  }

  function buildFilters() {
    const box = document.getElementById("filters");
    box.innerHTML = "";
    for (const [key, label] of FILTERS) {
      const values = choices(key).map((item) => (Array.isArray(item) ? item : [item, item]));
      if (!values.length) continue;
      const field = document.createElement("div");
      field.className = "field";
      const id = `filter-${key}`;
      field.innerHTML = `<label for="${id}">${label}</label>
        <select id="${id}">${option("", key === "family" ? "Auto" : "All", state[key])}${values
          .map(([value, text]) => option(value, text, state[key]))
          .join("")}</select>`;
      field.querySelector("select").addEventListener("change", (event) => {
        state[key] = event.target.value;
        if (key === "task") state.family = "";
        writeHash();
        renderAll();
      });
      box.appendChild(field);
    }
    const reset = document.createElement("button");
    reset.className = "btn ghost";
    reset.type = "button";
    reset.textContent = "Reset";
    reset.addEventListener("click", () => {
      for (const [key] of FILTERS) state[key] = "";
      state.metric = {};
      state.common = {};
      state.showAllCheckpoints = false;
      state.heatmap = "";
      writeHash();
      buildFilters();
      renderAll();
    });
    box.appendChild(reset);
  }

  function kpi(label, value, cls) {
    return `<div class="kpi"><div class="label">${W.escape(label)}</div><div class="value ${cls || ""}">${W.escape(value)}</div></div>`;
  }

  function renderKpis() {
    const k = snapshot.kpis;
    document.getElementById("kpis").innerHTML = [
      kpi("Models", k.models),
      kpi("Slices", k.slices, "good"),
      kpi("Pending metrics", k.pending, k.pending ? "warn" : ""),
      kpi("Failed runs", k.failed, k.failed ? "bad" : ""),
      kpi("Benchmark families", k.families),
      kpi("Source warnings", k.warnings, k.warnings ? "warn" : ""),
    ].join("");
  }

  function renderStatus() {
    const el = document.getElementById("statusline");
    const time = lastRefresh ? lastRefresh.toLocaleTimeString() : "—";
    const warnings = snapshot ? snapshot.kpis.warnings : 0;
    el.textContent = `updated ${time} · ${snapshot ? snapshot.kpis.slices : 0} slices${warnings ? ` · ${warnings} warning(s)` : ""}`;
  }

  function renderAll() {
    if (!snapshot) return;
    const showAll = document.getElementById("show-all-checkpoints");
    if (showAll) showAll.checked = !!state.showAllCheckpoints;
    renderKpis();
    buildFilters();
    renderStatus();
    W.matrix.render(snapshot, state);
    W.charts.render(snapshot, state);
    W.feed.render(snapshot);
  }

  async function load(version) {
    const url = version ? `/api/snapshot?version=${encodeURIComponent(version)}` : "/api/snapshot";
    const response = await fetch(url, { cache: "no-store" });
    if (response.status === 401) {
      W.loginRedirect();
      return false;
    }
    if (response.status === 204) return false;
    if (!response.ok) throw new Error(`snapshot: HTTP ${response.status}`);
    snapshot = await response.json();
    state.aggregate = snapshot.aggregate || "macro";
    lastRefresh = new Date();
    renderAll();
    return true;
  }

  async function poll() {
    if (!snapshot) return;
    try {
      await load(snapshot.version);
    } catch (error) {
      document.getElementById("statusline").textContent = `refresh failed: ${error.message}`;
    }
  }

  async function refreshNow() {
    const button = document.getElementById("refresh");
    button.disabled = true;
    button.textContent = "Scanning…";
    try {
      await fetch("/api/refresh", { method: "POST" });
      await load();
    } catch (error) {
      document.getElementById("statusline").textContent = `refresh failed: ${error.message}`;
    } finally {
      button.disabled = false;
      button.textContent = "Refresh";
    }
  }

  async function boot() {
    W.theme.init();
    window.addEventListener("worklog:theme", () => {
      if (snapshot) renderAll();
    });
    readHash();
    try {
      await load();
    } catch (error) {
      document.getElementById("matrix").innerHTML = `<div class="placeholder">Could not load results: ${W.escape(error.message)}</div>`;
      return;
    }
    document.getElementById("refresh").addEventListener("click", refreshNow);
    document.getElementById("show-all-checkpoints").addEventListener("change", (event) => {
      state.showAllCheckpoints = event.target.checked;
      writeHash();
      renderAll();
    });
    setInterval(poll, POLL_MS);
  }

  W.app = {
    setMetric(task, name) {
      state.metric[task] = name;
      writeHash();
      renderAll();
    },
    setCommon(task, on) {
      if (!state.common) state.common = {};
      state.common[task] = !!on;
      writeHash();
      renderAll();
    },
    setHeatmapFamily(family) {
      state.heatmap = family;
      writeHash();
      if (snapshot) W.charts.render(snapshot, state);
    },
  };

  document.addEventListener("DOMContentLoaded", boot);
})(window.Worklog);
