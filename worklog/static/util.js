/* Shared formatting and the one aggregation rule the client re-applies. */
window.Worklog = window.Worklog || {};

(function (W) {
  const FAMILY_LABELS = { baseline: "Baselines", melt: "MELT", smurf: "SMURF", other: "Other" };

  W.familyLabel = (key) => FAMILY_LABELS[key] || "Other";

  W.fmt = function (value, digits) {
    if (value === null || value === undefined || Number.isNaN(value)) return "–";
    const d = digits === undefined ? (Math.abs(value) < 1 ? 3 : 2) : digits;
    return Number(value).toFixed(d);
  };

  /* Macro mean or sample-weighted mean, mirroring views.aggregate. */
  W.average = function (values, kind) {
    const items = values.filter((item) => Number.isFinite(item.value));
    if (!items.length) return null;
    if (kind === "weighted") {
      const usable = items.filter((item) => (item.samples || 0) > 0);
      const total = usable.reduce((sum, item) => sum + item.samples, 0);
      if (!total) return null;
      return usable.reduce((sum, item) => sum + item.value * item.samples, 0) / total;
    }
    return items.reduce((sum, item) => sum + item.value, 0) / items.length;
  };

  /* Highest step of a model's base within the current campaign scope: a
     campaign that only ran an older checkpoint still shows it. */
  function latestInScope(snapshot, state, model) {
    const info = (snapshot.checkpoints || {})[model];
    if (!info || info.step === null) return true;
    const kind = snapshot.model_families[model] || "other";
    let highest = info.step;
    for (const [other, otherInfo] of Object.entries(snapshot.checkpoints || {})) {
      if ((snapshot.model_families[other] || "other") !== kind) continue;
      if (otherInfo.base !== info.base || otherInfo.step === null) continue;
      if (
        state.campaign &&
        !snapshot.slices.some((slice) => slice.model === other && slice.campaign === state.campaign)
      ) {
        continue;
      }
      highest = Math.max(highest, otherInfo.step);
    }
    return info.step >= highest;
  }

  /* A model shown in a task's results: it ran that task, it passes the group
     filter, and (unless Show all is ticked) it is the latest MELT/SMURF step. */
  W.modelVisible = function (snapshot, state, model, task) {
    const kind = snapshot.model_families[model] || "other";
    if (state.modelFamily && kind !== state.modelFamily) return false;
    if (task && !snapshot.slices.some((slice) => slice.model === model && slice.task === task)) return false;
    if (!state.showAllCheckpoints && (kind === "melt" || kind === "smurf") && !latestInScope(snapshot, state, model)) {
      return false;
    }
    return true;
  };

  W.duration = function (start, end) {
    if (!start || !end) return "";
    const ms = new Date(end) - new Date(start);
    if (!Number.isFinite(ms) || ms < 0) return "";
    const minutes = Math.floor(ms / 60000);
    if (minutes < 1) return "<1 min";
    if (minutes < 60) return `${minutes} min`;
    const hours = Math.floor(minutes / 60);
    return `${hours} h ${minutes % 60} min`;
  };

  W.when = function (iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString();
  };

  const ENTITIES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  /* ---- theme ---------------------------------------------------------- */
  const THEME_KEY = "worklog-theme";
  const theme = (function () {
    function current() {
      return document.documentElement.dataset.theme || "dark";
    }
    function paintButton() {
      const button = document.getElementById("theme-toggle");
      if (!button) return;
      const light = current() === "light";
      const next = light ? "dark" : "light";
      button.textContent = light ? "\u2600" : "\u263E";
      button.title = `Switch to ${next} theme`;
      button.setAttribute("aria-label", `Switch to ${next} theme`);
    }
    function set(name) {
      document.documentElement.dataset.theme = name;
      try {
        localStorage.setItem(THEME_KEY, name);
      } catch (error) {
        /* private mode: the theme just will not persist */
      }
      paintButton();
      window.dispatchEvent(new Event("worklog:theme"));
    }
    function toggle() {
      set(current() === "dark" ? "light" : "dark");
    }
    function init() {
      let saved = null;
      try {
        saved = localStorage.getItem(THEME_KEY);
      } catch (error) {
        saved = null;
      }
      const prefersLight =
        window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches;
      set(saved || (prefersLight ? "light" : "dark"));
      const button = document.getElementById("theme-toggle");
      if (button) button.addEventListener("click", toggle);
    }
    return { init, set, toggle, current };
  })();
  W.theme = theme;

  /* Send the browser to the login page, returning to the current view. */
  W.loginRedirect = function () {
    const here = location.pathname + location.search + location.hash;
    location.assign("/login?next=" + encodeURIComponent(here));
  };

  /* ---- slice columns ---------------------------------------------------- */

  /* A slice's column within its family: its language, or its benchmark when the
     family has no language axis (LibriSpeech). Shared by the matrix, the
     heatmap and the common-languages filter so they cannot disagree. */
  W.languageOf = function (row) {
    if (row.lang) return row.lang;
    if (row.direction === "enx") return row.tgt || row.src;
    if (row.direction === "xen") return row.src || row.tgt;
    return row.tgt || row.src;
  };

  /* Columns are benchmarks when the family has no language axis (LibriSpeech). */
  W.axisFor = function (rows) {
    const languages = [...new Set(rows.map(W.languageOf).filter(Boolean))].sort();
    const benchmarks = [...new Set(rows.map((row) => row.benchmark))].sort();
    if (benchmarks.length > 1 && languages.length <= 1) {
      return { mode: "benchmark", values: benchmarks };
    }
    return { mode: "language", values: languages };
  };

  W.columnOf = function (row, mode) {
    return mode === "benchmark" ? row.benchmark : W.languageOf(row);
  };

  /* The campaign / model-group / language filter every renderer applies. */
  W.visibleSlices = function (snapshot, state) {
    return snapshot.slices.filter(
      (slice) =>
        (!state.campaign || slice.campaign === state.campaign) &&
        (!state.modelFamily || snapshot.model_families[slice.model] === state.modelFamily) &&
        (!state.language ||
          slice.lang === state.language ||
          slice.src === state.language ||
          slice.tgt === state.language)
    );
  };

  W.escape = function (text) {
    return String(text === null || text === undefined ? "" : text).replace(/[&<>"']/g, (ch) => ENTITIES[ch]);
  };
})(window.Worklog);
