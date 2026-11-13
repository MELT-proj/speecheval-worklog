# Worklog View Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the worklog's matrix and heatmap readable for heterogeneous suites: per-benchmark heatmap columns where a family has no language axis, one heatmap tab per dataset, no rows for models that cannot do a task, and only the newest MELT/SMURF checkpoint by default.

**Architecture:** The scanner and store are untouched. `worklog/views.py` adds checkpoint metadata to the snapshot payload. Two static scripts consume it: `matrix.js` filters rows, `charts.js` renders dataset tabs and a variant-aware x-axis. `app.js` owns the two new URL-backed view flags (`showAll`, `heatmap`); `index.html` and `style.css` get the checkbox and tab styling.

**Tech Stack:** Python 3.10+ / Flask / pytest; vanilla JS; ECharts; Playwright for browser tests.

**Spec:** `docs/superpowers/specs/2026-11-13-worklog-view-fixes-design.md`

## Global Constraints

- Read-only app: no new writes, no store/schema changes.
- Snapshot is additive only; existing keys and their shapes stay unchanged.
- No new `worklog.yaml` keys; families, metrics and headline stay as configured.
- Python style: line length 100, type hints on signatures, no `from typing import`.
- Tests live in existing files (`tests/test_views.py`, `tests/test_ui.py`), no new test modules.
- Browser tests need `LD_LIBRARY_PATH=$(cat /tmp/opencode/pwlibs.path)` on artemis.
- URL state keys are `showAll=1` and `heatmap=<family>`; existing keys keep their names.

## Review Focus

- A family whose slices share one language must not put two values on one heatmap cell; LibriSpeech is the case, and the canvas data length proves it.
- A model that only ran ASR must vanish from the ST matrix and ST heatmap even though it has a finite metric elsewhere.
- Clicking a heatmap tab must keep every global filter in the URL and in the rendered views.
- The Show-all checkbox must round-trip: checking reveals old checkpoints, unchecking hides them, and `showAll=1` appears/disappears in the URL.
- A one-benchmark family (VoxPopuli) must keep language columns; only a multi-benchmark family switches to benchmark columns.

---

### Task 1: Checkpoint metadata in the snapshot

**Files:**
- Modify: `worklog/views.py`
- Test: `tests/test_views.py`

**Interfaces:**
- Consumes: `Sequence[str]` of model names, `dict[str, str]` mapping model → `model_family` group.
- Produces:
  - `checkpoint_of(model: str) -> tuple[str, int | None]`
  - `checkpoint_flags(models: Sequence[str], groups: dict[str, str]) -> dict[str, dict]`
  - snapshot key `checkpoints: {model: {"base": str, "step": int | None, "latest": bool}}`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_views.py`:

```python
def test_highest_step_is_the_latest_checkpoint(cfg):
    runs = [
        run(model="fondue-step-3000", provider="melt/fondue-step-3000"),
        run(model="fondue-step-26000", provider="melt/fondue-step-26000"),
        run(model="canary-1b-v2", provider="melt/hf/canary"),
    ]
    snap = build_snapshot(cfg, runs, [], "v1", "now")
    checkpoints = snap["checkpoints"]
    assert checkpoints["fondue-step-26000"] == {"base": "fondue", "step": 26000, "latest": True}
    assert checkpoints["fondue-step-3000"]["latest"] is False
    assert checkpoints["canary-1b-v2"] == {"base": "canary-1b-v2", "step": None, "latest": True}


def test_latest_is_computed_within_a_model_family(cfg):
    runs = [
        run(model="seed-step-5", provider="melt/seed-step-5"),
        run(model="seed-step-9", provider="smurf/seed-step-9"),
    ]
    snap = build_snapshot(cfg, runs, [], "v1", "now")
    assert snap["checkpoints"]["seed-step-5"]["latest"] is True
    assert snap["checkpoints"]["seed-step-9"]["latest"] is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/giuseppe/melt-proj/speecheval-worklog && /mnt/scratch-artemis/giuseppe/venvs/speecheval-worklog/bin/python -m pytest tests/test_views.py -q`
Expected: FAIL with `KeyError: 'checkpoints'`.

- [ ] **Step 3: Implement the helpers and snapshot key**

In `worklog/views.py`, add `import re` and below the imports:

```python
_STEP_RE = re.compile(r"^(?P<base>.+)-step-(?P<step>\d+)$")


