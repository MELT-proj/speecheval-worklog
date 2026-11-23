/* Trends page: one line per training lineage, one panel per benchmark. */
window.Worklog = window.Worklog || {};

(function (W) {
  const TASK_LABELS = { asr: "Speech recognition", st: "Speech translation", audio_mcq: "Multiple-choice QA" };
  let snapshot = null;
  let charts = [];
  const state = { lineages: null, task: "", metrics: {}, x: "step", pinned: "" };

  function isPercent(metric) {
    return (
      metric.includes("wer") ||
      metric.includes("cer") ||
      metric.includes("accuracy") ||
      metric.includes("unresolved")
    );
  }

  function taskLabel(task) {
    return (snapshot.tasks[task] || {}).label || TASK_LABELS[task] || task;
  }

  function metricOf(task) {
    return state.metrics[task] || (snapshot.tasks[task] || {}).metric || "";
  }

  function ruleOf(task, metric) {
    const taskInfo = snapshot.tasks[task] || {};
    return (taskInfo.metrics || []).find((item) => item.name === metric) || { name: metric, label: metric, lower: false };
  }

  function visibleLineages() {
    return snapshot.lineages.filter((lineage) => !state.lineages || state.lineages.includes(lineage.id));
  }

  function format(metric, value) {
    if (!Number.isFinite(value)) return "–";
    return isPercent(metric) ? `${(value * 100).toFixed(2)}%` : value.toFixed(2);
  }

  function renderLineageToggles() {
    const box = document.getElementById("lineage-toggles");
    box.innerHTML = "";
    for (const lineage of snapshot.lineages) {
      const label = document.createElement("label");
      label.className = "check lineage-toggle";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.dataset.lineage = lineage.id;
      input.checked = !state.lineages || state.lineages.includes(lineage.id);
      input.addEventListener("change", () => {
        const current = new Set(state.lineages || snapshot.lineages.map((item) => item.id));
        if (input.checked) current.add(lineage.id);
        else current.delete(lineage.id);
        state.lineages = [...current];
        render();
      });
      label.appendChild(input);
      label.appendChild(document.createTextNode(` ${lineage.name}`));
      box.appendChild(label);
    }
  }

  function renderTaskSelect() {
    const select = document.getElementById("trend-task");
    select.innerHTML = "";
    const all = document.createElement("option");
    all.value = "";
    all.textContent = "All tasks";
    select.appendChild(all);
    for (const task of Object.keys(snapshot.tasks)) {
      const option = document.createElement("option");
      option.value = task;
      option.textContent = taskLabel(task);
      if (task === state.task) option.selected = true;
      select.appendChild(option);
    }
    select.onchange = () => {
      state.task = select.value;
      render();
    };
  }

  function renderMetricSelects() {
    const box = document.getElementById("metric-selects");
    box.innerHTML = "";
    for (const [task, info] of Object.entries(snapshot.tasks)) {
      const field = document.createElement("div");
      field.className = "field";
      const id = `metric-${task}`;
      const label = document.createElement("label");
      label.htmlFor = id;
      label.textContent = `${taskLabel(task)} metric`;
      const select = document.createElement("select");
      select.id = id;
      for (const metric of info.metrics || []) {
        const option = document.createElement("option");
        option.value = metric.name;
        option.textContent = metric.label;
        if (metric.name === metricOf(task)) option.selected = true;
        select.appendChild(option);
      }
      select.onchange = () => {
        state.metrics[task] = select.value;
        render();
      };
      field.appendChild(label);
      field.appendChild(select);
      box.appendChild(field);
    }
  }

  function panelSeries(panel, task, metric) {
    const lineages = visibleLineages();
    const series = [];
    for (const lineage of lineages) {
      const data = panel.points
        .filter((point) => point.lineage === lineage.id && Number.isFinite((point.values || {})[metric]))
        .map((point) => [point.step, point.values[metric]])
        .sort((a, b) => a[0] - b[0]);
      if (!data.length) continue;
      series.push({
        name: lineage.name,
        type: "line",
        data,
        showSymbol: data.length <= 12,
        symbolSize: 6,
        connectNulls: false,
        lineStyle: { width: 2 },
        emphasis: { focus: "series" },
      });
    }
    return series;
  }

  function chartColors() {
    const css = getComputedStyle(document.documentElement);
    return {
      ink: css.getPropertyValue("--muted").trim() || "#8b98a9",
      line: css.getPropertyValue("--line").trim() || "#263041",
      text: css.getPropertyValue("--text").trim() || "#e6edf3",
      panel: css.getPropertyValue("--panel").trim() || "#161b22",
    };
  }

  function renderPanel(panel) {
    const task = panel.task;
    const metric = metricOf(task);
    const rule = ruleOf(task, metric);
    const wrapper = document.createElement("section");
    wrapper.className = "trend-panel";
    wrapper.dataset.family = panel.family;
    const head = document.createElement("div");
    head.className = "trend-panel-head";
    const title = document.createElement("h3");
    const percent = isPercent(metric);
    title.textContent = `${panel.family} · ${rule.label}${percent ? " (%)" : ""} ${rule.lower ? "↓" : "↑"}`;
    head.appendChild(title);
    wrapper.appendChild(head);
    const el = document.createElement("div");
    el.className = "trend-chart";
    wrapper.appendChild(el);
    document.getElementById("trend-panels").appendChild(wrapper);

    const series = panelSeries(panel, task, metric);
    if (!series.length) {
      el.innerHTML = `<div class="placeholder">No result for the visible lineages.</div>`;
      return;
    }
    const colors = chartColors();
    const chart = echarts.init(el, null, { renderer: "canvas" });
    charts.push(chart);
    chart.setOption({
      backgroundColor: "transparent",
      animationDuration: 300,
      tooltip: {
        trigger: "axis",
        backgroundColor: colors.panel,
        borderColor: colors.line,
        textStyle: { color: colors.text },
        formatter: (items) => {
          if (!items.length) return "";
          const step = items[0].value[0];
          const rows = items
            .map((item) => `${W.escape(item.seriesName)}: ${W.escape(format(metric, item.value[1]))}`)
            .join("<br/>");
          return `<b>step ${W.escape(step)}</b><br/>${rows}`;
        },
      },
      grid: { left: 56, right: 20, top: 18, bottom: 44 },
      xAxis: {
        type: "value",
        name: "Step",
        nameLocation: "middle",
        nameGap: 28,
        scale: true,
        axisLabel: { color: colors.ink, fontSize: 11 },
        axisLine: { lineStyle: { color: colors.line } },
        splitLine: { lineStyle: { color: colors.line, opacity: 0.45 } },
      },
      yAxis: {
        type: "value",
        scale: true,
        axisLabel: {
          color: colors.ink,
          fontSize: 11,
          formatter: (value) => (percent ? `${(value * 100).toFixed(0)}%` : value.toFixed(2)),
        },
        axisLine: { show: false },
        splitLine: { lineStyle: { color: colors.line, opacity: 0.45 } },
      },
      series,
    });
  }

  function renderPanels() {
    for (const chart of charts) chart.dispose();
    charts = [];
    const box = document.getElementById("trend-panels");
    box.innerHTML = "";
    if (!snapshot.lineages.length) {
      box.innerHTML = '<div class="placeholder">No checkpoints to trend yet.</div>';
      return;
    }
    const panels = snapshot.panels.filter((panel) => !state.task || panel.task === state.task);
    if (!panels.length) {
      box.innerHTML = '<div class="placeholder">No results for this task.</div>';
      return;
    }
    for (const panel of panels) renderPanel(panel);
  }

  function renderStatus() {
    const el = document.getElementById("statusline");
    if (!el || !snapshot) return;
    const visible = visibleLineages();
    el.textContent = `${visible.length} lineages · ${snapshot.panels.length} benchmarks · updated ${new Date().toLocaleTimeString()}`;
  }

  function render() {
    if (!snapshot) return;
    renderLineageToggles();
    renderTaskSelect();
    renderMetricSelects();
    renderPanels();
    renderStatus();
  }

  function rankCheckpoints() {
    return [];
  }

  async function load() {
    const response = await fetch("/api/trends", { cache: "no-store" });
    if (response.status === 401) {
      W.loginRedirect();
      return;
    }
    if (!response.ok) throw new Error(`trends: HTTP ${response.status}`);
    snapshot = await response.json();
    render();
  }

  async function boot() {
    W.theme.init();
    window.addEventListener("worklog:theme", () => {
      if (snapshot) renderPanels();
    });
    window.addEventListener("resize", () => charts.forEach((chart) => chart.resize()));
    try {
      await load();
    } catch (error) {
      document.getElementById("trend-panels").innerHTML = `<div class="placeholder">Could not load trends: ${W.escape(error.message)}</div>`;
    }
  }

  W.trends = { render, rankCheckpoints };
  document.addEventListener("DOMContentLoaded", boot);
})(window.Worklog);
