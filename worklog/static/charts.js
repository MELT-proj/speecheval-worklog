/* Language heatmap: models x languages (or benchmarks) for one dataset family. */
window.Worklog = window.Worklog || {};

(function (W) {
  let chart = null;

  function languageOf(row) {
    if (row.lang) return row.lang;
    if (row.direction === "enx") return row.tgt || row.src;
    if (row.direction === "xen") return row.src || row.tgt;
    return row.tgt || row.src;
  }

  function visible(snapshot, state) {
    return snapshot.slices.filter(
      (slice) =>
        (!state.campaign || slice.campaign === state.campaign) &&
        (!state.modelFamily || snapshot.model_families[slice.model] === state.modelFamily) &&
        (!state.language ||
          slice.lang === state.language ||
          slice.src === state.language ||
          slice.tgt === state.language)
    );
  }

  function taskForFamily(snapshot, family) {
    for (const [task, matrix] of Object.entries(snapshot.matrix)) {
      if (matrix.cells.some((cell) => cell.family === family)) return task;
    }
    return null;
  }

  /* Families with results under the campaign/group filters, in config order. */
  function familiesWithResults(snapshot, state) {
    const present = new Set(
      snapshot.slices
        .filter(
          (slice) =>
            (!state.campaign || slice.campaign === state.campaign) &&
            (!state.modelFamily || snapshot.model_families[slice.model] === state.modelFamily)
        )
        .map((slice) => slice.family)
    );
    return snapshot.filters.families.filter(
      (family) =>
        present.has(family) &&
        (!state.task || taskForFamily(snapshot, family) === state.task) &&
        (!state.family || family === state.family)
    );
  }

  function commonPrefix(values) {
    return values.reduce((prefix, value) => {
      let i = 0;
      while (i < prefix.length && i < value.length && prefix[i] === value[i]) i += 1;
      return prefix.slice(0, i);
    });
  }

  /* ``librispeech-validation-clean`` -> ``clean`` within its own family. */
  function variantLabel(names, name) {
    let prefix = commonPrefix(names);
    const cut = prefix.lastIndexOf("-");
    if (cut > 0) prefix = prefix.slice(0, cut + 1);
    return name.startsWith(prefix) && name.length > prefix.length ? name.slice(prefix.length) : name;
  }

  /* Columns are benchmarks when the family has no language axis (LibriSpeech). */
  function axisFor(rows) {
    const languages = [...new Set(rows.map(languageOf).filter(Boolean))].sort();
    const benchmarks = [...new Set(rows.map((row) => row.benchmark))].sort();
    if (benchmarks.length > 1 && languages.length <= 1) {
      return { mode: "benchmark", values: benchmarks };
    }
    return { mode: "language", values: languages };
  }

  function columnOf(row, mode) {
    return mode === "benchmark" ? row.benchmark : languageOf(row);
  }

  function columnLabel(axis, value) {
    return axis.mode === "benchmark" ? variantLabel(axis.values, value) : value;
  }

  function renderTabs(families, active) {
    const bar = document.getElementById("heatmap-tabs");
    if (!bar) return;
    bar.innerHTML = "";
    for (const family of families) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = family === active ? "tab active" : "tab";
      button.setAttribute("role", "tab");
      button.setAttribute("aria-selected", String(family === active));
      button.textContent = family;
      button.addEventListener("click", () => W.app.setHeatmapFamily(family));
      bar.appendChild(button);
    }
  }

  function selectedMetric(snapshot, state, task) {
    return (state.metric && state.metric[task]) || snapshot.matrix[task].metric;
  }

  function render(snapshot, state) {
    const el = document.getElementById("heatmap");
    const hint = document.getElementById("heatmap-hint");
    if (!el) return;
    const slices = visible(snapshot, state);
    const families = familiesWithResults(snapshot, state);
    if (!families.length) {
      renderTabs([], "");
      el.innerHTML = '<div class="placeholder">No heatmap data for the current filters.</div>';
      return;
    }
    const family = state.heatmap && families.includes(state.heatmap) ? state.heatmap : families[0];
    renderTabs(families, family);
    const task = taskForFamily(snapshot, family);
    if (!task || !snapshot.matrix[task]) {
      el.innerHTML = '<div class="placeholder">No heatmap data for the current filters.</div>';
      return;
    }
    const metric = selectedMetric(snapshot, state, task);
    const rule = (snapshot.matrix[task].metrics || []).find((item) => item.name === metric) || {
      name: metric,
      label: metric,
      lower: snapshot.matrix[task].lower,
    };

    const rows = slices.filter((slice) => slice.family === family && slice.task === task);
    const models = snapshot.filters.models.filter(
      (model) =>
        W.modelVisible(snapshot, state, model, task) && rows.some((row) => row.model === model)
    );
    const axis = axisFor(rows);
    if (!rows.length || !axis.values.length) {
      el.innerHTML = '<div class="placeholder">No per-language results for this family yet.</div>';
      return;
    }

    const byCell = new Map();
    let min = Infinity;
    let max = -Infinity;
    rows.forEach((row) => {
      const value = row.metrics[metric];
      if (!Number.isFinite(value)) return;
      const x = axis.values.indexOf(columnOf(row, axis.mode));
      const y = models.indexOf(row.model);
      if (x < 0 || y < 0) return;
      const key = `${x}|${y}`;
      if (!byCell.has(key)) byCell.set(key, []);
      byCell.get(key).push(value);
    });
    const data = [...byCell.entries()].map(([key, values]) => {
      const [x, y] = key.split("|").map(Number);
      const value = values.reduce((sum, item) => sum + item, 0) / values.length;
      min = Math.min(min, value);
      max = Math.max(max, value);
      return [x, y, value];
    });

    el.innerHTML = "";
    el.style.height = `${Math.max(260, 46 + models.length * 38)}px`;
    if (chart) chart.dispose();
    const css = getComputedStyle(document.documentElement);
    const ink = css.getPropertyValue("--muted").trim() || "#8b98a9";
    const gridLine = css.getPropertyValue("--line").trim() || "#263041";
    const panel = css.getPropertyValue("--panel").trim() || "#161b22";
    const text = css.getPropertyValue("--text").trim() || "#e6edf3";
    const pageBg = css.getPropertyValue("--bg").trim() || "#0f1216";
    chart = echarts.init(el, null, { renderer: "canvas" });
    const percent = metric.includes("wer") || metric.includes("cer");
    const colors = rule.lower
      ? ["#1f7a5a", "#3ddc97", "#f4b942", "#ff6b6b"]
      : ["#1d3557", "#2a6f97", "#4cc9f0", "#3ddc97"];
    chart.setOption({
      backgroundColor: "transparent",
      animationDuration: 350,
      tooltip: {
        backgroundColor: panel,
        borderColor: gridLine,
        textStyle: { color: text },
        formatter: (item) => {
          const model = models[item.value[1]];
          const column = columnLabel(axis, axis.values[item.value[0]]);
          const value = percent ? (item.value[2] * 100).toFixed(2) : item.value[2].toFixed(2);
          return `<b>${W.escape(model)}</b><br/>${W.escape(column)} · ${W.escape(rule.label)}: ${value}`;
        },
      },
      grid: { left: 230, right: 24, top: 18, bottom: 78 },
      xAxis: {
        type: "category",
        data: axis.values.map((value) => columnLabel(axis, value)),
        splitArea: { show: true, areaStyle: { color: ["transparent", "rgba(127,127,127,.06)"] } },
        axisLabel: { color: ink, fontSize: 11 },
        axisLine: { lineStyle: { color: gridLine } },
        axisTick: { show: false },
      },
      yAxis: {
        type: "category",
        data: models,
        axisLabel: { color: ink, fontSize: 11, fontFamily: "ui-monospace, monospace" },
        axisLine: { show: false },
        axisTick: { show: false },
      },
      visualMap: {
        min: Number.isFinite(min) ? min : 0,
        max: Number.isFinite(max) && Number.isFinite(min) && max > min ? max : (Number.isFinite(min) ? min + 1e-9 : 1),
        calculable: false,
        orient: "horizontal",
        left: "center",
        bottom: 8,
        itemWidth: 12,
        itemHeight: 90,
        textStyle: { color: ink, fontSize: 10 },
        formatter: (value) => (percent ? (value * 100).toFixed(0) : value.toFixed(1)),
        inRange: { color: colors },
      },
      series: [
        {
          type: "heatmap",
          data,
          label: {
            show: true,
            color: "#071014",
            fontSize: 10,
            formatter: (item) => (percent ? (item.value[2] * 100).toFixed(1) : item.value[2].toFixed(1)),
          },
          itemStyle: { borderColor: pageBg, borderWidth: 2, borderRadius: 4 },
          emphasis: { itemStyle: { borderColor: "#e6edf3", borderWidth: 1 } },
        },
      ],
    });
    if (hint) {
      hint.textContent = `${family} · ${rule.label}${percent ? " (%)" : ""}${rule.lower ? " · lower is better" : ""}`;
    }
  }

  window.addEventListener("resize", () => chart && chart.resize());
  W.charts = { render };
})(window.Worklog);