def checkpoint_of(model: str) -> tuple[str, int | None]:
    """The base name and numeric step of ``<base>-step-<n>``; ``None`` without a step."""
    match = _STEP_RE.match(model)
    if match is None:
        return model, None
    return match.group("base"), int(match.group("step"))


def checkpoint_flags(models: Sequence[str], groups: dict[str, str]) -> dict[str, dict]:
    """Per-model base, step and whether it is the highest step of its group.

    Models without a step are always latest; a baseline that happens to carry a
    step suffix cannot displace a MELT or SMURF checkpoint, because the group is
    part of the key.
    """
    parsed = {model: checkpoint_of(model) for model in models}
    highest: dict[tuple[str, str], int] = {}
    for model, (base, step) in parsed.items():
        if step is None:
            continue
        key = (groups.get(model, "other"), base)
        highest[key] = max(highest.get(key, step), step)
    return {
        model: {
            "base": base,
            "step": step,
            "latest": step is None or step == highest[(groups.get(model, "other"), base)],
        }
        for model, (base, step) in parsed.items()
    }
```

In `build_snapshot`, replace the providers/model-families block with:

```python
    providers = {run.model: (run.provider or run.model) for run in runs}
    model_groups = {model: model_family(provider) for model, provider in providers.items()}
```

and add to the returned dict, after `"model_families": model_groups,`:

```python
        "checkpoints": checkpoint_flags(models, model_groups),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/giuseppe/melt-proj/speecheval-worklog && /mnt/scratch-artemis/giuseppe/venvs/speecheval-worklog/bin/python -m pytest tests/test_views.py -q`
Expected: PASS (all view tests).

- [ ] **Step 5: Commit**

```bash
cd /home/giuseppe/melt-proj/speecheval-worklog
git add worklog/views.py tests/test_views.py
git commit -m "worklog: snapshot checkpoint step and latest flag"
```

---

### Task 2: Drop unsupported tasks and old checkpoints from the matrix

**Files:**
- Modify: `worklog/static/util.js`, `worklog/static/matrix.js`, `worklog/static/app.js`, `worklog/static/index.html`, `worklog/static/style.css`
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: `snapshot.checkpoints` from Task 1; `snapshot.model_families`.
- Produces:
  - `W.modelVisible(snapshot, state, model, task) -> boolean`
  - URL flag `showAll=1`; `state.showAllCheckpoints: boolean`
  - DOM: `#show-all-checkpoints` checkbox in the matrix card header.

- [ ] **Step 1: Write the failing browser test**

Append to `tests/test_ui.py` (the imports and `make_log` are already there):

```python
def test_matrix_drops_old_checkpoints_and_unsupported_tasks(tmp_path):
    for step in (3000, 26000):
        make_log(
            tmp_path,
            campaign="speechval-fondue",
            model=f"fondue-step-{step}",
            eval_model=f"melt/fondue-step-{step}",
            benchmark="fleurs24-st-xen-dev",
            slice_id="st-fleurs-de_en",
            metrics={"corpus_bleu": 20.0 + step / 1000},
        )
    make_log(
        tmp_path,
        model="cohere-transcribe-03-2026",
        eval_model="melt/hf/cohere_transcribe",
        benchmark="voxpopuli-dev",
        slice_id="asr-voxpopuli-en-dev",
        task="asr",
        dataset_id="voxpopuli-en-dev",
        src=None,
        tgt=None,
        lang="en",
        metrics={"corpus_wer_raw": 0.1},
    )
    cfg = load_config(overrides={"roots": [str(tmp_path)], "include": ["*"], "cache": str(tmp_path / "w.sqlite")})
    store = Store(cfg.cache)
    store.sync([row for path in iter_log_paths(cfg) if (row := scan_run(path))])
    server = make_server("127.0.0.1", 0, create_app(cfg, store))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1500, "height": 1200})
            page.goto(f"http://127.0.0.1:{server.server_port}")
            snapshot = page.evaluate("() => fetch('/api/snapshot').then((r) => r.json())")
            page.evaluate(
                "([snap, state]) => window.Worklog.matrix.render(snap, state)",
                [snapshot, {"aggregate": "macro"}],
            )
            asr = page.locator(".matrix-block").nth(0).inner_text()
            st = page.locator(".matrix-block").nth(1).inner_text()
            assert "cohere-transcribe-03-2026" in asr
            assert "cohere-transcribe-03-2026" not in st
            assert "fondue-step-26000" in st
            assert "fondue-step-3000" not in st

            page.evaluate(
                "([snap, state]) => window.Worklog.matrix.render(snap, state)",
                [snapshot, {"aggregate": "macro", "showAllCheckpoints": True}],
            )
            assert "fondue-step-3000" in page.locator(".matrix-block").nth(1).inner_text()
            browser.close()
    finally:
        server.shutdown()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /home/giuseppe/melt-proj/speecheval-worklog && LD_LIBRARY_PATH=$(cat /tmp/opencode/pwlibs.path) /mnt/scratch-artemis/giuseppe/venvs/speecheval-worklog/bin/python -m pytest tests/test_ui.py::test_matrix_drops_old_checkpoints_and_unsupported_tasks -q`
