/* The /inspect page: one run's setup, scores and per-sample transcript cards. */
window.Worklog = window.Worklog || {};

(function (W) {
  const DEFAULT_LIMIT = 25;

  let runId = null;
  let currentOffset = 0;
  let currentLimit = DEFAULT_LIMIT;
  let currentQ = "";
  let currentSort = "order";
  let currentFilter = "";
  let overview = null;
  let searchTimer = null;

  function params() {
    return new URLSearchParams(location.search);
  }

  function readParams() {
    const p = params();
    runId = p.get("run");
    currentOffset = Math.max(parseInt(p.get("offset") || "0", 10) || 0, 0);
    currentQ = p.get("q") || "";
    currentSort = p.get("sort") || "order";
    currentFilter = p.get("filter") || "";
    const back = p.get("back");
    const backLink = document.getElementById("back-link");
    if (backLink && back) backLink.href = back;
  }

  function writeParams() {
    const p = new URLSearchParams();
    if (runId) p.set("run", runId);
    if (currentOffset) p.set("offset", String(currentOffset));
    if (currentQ) p.set("q", currentQ);
    if (currentSort && currentSort !== "order") p.set("sort", currentSort);
    if (currentFilter) p.set("filter", currentFilter);
    const back = params().get("back");
    if (back) p.set("back", back);
    const query = p.toString();
    history.replaceState(null, "", query ? `${location.pathname}?${query}` : location.pathname);
  }

  function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  function setHtml(id, html) {
    const el = document.getElementById(id);
    if (el) el.innerHTML = html;
  }

  function fmtPercent(value) {
    if (value === null || value === undefined || Number.isNaN(value)) return "–";
    return `${(value * 100).toFixed(2)}%`;
  }

  function fmtDuration(seconds) {
    if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return "–";
    if (seconds < 1) return `${(seconds * 1000).toFixed(0)} ms`;
    if (seconds < 60) return `${seconds.toFixed(1)} s`;
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}:${secs.toFixed(1).padStart(4, "0")}`;
  }

  function renderJson(data) {
    return `<pre>${W.escape(JSON.stringify(data, null, 2))}</pre>`;
  }

  function field(label, value, isCode) {
    const content = isCode ? `<pre>${W.escape(value)}</pre>` : W.escape(value);
    return `<div class="fieldgrid-label">${W.escape(label)}</div>
      <div class="fieldgrid-value">${content}</div>`;
  }

  function stringify(value) {
    if (value === null || value === undefined) return "–";
    return JSON.stringify(value, null, 2);
  }

  function clampedRow(label, text, clampId) {
    const safe = W.escape(text === null || text === undefined ? "–" : String(text));
    return `<div class="sample-row">
      <div class="sample-label">${W.escape(label)}</div>
      <div class="sample-value">
        <div class="clamped" id="${W.escape(clampId)}">${safe}</div>
        <button class="btn ghost show-all" type="button" data-target="${W.escape(clampId)}">show all</button>
      </div>
    </div>`;
  }

  function plainRow(label, text, isCode) {
    const safe = text === null || text === undefined ? "–" : String(text);
    const content = isCode ? `<pre>${W.escape(safe)}</pre>` : W.escape(safe);
    return `<div class="sample-row">
      <div class="sample-label">${W.escape(label)}</div>
      <div class="sample-value">${content}</div>
    </div>`;
  }

  function renderSetup(setup) {
    const taskArgs = setup.task_args || {};
    const formatSpec = taskArgs.format_spec || setup.format_spec;
    const rows = [
      field("prompt_style", taskArgs.prompt_style || "–", false),
      field("instruction", taskArgs.instruction || "–", false),
      field("format_spec", stringify(formatSpec), true),
      field("generate_config", stringify(setup.generate_config || setup.model_generate_config), true),
      field("model_args", stringify(setup.model_args), true),
      field("scorers", stringify(setup.scorers), true),
      field("packages", stringify(setup.packages), true),
      field("dataset", stringify(setup.dataset), true),
    ].join("");

    let note = "";
    if (formatSpec && typeof formatSpec === "object" && formatSpec.apply_chat_template) {
      note = `<p class="hint">Chat template is applied inside the provider; the messages shown are pre-template.</p>`;
    }
    setHtml("setup", rows + note);

    const files = setup.context_files || {};
    const fileNames = ["instruction_file", "system_prompt_file", "model_config_override"];
    const filesHtml = fileNames
      .map((name) => {
        const file = files[name];
        if (!file) return "";
        const text = file.text !== null && file.text !== undefined ? file.text : file.note || "–";
        return `<details class="setup-file">
          <summary>${W.escape(name)}: <code>${W.escape(file.path)}</code> ${file.exists ? "✓" : "✗"}</summary>
          ${renderJson(text)}
        </details>`;
      })
      .filter(Boolean)
      .join("");
    setHtml("setup-files", filesHtml);
  }

  function renderScores(scores) {
    if (!Array.isArray(scores) || !scores.length) {
      setHtml("scores", `<div class="placeholder">No scores recorded.</div>`);
      return;
    }
    const html = scores
      .map((scorer) => {
        const metrics = scorer.metrics || {};
        const rows = Object.entries(metrics)
          .map(([name, metric]) => {
            let value = metric;
            if (typeof metric === "object" && metric !== null) value = metric.value;
            return `<tr><td>${W.escape(name)}</td><td>${W.escape(String(value))}</td></tr>`;
          })
          .join("");
        return `<div class="score-table-wrap">
          <h4>${W.escape(scorer.name || "scorer")}</h4>
          <table class="score-table">${rows}</table>
        </div>`;
      })
      .join("");
    setHtml("scores", html);
  }

  function firstAnswer(scores) {
    for (const entry of Object.values(scores || {})) {
      if (entry && entry.answer !== undefined && entry.answer !== null) return entry.answer;
    }
    return null;
  }

  function flagChips(flags) {
    return (flags || [])
      .map((f) => `<span class="chip ${W.escape(f)}">${W.escape(f)}</span>`)
      .join("");
  }

  function renderSample(sample) {
    const timing = sample.timing || {};
    const normalized = firstAnswer(sample.scores);
    const clampId = (suffix) => `s-${suffix}-${W.escape(sample.id)}`;

    let rows = "";
    rows += clampedRow("Reference", sample.target, clampId("ref"));
    rows += clampedRow("Normalized", normalized, clampId("norm"));
    rows += clampedRow("Generated", sample.completion, clampId("gen"));
    rows += plainRow("Prompt", sample.prompt, true);
    rows += plainRow("Audio", sample.input, false);

    const details = `<details><summary>messages</summary>${renderJson(sample.messages)}</details>
      <details><summary>scores</summary>${renderJson(sample.scores)}</details>
      <details><summary>metadata</summary>${renderJson(sample.metadata)}</details>`;

    return `<article class="sample" data-id="${W.escape(sample.id)}">
      <div class="sample-head">
        <span class="sample-id">${W.escape(sample.id)}</span>
        <span class="sample-wer">${W.escape(fmtPercent(sample.wer))}</span>
        <span class="sample-time">${W.escape(fmtDuration(timing.total_time))}</span>
        ${flagChips(sample.flags)}
      </div>
      <div class="sample-body">${rows}</div>
      <div class="sample-details">${details}</div>
    </article>`;
  }

  function renderSortOptions(sortKeys) {
    const select = document.getElementById("sample-sort");
    if (!select) return;
    select.innerHTML = sortKeys
      .map((key) => {
        const selected = key === currentSort ? " selected" : "";
        return `<option value="${W.escape(key)}"${selected}>${W.escape(key)}</option>`;
      })
      .join("");
  }

  function renderSamples(data) {
    const total = data.total;
    const offset = data.offset;
    const limit = data.limit;
    const first = total === 0 ? 0 : offset + 1;
    const last = Math.min(offset + limit, total);
    setText("sample-count", total === 0 ? "0 of 0" : `${first}–${last} of ${total}`);

    currentOffset = offset;
    currentLimit = limit;
    const digest = (overview && overview.digest) || {};
    renderSortOptions(digest.sorts || ["order"]);

    const prev = document.getElementById("sample-prev");
    const next = document.getElementById("sample-next");
    if (prev) prev.disabled = offset <= 0;
    if (next) next.disabled = offset + limit >= total;

    if (total === 0) {
      const status = (overview && overview.run && overview.run.status) || "unknown";
      const message = currentQ
        ? "No samples match this search."
        : `This run has no samples (status ${status}).`;
      setHtml("samples", `<div class="placeholder">${W.escape(message)}</div>`);
      return;
    }

    setHtml("samples", data.samples.map(renderSample).join(""));

    document.querySelectorAll(".show-all").forEach((button) => {
      button.addEventListener("click", (event) => {
        const target = document.getElementById(event.target.dataset.target);
        if (!target) return;
        target.classList.toggle("expanded");
        event.target.textContent = target.classList.contains("expanded") ? "show less" : "show all";
      });
    });
  }

  function updateUrlAndLoad() {
    writeParams();
    loadSamples();
  }

  async function loadOverview() {
    if (!runId) {
      setText("run-title", "No run selected");
      setHtml("run-meta", "No run selected — pick a slice in the results matrix.");
      setHtml("samples", `<div class="placeholder">No run selected — pick a slice in the results matrix.</div>`);
      return;
    }
    const response = await fetch(`/api/run/${encodeURIComponent(runId)}/overview`, { cache: "no-store" });
    if (!response.ok) {
      const body = await response.json().catch(() => ({ error: `HTTP ${response.status}` }));
      setText("run-title", "Error");
      setHtml("run-meta", W.escape(body.error || `HTTP ${response.status}`));
      setHtml("samples", `<div class="placeholder">${W.escape(body.error || `HTTP ${response.status}`)}</div>`);
      return;
    }
    overview = await response.json();
    const run = overview.run || {};
    const setup = overview.setup || {};
    const digest = overview.digest || {};

    setText("run-title", `${run.model || "unknown"} · ${run.benchmark || "unknown"}`);
    const metaParts = [
      run.task || "–",
      run.status || "–",
      run.created ? `created ${W.when(run.created)}` : "",
      run.finished ? `finished ${W.when(run.finished)}` : "",
      `${digest.samples || 0} samples`,
      `${digest.failed || 0} failed`,
      `${digest.flagged || 0} flagged`,
      run.path || "",
    ].filter(Boolean);
    setText("run-meta", metaParts.join(" · "));

    renderSetup(setup);
    renderScores(overview.scores);

    if (digest.samples === 0) {
      setText("sample-count", "0 of 0");
      setHtml("samples", `<div class="placeholder">This run has no samples (status ${W.escape(run.status || "unknown")}).</div>`);
      return;
    }
    await loadSamples();
  }

  async function loadSamples() {
    if (!runId) return;
    const url = new URL(`/api/run/${encodeURIComponent(runId)}/samples`, location.origin);
    url.searchParams.set("offset", String(currentOffset));
    url.searchParams.set("limit", String(currentLimit));
    if (currentQ) url.searchParams.set("q", currentQ);
    if (currentSort) url.searchParams.set("sort", currentSort);
    if (currentFilter) url.searchParams.set("filter", currentFilter);

    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) {
      const body = await response.json().catch(() => ({ error: `HTTP ${response.status}` }));
      setHtml("samples", `<div class="placeholder">${W.escape(body.error || `HTTP ${response.status}`)}</div>`);
      return;
    }
    renderSamples(await response.json());
  }

  function bindControls() {
    const search = document.getElementById("sample-search");
    const sort = document.getElementById("sample-sort");
    const issues = document.getElementById("sample-issues");
    const prev = document.getElementById("sample-prev");
    const next = document.getElementById("sample-next");

    if (search) {
      search.value = currentQ;
      search.addEventListener("input", () => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => {
          currentQ = search.value;
          currentOffset = 0;
          updateUrlAndLoad();
        }, 250);
      });
    }
    if (sort) {
      sort.addEventListener("change", () => {
        currentSort = sort.value;
        currentOffset = 0;
        updateUrlAndLoad();
      });
    }
    if (issues) {
      issues.checked = currentFilter === "issues";
      issues.addEventListener("change", () => {
        currentFilter = issues.checked ? "issues" : "";
        currentOffset = 0;
        updateUrlAndLoad();
      });
    }
    if (prev) {
      prev.addEventListener("click", () => {
        currentOffset = Math.max(currentOffset - currentLimit, 0);
        updateUrlAndLoad();
      });
    }
    if (next) {
      next.addEventListener("click", () => {
        currentOffset += currentLimit;
        updateUrlAndLoad();
      });
    }
  }

  async function boot() {
    W.theme.init();
    readParams();
    bindControls();
    await loadOverview();
  }

  W.inspect = { boot };
  document.addEventListener("DOMContentLoaded", boot);
})(window.Worklog);
