/* The results matrix: models x benchmark families, with a drill-down per cell. */
window.Worklog = window.Worklog || {};

(function (W) {
  const GROUP_ORDER = ["baseline", "melt", "smurf", "other"];
  const TASKS = [
    ["asr", "Speech recognition"],
    ["st", "Speech translation"],
  ];

  function isPercent(metric) {
    return /(^|[._])(wer|cer)($|_)/.test(metric) || metric.includes("wer") || metric.includes("cer");
  }

  function format(metric, value) {
    if (!Number.isFinite(value)) return "–";
    return isPercent(metric) ? `${(value * 100).toFixed(2)}` : W.fmt(value, 2);
  }

  function sliceLabel(slice) {
    if (slice.src && slice.tgt) return `${slice.src} \u2192 ${slice.tgt}`;
    return slice.slice || slice.benchmark;
  }

  function visibleSlices(snapshot, state) {
    return snapshot.slices.filter(
      (slice) =>
        (!state.campaign || slice.campaign === state.campaign) &&
        (!state.modelFamily || snapshot.model_families[slice.model] === state.modelFamily) &&
        (!state.language || slice.src === state.language || slice.tgt === state.language || slice.lang === state.language)
    );
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
    const families = snapshot.filters.families.filter((name) => {
      const cells = matrix.cells.filter((cell) => cell.family === name);
      return cells.length > 0;
    });
    if (!families.length) return null;
    const slices = visibleSlices(snapshot, state).filter((slice) => slice.task === task);
    const models = snapshot.filters.models.filter(
      (model) => !state.modelFamily || snapshot.model_families[model] === state.modelFamily
    );

    const wrapper = document.createElement("section");
    wrapper.className = "matrix-block";
    const h = document.createElement("h3");
    h.className = "matrix-title";
    h.textContent = `${title} · ${matrix.label}${isPercent(matrix.metric) ? " (%)" : ""} ${matrix.lower ? "↓" : "↑"}`;
    wrapper.appendChild(h);

    const table = document.createElement("table");
    table.className = "matrix-table";
    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    headRow.innerHTML = "<th>Model</th>" + families.map((f) => `<th>${W.escape(f)}</th>`).join("");
    thead.appendChild(headRow);
    table.appendChild(thead);

    const tbody = document.createElement("tbody");
    const grid = {}; // family -> [{model, value}]
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
          const cellRows = slices.filter((slice) => slice.model === model && slice.family === family);
          const cell = cellFor(cellRows, matrix.metric, state);
          grid[family].push({ model, value: cell.value, el: null });
          const td = document.createElement("td");
          td.className = `cell ${cell.status}`;
          if (cell.status === "empty") td.textContent = "–";
          else if (cell.status === "pending") td.innerHTML = "<span class=\"v\">…</span>";
          else {
            td.innerHTML = `<span class="v">${format(matrix.metric, cell.value)}</span><span class="n">${cell.n}/${cell.total}</span>`;
            td.addEventListener("click", () => drill(snapshot, state, model, family, task));
          }
          if (snapshot.runs.some((run) => run.model === model && run.status === "error" && cellRows.some((s) => s.slice === run.slice))) {
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
      const best = matrix.lower
        ? Math.min(...values.map((item) => item.value))
        : Math.max(...values.map((item) => item.value));
      const worst = matrix.lower
        ? Math.max(...values.map((item) => item.value))
        : Math.min(...values.map((item) => item.value));
      for (const item of values) {
        if (!item.el) continue;
        const span = worst - best || 1;
        const t = (item.value - best) / span; // 0 best, 1 worst
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

  function drill(snapshot, state, model, family, task) {
    const metric = snapshot.matrix[task].metric;
    const rows = visibleSlices(snapshot, state)
      .filter((slice) => slice.model === model && slice.family === family && slice.task === task)
      .sort((a, b) => (b.metrics[metric] ?? -Infinity) - (a.metrics[metric] ?? -Infinity));

    const title = document.getElementById("modal-title");
    const body = document.getElementById("modal-body");
    const modal = document.getElementById("modal");
    title.textContent = `${model} · ${family}`;

    const lines = rows
      .map((row) => {
        const value = Number.isFinite(row.metrics[metric]) ? format(metric, row.metrics[metric]) : "–";
        const status = (row.pending || []).length && !Number.isFinite(row.metrics[metric]) ? "pending" : row.status;
        return `<tr>
          <td>${W.escape(sliceLabel(row))}</td>
          <td>${value}</td>
          <td>${row.completed}/${row.samples}</td>
          <td>${W.escape(status)}</td>
          <td class="logpath">${W.escape(row.path)}</td>
        </tr>`;
      })
      .join("");

    body.innerHTML = `
      <p class="hint">${rows.length} slice(s) · ${W.escape(snapshot.matrix[task].label)} · click a row's path to copy it</p>
      <table class="matrix-table detail">
        <thead><tr><th>Slice</th><th>${W.escape(snapshot.matrix[task].label)}</th><th>Samples</th><th>Status</th><th>Log</th></tr></thead>
        <tbody>${lines || '<tr><td colspan="5">No slices match the current filters.</td></tr>'}</tbody>
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
      if (!rendered) el.innerHTML = '<div class="placeholder">No results match the current filters.</div>';
    },
    drill,
  };

  const close = document.getElementById("modal-close");
  if (close) close.addEventListener("click", () => document.getElementById("modal").close());
})(window.Worklog);