Expected: FAIL on `"cohere-transcribe-03-2026" not in st` (the ST table currently has the all-dash row) or on the older checkpoint.

- [ ] **Step 3: Implement the shared visibility rule and use it in the matrix**

In `worklog/static/util.js`, add before the theme section:

```js
  /* A model shown in a task's results: it ran that task, it passes the group
     filter, and (unless Show all is ticked) it is the latest MELT/SMURF step. */
  W.modelVisible = function (snapshot, state, model, task) {
    const kind = snapshot.model_families[model] || "other";
    if (state.modelFamily && kind !== state.modelFamily) return false;
    if (task && !snapshot.slices.some((slice) => slice.model === model && slice.task === task)) return false;
    if (!state.showAllCheckpoints && (kind === "melt" || kind === "smurf")) {
      const info = (snapshot.checkpoints || {})[model];
      if (info && info.latest === false) return false;
    }
    return true;
  };
```

In `worklog/static/matrix.js`, replace the `models` computation in `renderTable`:

```js
    const models = snapshot.filters.models.filter((model) =>
      W.modelVisible(snapshot, state, model, task)
    );
```

In `worklog/static/index.html`, add the checkbox to the Results matrix card head:

```html
        <label class="check" for="show-all-checkpoints">
          <input type="checkbox" id="show-all-checkpoints"> Show all checkpoints
        </label>
```

In `worklog/static/style.css`, after the `.metric-select` rule:

```css
.check {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  margin-left: auto;
  color: var(--muted);
  font-size: 12px;
  cursor: pointer;
  white-space: nowrap;
}
.check input { accent-color: var(--accent); }
```

In `worklog/static/app.js`:

- In `readHash`, after the metric loop: `state.showAllCheckpoints = params.get("showAll") === "1";`
- In `writeHash`, after the metric loop: `if (state.showAllCheckpoints) params.set("showAll", "1");`
- In the Reset handler, add `state.showAllCheckpoints = false;`
- In `renderAll`, before `renderKpis()`:

```js
    const showAll = document.getElementById("show-all-checkpoints");
    if (showAll) showAll.checked = !!state.showAllCheckpoints;
```

- In `boot`, after the Refresh listener:

```js
    document.getElementById("show-all-checkpoints").addEventListener("change", (event) => {
      state.showAllCheckpoints = event.target.checked;
      writeHash();
      renderAll();
    });
```

- [ ] **Step 4: Extend the boot test with the checkbox round trip**

In `tests/test_ui.py::test_page_boots_with_kpis_heatmap_feed_and_filters`, after the metric assertions and before `page.click("#refresh")`, add:

```python
        page.check("#show-all-checkpoints")
        assert "showAll=1" in page.url
        page.uncheck("#show-all-checkpoints")
        assert "showAll=1" not in page.url
```

- [ ] **Step 5: Run the matrix and boot tests**

Run: `cd /home/giuseppe/melt-proj/speecheval-worklog && LD_LIBRARY_PATH=$(cat /tmp/opencode/pwlibs.path) /mnt/scratch-artemis/giuseppe/venvs/speecheval-worklog/bin/python -m pytest tests/test_ui.py -q`
Expected: PASS (all UI tests).

- [ ] **Step 6: Commit**

```bash
cd /home/giuseppe/melt-proj/speecheval-worklog
git add worklog/static/util.js worklog/static/matrix.js worklog/static/app.js \
  worklog/static/index.html worklog/static/style.css tests/test_ui.py
git commit -m "worklog: hide unsupported tasks and old checkpoints by default"
```

---

### Task 3: Heatmap dataset tabs and benchmark-variant columns

