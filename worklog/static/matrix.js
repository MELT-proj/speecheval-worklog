/* The results matrix: models x benchmark families, with a drill-down per cell. */
window.Worklog = window.Worklog || {};

(function (W) {
  const GROUP_ORDER = ["baseline", "melt", "smurf", "other"];
  const TASKS = [
    ["asr", "Speech recognition"],
    ["st", "Speech translation"],
    ["audio_mcq", "Multiple-choice QA"],
  ];

  function isPercent(metric) {
    return (
      metric.includes("wer") ||
      metric.includes("cer") ||
      metric.includes("accuracy") ||
      metric.includes("unresolved")
    );
  }

  function format(metric, value) {
    if (!Number.isFinite(value)) return "–";
    return isPercent(metric) ? (value * 100).toFixed(2) : W.fmt(value, 2);
  }

  function sliceLabel(slice) {
    if (slice.src && slice.tgt) return `${slice.src} \u2192 ${slice.tgt}`;
    return slice.lang || slice.slice || slice.benchmark;
  }

  function selectedMetric(snapshot, state, task) {
    return (state.metric && state.metric[task]) || snapshot.matrix[task].metric;
  }

  function metricRule(matrix, name) {
    return (matrix.metrics || []).find((item) => item.name === name) || {
      name,
      label: name,
      lower: matrix.lower,
    };
  }

  function cellFor(rows, metric, state) {
    const values = rows
      .filter((row) => Number.isFinite(row.metrics[metric]))
      .map((row) => ({ value: row.metrics[metric], samples: row.samples }));
    const pending = rows.some((row) => (row.pending || []).length);
    const total = rows.length;
    let status = "ok";
    if (!total) status = "empty";
    else if (!values.length) status = pending ? "pending" : "empty";
    else if (values.length < total) status = "partial";
    return { value: W.average(values, state.aggregate || "macro"), n: values.length, total, status };
  }

  function renderTable(snapshot, state, task, title) {
    const matrix = snapshot.matrix[task];
    if (!matrix) return null;
    const metric = selectedMetric(snapshot, state, task);
    const rule = metricRule(matrix, metric);
    const families = snapshot.filters.families.filter((name) => {
      if (state.family && name !== state.family) return false;
      return matrix.cells.some((cell) => cell.family === name);
    });
    if (!families.length) return null;
    const slices = W.visibleSlices(snapshot, state).filter((slice) => slice.task === task);
    const models = snapshot.filters.models.filter((model) =>
      W.modelVisible(snapshot, state, model, task)
    );
    const commonOn = !!(state.common && state.common[task]);
    const common = commonOn ? W.commonColumns(snapshot, state, task, metric) : null;
    const cellSlices = commonOn ? slices.filter((slice) => W.keepsRow(common, slice)) : slices;

    const wrapper = document.createElement("section");
    wrapper.className = "matrix-block";
    const head = document.createElement("div");
    head.className = "matrix-head";
    const h = document.createElement("h3");
    h.className = "matrix-title";
    h.textContent = `${title} · ${rule.label}${isPercent(metric) ? " (%)" : ""} ${rule.lower ? "↓" : "↑"}`;
    head.appendChild(h);
    const select = document.createElement("select");
    select.id = `metric-${task}`;
    select.className = "metric-select";
    select.title = "Metric shown in this table";
    for (const item of matrix.metrics || []) {
      const option = document.createElement("option");
      option.value = item.name;
      option.textContent = item.label;
      if (item.name === metric) option.selected = true;
      select.appendChild(option);
    }
    select.addEventListener("change", (event) => W.app.setMetric(task, event.target.value));

    const controls = document.createElement("div");
    controls.className = "matrix-controls";
    controls.appendChild(select);
    const toggle = document.createElement("label");
    toggle.className = "check";
    toggle.title =
      "Restrict this table to the languages every visible model has a result on " +
      "(LibriSpeech uses its two benchmark variants as columns)";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.id = `common-${task}`;
    box.checked = !!(state.common && state.common[task]);
    box.addEventListener("change", (event) => W.app.setCommon(task, event.target.checked));
    toggle.appendChild(box);
    toggle.appendChild(document.createTextNode(" common languages only"));
    controls.appendChild(toggle);
    head.appendChild(controls);
    wrapper.appendChild(head);

    if (commonOn) {
      const anyColumn = families.some((name) => {
        const entry = common.get(name);
        return !!entry && entry.columns.size > 0;
      });
      if (!anyColumn) {
        const empty = document.createElement("div");
        empty.className = "placeholder";
        empty.textContent = "No column has a result for every visible model at this metric.";
        wrapper.appendChild(empty);
        return wrapper;
      }
    }

    const table = document.createElement("table");
    table.className = "matrix-table";
    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    headRow.innerHTML = "<th>Model</th>" + families.map((f) => `<th>${W.escape(f)}</th>`).join("");
    thead.appendChild(headRow);
    table.appendChild(thead);

    const tbody = document.createElement("tbody");
    const grid = {};
    for (const family of families) grid[family] = [];
    const groups = {};
    for (const model of models) {
      const kind = snapshot.model_families[model] || "other";
      (groups[kind] = groups[kind] || []).push(model);
    }

    for (const kind of GROUP_ORDER) {
      const names = groups[kind];
      if (!names || !names.length) continue;
      const groupRow = document.createElement("tr");
      groupRow.className = "group";
      groupRow.innerHTML = `<td colspan="${families.length + 1}">${W.familyLabel(kind)}</td>`;
      tbody.appendChild(groupRow);

      for (const model of names) {
        const row = document.createElement("tr");
        const nameCell = document.createElement("td");
        nameCell.innerHTML = `<div class="model-cell"><span class="dot ${kind}"></span><span class="model-name">${W.escape(model)}</span></div>`;
        row.appendChild(nameCell);

        for (const family of families) {
          const cellRows = cellSlices.filter((slice) => slice.model === model && slice.family === family);
          const cell = cellFor(cellRows, metric, state);
          grid[family].push({ model, value: cell.value, el: null });
          const td = document.createElement("td");
          td.className = `cell ${cell.status}`;
          if (cell.status === "empty") td.textContent = "–";
          else if (cell.status === "pending") td.innerHTML = '<span class="v">…</span>';
          else {
            td.innerHTML = `<span class="v">${format(metric, cell.value)}</span><span class="n">${cell.n}/${cell.total}</span>`;
            td.addEventListener("click", () => drill(snapshot, state, model, family, task, metric));
          }
          if (
            snapshot.runs.some(
              (run) => run.model === model && run.status === "error" && cellRows.some((s) => s.slice === run.slice)
            )
          ) {
            td.classList.add("failed");
          }
          row.appendChild(td);
          grid[family][grid[family].length - 1].el = td;
        }
        tbody.appendChild(row);
      }
    }
    table.appendChild(tbody);

    // Highlight the best value per column (lower-is-better metrics inverted).
    for (const family of families) {
      const values = grid[family].filter((item) => Number.isFinite(item.value));
      if (values.length < 2) continue;
      const best = rule.lower
        ? Math.min(...values.map((item) => item.value))
        : Math.max(...values.map((item) => item.value));
      const worst = rule.lower
        ? Math.max(...values.map((item) => item.value))
        : Math.min(...values.map((item) => item.value));
      for (const item of values) {
        if (!item.el) continue;
        const span = worst - best || 1;
        const t = (item.value - best) / span;
        const alpha = 0.22 * (1 - t) + 0.04;
        const rgb = t < 0.5 ? "61, 220, 151" : "255, 107, 107";
        item.el.style.background = `rgba(${rgb}, ${alpha.toFixed(3)})`;
        if (item.value === best) {
          const star = document.createElement("span");
          star.className = "star";
          star.textContent = "★";
          item.el.appendChild(star);
        }
      }
    }

    wrapper.appendChild(table);
    return wrapper;
  }

  function renderUnclassified(snapshot, state) {
    const rows = W.visibleSlices(snapshot, state).filter((slice) => slice.family === "unclassified");
    if (!rows.length) return null;
    const block = document.createElement("section");
    block.className = "matrix-block";
    block.innerHTML = `
      <h3 class="matrix-title">Unclassified · ${rows.length} slice(s) not in worklog.yaml</h3>
      <div class="unclassified">
        <table class="matrix-table detail">
          <thead><tr><th>Benchmark</th><th>Slice</th><th>Model</th><th>Status</th><th>Log</th></tr></thead>
          <tbody>
            ${rows
              .map(
                (row) => `<tr><td>${W.escape(row.benchmark)}</td><td>${W.escape(row.slice)}</td>
                  <td>${W.escape(row.model)}</td><td>${W.escape(row.status)}</td>
                  <td class="logpath">${W.escape(row.path)}</td></tr>`
              )
              .join("")}
          </tbody>
        </table>
      </div>`;
    return block;
  }

  function drill(snapshot, state, model, family, task, metric) {
    const matrix = snapshot.matrix[task];
    const rule = metricRule(matrix, metric);
    const all = W.visibleSlices(snapshot, state)
      .filter((slice) => slice.model === model && slice.family === family && slice.task === task)
      .sort((a, b) => (b.metrics[metric] ?? -Infinity) - (a.metrics[metric] ?? -Infinity));
    const commonOn = !!(state.common && state.common[task]);
    const common = commonOn ? W.commonColumns(snapshot, state, task, metric) : null;
    const rows = commonOn ? all.filter((row) => W.keepsRow(common, row)) : all;
    const entry = commonOn ? common.get(family) : null;
    const unit = entry && entry.mode === "benchmark" ? "variants" : "languages";

    const title = document.getElementById("modal-title");
    const body = document.getElementById("modal-body");
    const modal = document.getElementById("modal");
    title.textContent = `${model} · ${family}`;

    // The inspector reads `back` as a same-origin path (see safeBackUrl in
    // inspect.js), hence the "/" before the filter hash.
    const lines = rows
      .map((row) => {
        const has = Number.isFinite(row.metrics[metric]);
        const value = has ? format(metric, row.metrics[metric]) : "–";
        const status = !has && (row.pending || []).length ? "pending" : row.status;
        return `<tr>
          <td>${W.escape(sliceLabel(row))}</td>
          <td>${value}</td>
          <td>${row.completed}/${row.samples}</td>
          <td>${W.escape(status)}</td>
          <td class="logpath">${W.escape(row.path)}</td>
          <td><a class="samples" href="/inspect?run=${row.run_id}&back=${encodeURIComponent("/" + location.hash)}">open</a></td>
        </tr>`;
      })
      .join("");

    const hintLine = commonOn
      ? `${rows.length} of ${all.length} ${unit} · ${W.escape(rule.label)} · common only · click a path to copy it`
      : `${all.length} slice(s) · ${W.escape(rule.label)} · click a path to copy it`;
    body.innerHTML = `
      <p class="hint">${hintLine}</p>
      <table class="matrix-table detail">
        <thead><tr><th>Slice</th><th>${W.escape(rule.label)}</th><th>Samples</th><th>Status</th><th>Log</th><th>Inspect</th></tr></thead>
        <tbody>${lines || '<tr><td colspan="6">No slices match the current filters.</td></tr>'}</tbody>
      </table>`;
    body.querySelectorAll("td.logpath").forEach((cell) => {
      cell.style.cursor = "copy";
      cell.title = "Click to copy";
      cell.addEventListener("click", () => navigator.clipboard && navigator.clipboard.writeText(cell.textContent));
    });
    if (typeof modal.showModal === "function") modal.showModal();
  }

  W.matrix = {
    render(snapshot, state) {
      const el = document.getElementById("matrix");
      el.innerHTML = "";
      let rendered = 0;
      for (const [task, title] of TASKS) {
        if (state.task && state.task !== task) continue;
        const block = renderTable(snapshot, state, task, title);
        if (block) {
          el.appendChild(block);
          rendered += 1;
        }
      }
      const unclassified = renderUnclassified(snapshot, state);
      if (unclassified) el.appendChild(unclassified);
      if (!rendered && !unclassified) el.innerHTML = '<div class="placeholder">No results match the current filters.</div>';
    },
    drill,
  };

  const close = document.getElementById("modal-close");
  if (close) close.addEventListener("click", () => document.getElementById("modal").close());
})(window.Worklog);
