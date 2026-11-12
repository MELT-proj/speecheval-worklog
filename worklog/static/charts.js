/* Language heatmap: models x languages for one benchmark family. */
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

  function activeTask(snapshot, state, slices) {
    if (state.family) return taskForFamily(snapshot, state.family) || "st";
    if (state.task) return state.task;
    const present = ["st", "asr"].filter((task) => slices.some((slice) => slice.task === task));
    return present[0] || "st";
  }

  function activeFamily(snapshot, state, slices, task) {
    if (state.family) return state.family;
    const first = snapshot.filters.families.find((family) =>
      slices.some((slice) => slice.family === family && slice.task === task)
    );
    return first || snapshot.filters.families[0];
  }

  function selectedMetric(snapshot, state, task) {
    return (state.metric && state.metric[task]) || snapshot.matrix[task].metric;
  }

  function render(snapshot, state) {
    const el = document.getElementById("heatmap");
    const hint = document.getElementById("heatmap-hint");
    if (!el) return;
    const slices = visible(snapshot, state);
    const task = activeTask(snapshot, state, slices);
    if (!snapshot.matrix[task]) {
      el.innerHTML = '<div class="placeholder">No heatmap data for the current filters.</div>';
      return;
    }
    const metric = selectedMetric(snapshot, state, task);
    const rule = (snapshot.matrix[task].metrics || []).find((item) => item.name === metric) || {
      name: metric,
      label: metric,
      lower: snapshot.matrix[task].lower,
    };
    const family = activeFamily(snapshot, state, slices, task);
    if (!family) {
      el.innerHTML = '<div class="placeholder">No heatmap data for the current filters.</div>';
      return;
    }

    const rows = slices.filter((slice) => slice.family === family && slice.task === task);
    const models = snapshot.filters.models.filter(
      (model) => !state.modelFamily || snapshot.model_families[model] === state.modelFamily
    );
    const languages = [...new Set(rows.map(languageOf).filter(Boolean))].sort();
    if (!rows.length || !languages.length) {
      el.innerHTML = '<div class="placeholder">No per-language results for this family yet.</div>';
      return;
    }

    const data = [];
    let min = Infinity;
    let max = -Infinity;
    rows.forEach((row) => {
      const value = row.metrics[metric];
      if (!Number.isFinite(value)) return;
      const x = languages.indexOf(languageOf(row));
      const y = models.indexOf(row.model);
      if (x < 0 || y < 0) return;
      data.push([x, y, value]);
      min = Math.min(min, value);
      max = Math.max(max, value);
    });

    el.innerHTML = "";
    el.style.height = `${Math.max(260, 46 + models.length * 38)}px`;
    if (chart) chart.dispose();
    chart = echarts.init(el, null, { renderer: "canvas" });
    const percent = metric.includes("wer") || metric.includes("cer");
    const colors = rule.lower
      ? ["#1f7a5a", "#3ddc97", "#f4b942", "#ff6b6b"]
      : ["#1d3557", "#2a6f97", "#4cc9f0", "#3ddc97"];
    chart.setOption({
      backgroundColor: "transparent",
      animationDuration: 350,
      tooltip: {
        formatter: (item) => {
          const model = models[item.value[1]];
          const language = languages[item.value[0]];
          const value = percent ? (item.value[2] * 100).toFixed(2) : item.value[2].toFixed(2);
          return `<b>${W.escape(model)}</b><br/>${W.escape(language)} · ${W.escape(rule.label)}: ${value}`;
        },
      },
      grid: { left: 150, right: 24, top: 18, bottom: 78 },
      xAxis: {
        type: "category",
        data: languages,
        splitArea: { show: true, areaStyle: { color: ["transparent", "rgba(255,255,255,.015)"] } },
        axisLabel: { color: "#8b98a9", fontSize: 11 },
        axisLine: { lineStyle: { color: "#263041" } },
        axisTick: { show: false },
      },
      yAxis: {
        type: "category",
        data: models,
        axisLabel: { color: "#8b98a9", fontSize: 11, fontFamily: "ui-monospace, monospace" },
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
        textStyle: { color: "#8b98a9", fontSize: 10 },
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
          itemStyle: { borderColor: "#0f1216", borderWidth: 2, borderRadius: 4 },
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