**Files:**
- Modify: `worklog/static/charts.js`, `worklog/static/index.html`, `worklog/static/style.css`, `worklog/static/app.js`
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: `W.modelVisible` and `snapshot.checkpoints` from Tasks 1–2.
- Produces:
  - DOM: `#heatmap-tabs`, one `button.tab` per family, active tab has `.active`.
  - URL flag `heatmap=<family>`; `state.heatmap: string`.
  - `W.app.setHeatmapFamily(family: string) -> void`.

- [ ] **Step 1: Extend the UI fixture and write the failing heatmap test**

In `tests/test_ui.py`, add these logs to the `live_server` fixture before the config is loaded (after the existing `make_log` calls):

```python
    for suffix, wer in (("clean", 0.02), ("other", 0.08)):
        make_log(
            tmp_path,
            benchmark=f"librispeech-validation-{suffix}",
            slice_id=f"asr-librispeech-validation-{suffix}",
            task="asr",
            dataset_id=f"librispeech-validation-{suffix}",
            src=None,
            tgt=None,
            lang="en",
            metrics={"corpus_wer_raw": wer},
        )
    make_log(
        tmp_path,
        model="cohere-transcribe-03-2026",
        eval_model="melt/hf/cohere_transcribe",
        benchmark="voxpopuli-dev",
        slice_id="asr-voxpopuli-en-dev",
        task="asr",
        dataset_id="voxpopuli-en-dev",
        src=None,
        tgt=None,
        lang="en",
        metrics={"corpus_wer_raw": 0.1},
    )
```

Append the test:

```python
def test_heatmap_has_tabs_and_variant_columns(live_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(live_server)
        page.wait_for_selector("#heatmap canvas")

        tabs = page.locator("#heatmap-tabs .tab").all_inner_texts()
        assert tabs == ["LibriSpeech", "VoxPopuli", "FLEURS XtoEN"]

        option = page.evaluate(
            "() => echarts.getInstanceByDom(document.getElementById('heatmap')).getOption()"
        )
        assert option["xAxis"][0]["data"] == ["clean", "other"]
        assert option["yAxis"][0]["data"] == ["canary-1b-v2"]
        assert len(option["series"][0]["data"]) == 2  # one value per benchmark, not overlaid

        page.select_option("#filter-language", "en")
        page.click("#heatmap-tabs .tab >> text=VoxPopuli")
        page.wait_for_function(
            "() => document.getElementById('heatmap-hint').textContent.includes('VoxPopuli')"
        )
        assert "heatmap=VoxPopuli" in page.url
        assert "language=en" in page.url
        option = page.evaluate(
            "() => echarts.getInstanceByDom(document.getElementById('heatmap')).getOption()"
        )
        assert option["xAxis"][0]["data"] == ["en"]
        assert len(option["series"][0]["data"]) == 2  # canary and cohere

        page.click("#heatmap-tabs .tab >> text=FLEURS XtoEN")
        page.wait_for_function(
            "() => document.getElementById('heatmap-hint').textContent.includes('FLEURS XtoEN')"
        )
        option = page.evaluate(
            "() => echarts.getInstanceByDom(document.getElementById('heatmap')).getOption()"
        )
        assert option["yAxis"][0]["data"] == ["canary-1b-v2"]  # cohere has no ST
        browser.close()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /home/giuseppe/melt-proj/speecheval-worklog && LD_LIBRARY_PATH=$(cat /tmp/opencode/pwlibs.path) /mnt/scratch-artemis/giuseppe/venvs/speecheval-worklog/bin/python -m pytest tests/test_ui.py::test_heatmap_has_tabs_and_variant_columns -q`
Expected: FAIL — `#heatmap-tabs` does not exist.

- [ ] **Step 3: Add the tab bar markup and styles**

In `worklog/static/index.html`, between the heatmap card head and `#heatmap`:

```html
        <div id="heatmap-tabs" class="tabs" role="tablist" aria-label="Heatmap dataset"></div>
```

In `worklog/static/style.css`, after the `.heatmap` rule:

```css
.tabs { display: flex; flex-wrap: wrap; gap: 6px; padding: 10px 16px 0; }
.tab {
  border: 1px solid var(--line);
  background: transparent;
  color: var(--muted);
  border-radius: 999px;
  padding: 4px 11px;
  font: inherit;
  font-size: 11.5px;
  cursor: pointer;
}
.tab:hover { color: var(--text); border-color: #3b4a61; }
.tab.active { color: var(--bg); background: var(--accent); border-color: var(--accent); font-weight: 600; }
```

- [ ] **Step 4: Rewrite the heatmap selection and axis**

