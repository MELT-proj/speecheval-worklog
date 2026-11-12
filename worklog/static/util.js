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

  W.escape = function (text) {
    const div = document.createElement("div");
    div.textContent = text === null || text === undefined ? "" : String(text);
    return div.innerHTML;
  };
})(window.Worklog);
