/* Trends page: one line per training lineage, one panel per benchmark. */
window.Worklog = window.Worklog || {};

(function (W) {
  const TASK_LABELS = { asr: "Speech recognition", st: "Speech translation", audio_mcq: "Multiple-choice QA" };
  let snapshot = null;
  let charts = [];
  let languageChart = null;
  let lastLanguages = null;
  let languageTimer = null;
  const state = {
    lineages: null,
    task: "",
    metrics: {},
    x: "step",
    pinned: "",
    tab: "benchmarks",
    langFamily: "",
    langLineage: "",
    langMetric: "",
    isolatedLang: "",
    sort: { key: "mean", dir: 1 },
  };

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

  function familyRule(family) {
    const panel = (snapshot.panels || []).find((item) => item.family === family);
    const taskInfo = (snapshot.tasks || {})[panel ? panel.task : ""] || {};
    const metric = taskInfo.metric || "";
    return { ...ruleOf(panel ? panel.task : "", metric), metric };
  }

  function visibleLineages() {
    return snapshot.lineages.filter((lineage) => !state.lineages || state.lineages.includes(lineage.id));
  }

  function format(metric, value) {
    if (!Number.isFinite(value)) return "–";
    return isPercent(metric) ? `${(value * 100).toFixed(2)}%` : value.toFixed(2);
  }

  function readHash() {
    const params = new URLSearchParams(location.hash.replace(/^#/, ""));
    const lineages = params.get("lineages");
    state.lineages = lineages ? (lineages === "none" ? [] : lineages.split(",").filter(Boolean)) : null;
    state.task = params.get("task") || "";
    state.metrics = {};
    for (const [key, value] of params.entries()) {
      if (key.startsWith("metric-") && value) state.metrics[key.slice("metric-".length)] = value;
    }
    state.x = params.get("x") === "progress" ? "progress" : "step";
    state.pinned = params.get("pinned") || "";
    state.tab = params.get("tab") === "languages" ? "languages" : "benchmarks";
    state.langFamily = params.get("lang-family") || "";
    state.langLineage = params.get("lang-lineage") || "";
    state.langMetric = params.get("lang-metric") || "";
  }

  function writeHash() {
    const params = new URLSearchParams();
    if (state.lineages) {
      params.set("lineages", state.lineages.length ? state.lineages.join(",") : "none");
    }
    if (state.task) params.set("task", state.task);
    for (const [task, name] of Object.entries(state.metrics)) {
      if (name) params.set(`metric-${task}`, name);
    }
    if (state.x !== "step") params.set("x", state.x);
    if (state.pinned) params.set("pinned", state.pinned);
    if (state.tab === "languages") {
      params.set("tab", "languages");
      if (state.langFamily) params.set("lang-family", state.langFamily);
      if (state.langLineage) params.set("lang-lineage", state.langLineage);
      if (state.langMetric) params.set("lang-metric", state.langMetric);
    }
    const hash = params.toString();
    history.replaceState(null, "", hash ? `#${hash}` : location.pathname + location.search);
  }

  function renderLineageToggles() {
    const box = document.getElementById("lineage-toggles");
    box.innerHTML = "";
    for (const lineage of snapshot.lineages) {
      const label = document.createElement("label");
      label.className = "check lineage-toggle";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.className = "lineage-toggle";
      input.dataset.lineage = lineage.id;
      input.checked = !state.lineages || state.lineages.includes(lineage.id);
      input.addEventListener("change", () => {
        const current = new Set(state.lineages || snapshot.lineages.map((item) => item.id));
        if (input.checked) current.add(lineage.id);
        else current.delete(lineage.id);
        state.lineages = [...current];
        writeHash();
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
      writeHash();
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
        writeHash();
        render();
      };
      field.appendChild(label);
      field.appendChild(select);
      box.appendChild(field);
    }
  }

  function renderXButtons() {
    document.getElementById("x-step").classList.toggle("active", state.x === "step");
    document.getElementById("x-progress").classList.toggle("active", state.x === "progress");
  }

  function xFor(step, lineageId) {
    if (state.x === "step") return step;
    const lineage = snapshot.lineages.find((item) => item.id === lineageId);
    return lineage && lineage.max_step ? (step / lineage.max_step) * 100 : step;
  }

  function pinnedCheckpoint() {
    if (!state.pinned) return null;
    return (snapshot.checkpoints || []).find((row) => String(row.step) === String(state.pinned)) || null;
  }

  function chartColors() {
    const css = getComputedStyle(document.documentElement);
    return {
      ink: css.getPropertyValue("--muted").trim() || "#8b98a9",
      line: css.getPropertyValue("--line").trim() || "#263041",
      text: css.getPropertyValue("--text").trim() || "#e6edf3",
      panel: css.getPropertyValue("--panel").trim() || "#161b22",
      good: css.getPropertyValue("--good").trim() || "#3ddc97",
      bad: css.getPropertyValue("--bad").trim() || "#ff6b6b",
      accent: css.getPropertyValue("--accent").trim() || "#4cc9f0",
    };
  }

  function panelSeries(panel, task, metric, lower, colors) {
    const pinned = pinnedCheckpoint();
    const series = [];
    for (const lineage of visibleLineages()) {
      const data = panel.points
        .filter((point) => point.lineage === lineage.id && Number.isFinite((point.values || {})[metric]))
        .map((point) => [xFor(point.step, lineage.id), point.values[metric]])
        .sort((a, b) => a[0] - b[0]);
      if (!data.length) continue;

      let best = data[0];
      let worst = data[0];
      for (const point of data) {
        if (lower ? point[1] < best[1] : point[1] > best[1]) best = point;
        if (lower ? point[1] > worst[1] : point[1] < worst[1]) worst = point;
      }
      const marks = [];
      if (data.length > 1 && best !== worst) {
        marks.push(
          { name: "best", coord: best, itemStyle: { color: colors.good }, label: { formatter: "best", color: colors.good } },
          { name: "worst", coord: worst, itemStyle: { color: colors.bad }, label: { formatter: "worst", color: colors.bad } }
        );
      }
      const item = {
        name: lineage.name,
        type: "line",
        data,
        showSymbol: data.length <= 12,
        symbolSize: 6,
        connectNulls: false,
        lineStyle: { width: 2 },
        emphasis: { focus: "series" },
      };
      if (marks.length) {
        item.markPoint = { symbolSize: 44, data: marks };
      }
      if (pinned) {
        item.markLine = {
          symbol: "none",
          silent: true,
          lineStyle: { color: colors.accent, type: "dashed", width: 1.5 },
          label: { show: true, formatter: `step ${pinned.step}`, color: colors.accent },
          data: [{ xAxis: xFor(pinned.step, pinned.lineage) }],
        };
      }
      series.push(item);
    }
    return series;
  }

  function renderPanel(panel, colors) {
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

    const series = panelSeries(panel, task, metric, rule.lower, colors);
    if (!series.length) {
      el.innerHTML = `<div class="placeholder">No result for the visible lineages.</div>`;
      return;
    }
    const first = charts.length === 0;
    const chart = echarts.init(el, null, { renderer: "canvas" });
    chart.group = "trends";
    charts.push(chart);
    const zoom = [{ type: "inside", xAxisIndex: 0 }, { type: "inside", yAxisIndex: 0 }];
    if (first) {
      zoom.push({ type: "slider", xAxisIndex: 0, bottom: 6, height: 16, textStyle: { color: colors.ink } });
    }
    chart.setOption({
      backgroundColor: "transparent",
      animationDuration: 300,
      dataZoom: zoom,
      tooltip: {
        trigger: "axis",
        backgroundColor: colors.panel,
        borderColor: colors.line,
        textStyle: { color: colors.text },
        formatter: (items) => {
          if (!items.length) return "";
          const step = state.x === "step" ? items[0].value[0] : Math.round(items[0].value[0]);
          const rows = items
            .map((item) => `${W.escape(item.seriesName)}: ${W.escape(format(metric, item.value[1]))}`)
            .join("<br/>");
          return `<b>${state.x === "step" ? `step ${W.escape(step)}` : `${W.escape(step)}% of training`}</b><br/>${rows}`;
        },
      },
      grid: { left: 56, right: 20, top: 18, bottom: first ? 62 : 44 },
      xAxis: {
        type: "value",
        name: state.x === "step" ? "Step" : "Progress (%)",
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
    const colors = chartColors();
    for (const panel of panels) renderPanel(panel, colors);
    if (charts.length) echarts.connect("trends");
  }

  function rankCheckpoints(snap, visibleIds) {
    const visible = new Set(visibleIds);
    const rows = (snap.checkpoints || []).filter((row) => visible.has(row.lineage));
    const families = [...new Set((snap.panels || []).map((panel) => panel.family))];
    const ranks = new Map();
    for (const family of families) {
      const panel = (snap.panels || []).find((item) => item.family === family);
      const taskInfo = panel ? (snap.tasks || {})[panel.task] || {} : {};
      const rule = (taskInfo.metrics || []).find((item) => item.name === taskInfo.metric) || { lower: false };
      const entries = rows.filter((row) => Number.isFinite((row.values || {})[family]));
      entries.sort((a, b) =>
        rule.lower ? a.values[family] - b.values[family] : b.values[family] - a.values[family]
      );
      let index = 0;
      while (index < entries.length) {
        let end = index;
        while (end + 1 < entries.length && entries[end + 1].values[family] === entries[index].values[family]) {
          end += 1;
        }
        const rank = (index + 1 + end + 1) / 2;
        for (let position = index; position <= end; position += 1) {
          if (!ranks.has(entries[position])) ranks.set(entries[position], {});
          ranks.get(entries[position])[family] = rank;
        }
        index = end + 1;
      }
    }
    return rows
      .map((row) => {
        const own = ranks.get(row) || {};
        const values = Object.values(own);
        return {
          ...row,
          ranks: own,
          coverage: values.length,
          mean_rank: values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : Infinity,
        };
      })
      .sort((a, b) => a.mean_rank - b.mean_rank || b.coverage - a.coverage || a.step - b.step);
  }

  function sortedRows(rows, families) {
    const { key, dir } = state.sort;
    const sorted = [...rows];
    sorted.sort((a, b) => {
      if (key === "mean") return (a.mean_rank - b.mean_rank) * dir;
      if (key === "coverage") return ((a.coverage - b.coverage) * dir) || (a.mean_rank - b.mean_rank);
      const family = key.slice(2);
      const left = a.values[family];
      const right = b.values[family];
      if (!Number.isFinite(left) && !Number.isFinite(right)) return a.step - b.step;
      if (!Number.isFinite(left)) return 1;
      if (!Number.isFinite(right)) return -1;
      const rule = familyRule(family);
      return (rule.lower ? left - right : right - left) * dir;
    });
    return sorted;
  }

  function renderLeaderboard() {
    const box = document.getElementById("leaderboard");
    if (!box) return;
    const ids = visibleLineages().map((lineage) => lineage.id);
    const minCoverage = Math.max(Number(document.getElementById("min-coverage").value) || 1, 1);
    const rows = rankCheckpoints(snapshot, ids).filter((row) => row.coverage >= minCoverage);
    const families = [...new Set(snapshot.panels.map((panel) => panel.family))];
    if (!rows.length) {
      box.innerHTML = '<div class="placeholder">No checkpoint has enough coverage for this view.</div>';
      return;
    }
    const best = {};
    const worst = {};
    for (const family of families) {
      const values = rows
        .map((row) => (row.values || {})[family])
        .filter((value) => Number.isFinite(value));
      if (!values.length) continue;
      const rule = familyRule(family);
      best[family] = rule.lower ? Math.min(...values) : Math.max(...values);
      worst[family] = rule.lower ? Math.max(...values) : Math.min(...values);
    }
    const ordered = sortedRows(rows, families);
    const head = families
      .map((family) => `<th data-sort="f:${W.escape(family)}">${W.escape(family)}</th>`)
      .join("");
    const body = ordered
      .map((row) => {
        const cells = families
          .map((family) => {
            const value = (row.values || {})[family];
            const rule = familyRule(family);
            let mark = "";
            if (Number.isFinite(value) && value === best[family]) mark = " ●";
            else if (Number.isFinite(value) && value === worst[family]) mark = " ○";
            const cls = mark === " ●" ? "best" : mark === " ○" ? "worst" : "";
            return `<td data-family="${W.escape(family)}" class="${cls}">${W.escape(format(rule.metric, value))}${mark}</td>`;
          })
          .join("");
        return `<tr class="checkpoint-row" data-lineage="${W.escape(row.lineage)}" data-step="${row.step}">
          <td>${W.escape((snapshot.lineages.find((item) => item.id === row.lineage) || {}).name || row.lineage)}</td>
          <td class="mono">${row.step}</td>${cells}
          <td class="num">${Number.isFinite(row.mean_rank) ? row.mean_rank.toFixed(2) : "–"}</td>
          <td class="num">${row.coverage}</td></tr>`;
      })
      .join("");
    box.innerHTML = `<table class="leaderboard-table"><thead><tr>
      <th>lineage</th><th data-sort="step">step</th>${head}
      <th data-sort="mean">mean rank</th><th data-sort="coverage">coverage</th>
    </tr></thead><tbody>${body}</tbody></table>`;
    for (const header of box.querySelectorAll("th[data-sort]")) {
      header.addEventListener("click", () => {
        const key = header.dataset.sort;
        state.sort = { key, dir: state.sort.key === key ? -state.sort.dir : 1 };
        renderLeaderboard();
      });
    }
    for (const row of box.querySelectorAll("tr.checkpoint-row")) {
      row.addEventListener("click", () => {
        state.pinned = row.dataset.step;
        writeHash();
        render();
      });
    }
  }

  function languageFamilies() {
    return (snapshot.panels || []).filter((panel) => (panel.languages || []).length > 1);
  }

  function languageMetricChoices(panel) {
    const taskInfo = snapshot.tasks[panel.task] || {};
    const available = new Set();
    for (const point of panel.points) {
      for (const name of Object.keys(point.values || {})) available.add(name);
    }
    const ordered = (taskInfo.metrics || []).filter((metric) => available.has(metric.name));
    return ordered.length ? ordered : taskInfo.metrics || [];
  }

  function renderLanguageControls() {
    const familySelect = document.getElementById("lang-family");
    const lineageSelect = document.getElementById("lang-lineage");
    const metricSelect = document.getElementById("lang-metric");
    const panels = languageFamilies();
    if (!panels.length) {
      familySelect.innerHTML = "";
      lineageSelect.innerHTML = "";
      metricSelect.innerHTML = "";
      return null;
    }
    const family = panels.find((panel) => panel.family === state.langFamily) || panels[0];
    state.langFamily = family.family;
    familySelect.innerHTML = "";
    for (const panel of panels) {
      const option = document.createElement("option");
      option.value = panel.family;
      option.textContent = panel.family;
      if (panel.family === state.langFamily) option.selected = true;
      familySelect.appendChild(option);
    }
    familySelect.onchange = () => {
      state.langFamily = familySelect.value;
      state.langLineage = "";
      state.langMetric = "";
      state.isolatedLang = "";
      writeHash();
      renderLanguageControls();
      loadLanguages();
    };

    const candidates = snapshot.lineages.filter((lineage) =>
      family.points.some((point) => point.lineage === lineage.id)
    );
    const lineage = candidates.find((item) => item.id === state.langLineage) || candidates[0];
    state.langLineage = lineage ? lineage.id : "";
    lineageSelect.innerHTML = "";
    for (const item of candidates) {
      const option = document.createElement("option");
      option.value = item.id;
      option.textContent = item.name;
      if (item.id === state.langLineage) option.selected = true;
      lineageSelect.appendChild(option);
    }
    lineageSelect.onchange = () => {
      state.langLineage = lineageSelect.value;
      state.isolatedLang = "";
      writeHash();
      loadLanguages();
    };

    const metrics = languageMetricChoices(family);
    const metric = metrics.find((item) => item.name === state.langMetric) || metrics[0];
    state.langMetric = metric ? metric.name : "";
    metricSelect.innerHTML = "";
    for (const item of metrics) {
      const option = document.createElement("option");
      option.value = item.name;
      option.textContent = item.label;
      if (item.name === state.langMetric) option.selected = true;
      metricSelect.appendChild(option);
    }
    metricSelect.onchange = () => {
      state.langMetric = metricSelect.value;
      state.isolatedLang = "";
      writeHash();
      loadLanguages();
    };
    return family;
  }

  function renderLanguageChart(body) {
    const el = document.getElementById("language-chart");
    if (languageChart) {
      languageChart.dispose();
      languageChart = null;
    }
    el.innerHTML = "";
    const metric = body.metric || { name: "", label: "", lower: false };
    const percent = isPercent(metric.name);
    const colors = chartColors();
    const groups = new Map();
    for (const point of body.points) {
      if (!groups.has(point.lang)) groups.set(point.lang, []);
      groups.get(point.lang).push([point.step, point.value]);
    }
    let names = [...groups.keys()].sort();
    if (state.isolatedLang) names = names.filter((name) => name === state.isolatedLang);
    const series = names.map((name) => ({
      name,
      type: "line",
      data: (groups.get(name) || []).sort((a, b) => a[0] - b[0]),
      showSymbol: true,
      symbolSize: 5,
      connectNulls: false,
      lineStyle: { width: 1.6 },
      emphasis: { focus: "series" },
    }));
    const chart = echarts.init(el, null, { renderer: "canvas" });
    chart.setOption({
      backgroundColor: "transparent",
      animationDuration: 250,
      tooltip: {
        trigger: "axis",
        backgroundColor: colors.panel,
        borderColor: colors.line,
        textStyle: { color: colors.text },
        formatter: (items) =>
          items.length
            ? `<b>step ${W.escape(items[0].value[0])}</b><br/>${items
                .map((item) => `${W.escape(item.seriesName)}: ${W.escape(format(metric.name, item.value[1]))}`)
                .join("<br/>")}`
            : "",
      },
      legend: { type: "scroll", bottom: 0, textStyle: { color: colors.ink, fontSize: 10 } },
      grid: { left: 56, right: 20, top: 18, bottom: 58 },
      xAxis: {
        type: "value",
        name: "Step",
        nameLocation: "middle",
        nameGap: 26,
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
          formatter: (value) => (percent ? `${(value * 100).toFixed(0)}%` : value.toFixed(1)),
        },
        splitLine: { lineStyle: { color: colors.line, opacity: 0.45 } },
      },
      series,
    });
    chart.on("click", (event) => {
      if (!event || !event.seriesName) return;
      state.isolatedLang = state.isolatedLang === event.seriesName ? "" : event.seriesName;
      renderLanguageChart(body);
    });
    languageChart = chart;
  }

  function renderLanguageList(body) {
    const metric = body.metric || { name: "", lower: false };
    const regressed = new Set(body.regressions.map((item) => item.lang));
    const weakest = body.worst_now.filter((item) => !regressed.has(item.lang));
    const row = (item) => `<tr class="lang-row${state.isolatedLang === item.lang ? " active" : ""}" data-lang="${W.escape(item.lang)}">
      <td><b>${W.escape(item.lang)}</b></td>
      <td>${W.escape(format(metric.name, item.latest))} <span class="hint">@${item.latest_step}</span></td>
      <td>${W.escape(format(metric.name, item.best))} <span class="hint">@${item.best_step}</span></td>
      <td>${W.escape(format(metric.name, item.gap))}</td>
      <td class="lang-onset">${item.onset === null || item.onset === undefined ? "—" : item.onset}</td>
    </tr>`;
    const section = (title, items) =>
      items.length
        ? `<h4>${title}</h4><table><thead><tr><th>language</th><th>latest</th><th>best</th><th>gap</th><th>onset</th></tr></thead><tbody>${items
            .map(row)
            .join("")}</tbody></table>`
        : "";
    const box = document.getElementById("language-list");
    box.innerHTML =
      section("Biggest regressions", body.regressions) +
      section("Weakest now", weakest) +
      (!body.regressions.length && !weakest.length ? '<div class="placeholder">No language detail.</div>' : "");
    for (const item of box.querySelectorAll(".lang-row")) {
      item.addEventListener("click", () => {
        const lang = item.dataset.lang;
        state.isolatedLang = state.isolatedLang === lang ? "" : lang;
        renderLanguageChart(body);
        for (const other of box.querySelectorAll(".lang-row")) {
          other.classList.toggle("active", other.dataset.lang === state.isolatedLang);
        }
      });
    }
  }

  async function loadLanguages() {
    if (!state.langFamily || !state.langLineage || !state.langMetric) {
      const family = renderLanguageControls();
      if (!family) return;
    }
    const threshold = Math.max(Number(document.getElementById("lang-threshold").value) || 10, 0) / 100;
    const n = Math.max(Number(document.getElementById("lang-n").value) || 5, 1);
    const query = new URLSearchParams({
      family: state.langFamily,
      lineage: state.langLineage,
      metric: state.langMetric,
      threshold: String(threshold),
      n: String(n),
    });
    try {
      const response = await fetch(`/api/trends/languages?${query.toString()}`, { cache: "no-store" });
      if (response.status === 401) {
        W.loginRedirect();
        return;
      }
      if (!response.ok) {
        document.getElementById("language-list").innerHTML = '<div class="placeholder">No language detail for this selection.</div>';
        return;
      }
      const body = await response.json();
      lastLanguages = body;
      renderLanguageChart(body);
      renderLanguageList(body);
    } catch (error) {
      document.getElementById("language-list").innerHTML = `<div class="placeholder">Could not load languages: ${W.escape(error.message)}</div>`;
    }
  }

  function setTab(tab) {
    state.tab = tab === "languages" ? "languages" : "benchmarks";
    writeHash();
    const languages = state.tab === "languages";
    document.getElementById("benchmarks-pane").hidden = languages;
    document.getElementById("language-pane").hidden = !languages;
    for (const [id, active] of [["tab-benchmarks", !languages], ["tab-languages", languages]]) {
      const button = document.getElementById(id);
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
    }
    if (languages) {
      renderLanguageControls();
      loadLanguages();
    }
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
    renderXButtons();
    renderPanels();
    renderLeaderboard();
    renderStatus();
  }

  async function load(version) {
    const url = version ? `/api/trends?version=${encodeURIComponent(version)}` : "/api/trends";
    const response = await fetch(url, { cache: "no-store" });
    if (response.status === 401) {
      W.loginRedirect();
      return false;
    }
    if (response.status === 204) return false;
    if (!response.ok) throw new Error(`trends: HTTP ${response.status}`);
    snapshot = await response.json();
    render();
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
    const button = document.getElementById("trend-refresh");
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
    readHash();
    window.addEventListener("worklog:theme", () => {
      if (snapshot) renderPanels();
      if (state.tab === "languages" && lastLanguages) renderLanguageChart(lastLanguages);
    });
    window.addEventListener("resize", () => {
      charts.forEach((chart) => chart.resize());
      if (languageChart) languageChart.resize();
    });
    document.getElementById("tab-benchmarks").addEventListener("click", () => setTab("benchmarks"));
    document.getElementById("tab-languages").addEventListener("click", () => setTab("languages"));
    const scheduleLanguages = () => {
      clearTimeout(languageTimer);
      languageTimer = setTimeout(loadLanguages, 300);
    };
    document.getElementById("lang-threshold").addEventListener("input", scheduleLanguages);
    document.getElementById("lang-n").addEventListener("input", scheduleLanguages);
    document.getElementById("x-step").addEventListener("click", () => {
      state.x = "step";
      writeHash();
      renderPanels();
      renderXButtons();
    });
    document.getElementById("x-progress").addEventListener("click", () => {
      state.x = "progress";
      writeHash();
      renderPanels();
      renderXButtons();
    });
    document.getElementById("reset-zoom").addEventListener("click", renderPanels);
    document.getElementById("min-coverage").addEventListener("change", renderLeaderboard);
    document.getElementById("trend-refresh").addEventListener("click", refreshNow);
    try {
      await load();
    } catch (error) {
      document.getElementById("trend-panels").innerHTML = `<div class="placeholder">Could not load trends: ${W.escape(error.message)}</div>`;
      return;
    }
    setTab(state.tab);
    setInterval(poll, 10000);
  }

  W.trends = { render, rankCheckpoints };
  document.addEventListener("DOMContentLoaded", boot);
})(window.Worklog);