In `worklog/static/charts.js`, replace `taskForFamily`, `activeTask` and `activeFamily` with:

```js
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
```

In `render`, replace the block from `const slices = visible(...)` through the `const languages = ...` line with:

```js
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
    const models = snapshot.filters.models.filter((model) =>
      W.modelVisible(snapshot, state, model, task)
    );
    const axis = axisFor(rows);
    if (!rows.length || !axis.values.length) {
      el.innerHTML = '<div class="placeholder">No per-language results for this family yet.</div>';
      return;
    }
```

Replace the `data` construction with cell-averaging:

```js
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
```

Change the x-axis data and tooltip to use the column labels:

```js
        data: axis.values.map((value) => columnLabel(axis, value)),
```

```js
          const column = columnLabel(axis, axis.values[item.value[0]]);
          return `<b>${W.escape(model)}</b><br/>${W.escape(column)} · ${W.escape(rule.label)}: ${value}`;
```

At the end of `render`, keep the hint but use the selected family (it already does with the new `family` variable). Remove the now-unused `activeTask`/`activeFamily` references.

- [ ] **Step 5: Wire the tab state in the app**

In `worklog/static/app.js`:

- In `readHash`, after the filters loop: `state.heatmap = params.get("heatmap") || "";`
- In `writeHash`, after the metric loop: `if (state.heatmap) params.set("heatmap", state.heatmap);`
- In the Reset handler, add `state.heatmap = "";`
- In `W.app`, add:

```js
    setHeatmapFamily(family) {
      state.heatmap = family;
      writeHash();
      if (snapshot) W.charts.render(snapshot, state);
    },
```

- [ ] **Step 6: Run the heatmap and full UI tests**

Run: `cd /home/giuseppe/melt-proj/speecheval-worklog && LD_LIBRARY_PATH=$(cat /tmp/opencode/pwlibs.path) /mnt/scratch-artemis/giuseppe/venvs/speecheval-worklog/bin/python -m pytest tests/test_ui.py -q`
Expected: PASS (all UI tests).

- [ ] **Step 7: Commit**

```bash
cd /home/giuseppe/melt-proj/speecheval-worklog
git add worklog/static/charts.js worklog/static/index.html worklog/static/style.css \
  worklog/static/app.js tests/test_ui.py
git commit -m "worklog: heatmap dataset tabs and benchmark-variant columns"
```

---

### Task 4: Full verification

**Files:**
- No production changes expected; fix anything the checks expose in the owning file.

- [ ] **Step 1: Run the whole suite with browser libraries**

Run: `cd /home/giuseppe/melt-proj/speecheval-worklog && LD_LIBRARY_PATH=$(cat /tmp/opencode/pwlibs.path) /mnt/scratch-artemis/giuseppe/venvs/speecheval-worklog/bin/python -m pytest tests -q`
Expected: all tests pass (baseline was 48 before this work; new tests raise the count).

- [ ] **Step 2: Run the linter**

Run: `cd /home/giuseppe/melt-proj/speecheval-worklog && /mnt/scratch-artemis/giuseppe/venvs/speecheval-worklog/bin/python -m ruff check worklog tests`
Expected: `All checks passed!`

- [ ] **Step 3: Look at the live page**

The app is already served at `http://127.0.0.1:8765`. Confirm by eye:
- LibriSpeech heatmap has two columns, `clean` and `other`, no overlapping text.
- The heatmap has a tab per dataset and clicking one does not change the matrix columns.
- The ST matrix has no Cohere row; ticking Show all checkpoints reveals `fondue-step-3000`.
- The default matrix shows `fondue-step-26000` and hides `fondue-step-3000`.

If the running process predates the change, restart it:

```bash
cd /home/giuseppe/melt-proj/speecheval-worklog
/mnt/scratch-artemis/giuseppe/venvs/speecheval-worklog/bin/python app.py --port 8765
```

- [ ] **Step 4: Commit any fix from verification**

```bash
git add -A
git commit -m "worklog: verification fixes"
```

Only if Step 1–3 required a change; otherwise skip.

---

## Self-review notes

- Spec section 1 is Task 3 (axis + averaging), section 2 is Tasks 2 and 3 (`W.modelVisible` in both), section 3 is Task 3 (tabs + URL), section 4 is Tasks 1 and 2 (metadata + checkbox).
- `state.showAllCheckpoints` and `state.heatmap` are the only new state names; both appear in `app.js` and in the tests.
- The plan does not add a backend endpoint or change `worklog.yaml`.
