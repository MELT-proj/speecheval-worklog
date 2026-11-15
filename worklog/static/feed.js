/* The run feed: newest logs first, errors included. */
window.Worklog = window.Worklog || {};

(function (W) {
  function chip(status) {
    const known = ["success", "error", "cancelled", "unknown"];
    const cls = known.includes(status) ? status : "unknown";
    return `<span class="chip ${cls}">${W.escape(status)}</span>`;
  }

  function render(snapshot) {
    const el = document.getElementById("feed");
    if (!el) return;
    const runs = snapshot.runs || [];
    if (!runs.length) {
      el.innerHTML = '<div class="placeholder">No runs yet. Drop a log in the source folder.</div>';
      return;
    }
    // The inspector reads `back` as a same-origin path (see safeBackUrl in
    // inspect.js), hence the "/" before the filter hash.
    el.innerHTML = runs
      .map((run) => {
        const duration = W.duration(run.created, run.finished);
        return `<a class="run" href="/inspect?run=${run.id}&back=${encodeURIComponent("/" + location.hash)}" title="${W.escape(run.path)}">
          ${chip(run.status)}
          <div>
            <div class="line1"><span class="model">${W.escape(run.model)}</span>
              · ${W.escape(run.slice || run.benchmark)}
              ${run.campaign ? `<span class="badge">${W.escape(run.campaign)}</span>` : ""}</div>
            <div class="line2">${W.when(run.finished || run.created)}${duration ? ` · ${duration}` : ""}</div>
            <div class="path">${W.escape(run.path)}</div>
          </div>
        </a>`;
      })
      .join("");
  }

  W.feed = { render };
})(window.Worklog);
