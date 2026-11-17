# Common-languages filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One checkbox per task that restricts the matrix, the heatmap and the cell drill-down to the languages (or benchmark variants) every visible model has a result on, and recomputes the aggregates on that common set.

**Architecture:** Five front-end-only changes over the existing snapshot. The column logic that today lives privately in `charts.js` moves into `util.js` together with a new intersection function; `matrix.js` and `charts.js` consume it when the task's box is ticked. No server, scanner, store or payload change.

**Tech Stack:** plain ES2017 in the `W` namespace (no build step), ECharts for the heatmap, Playwright for the browser tests, Flask app untouched.

**Spec:** `docs/superpowers/specs/2026-11-17-common-languages-design.md`

## Global Constraints

- No new runtime dependency, no build step, no framework; every front-end symbol hangs off the single `window.Worklog` (`W`) namespace.
- No change to `worklog/views.py`, `worklog/scanner.py`, `worklog/store.py`, `worklog/app.py` or the snapshot payload shape.
- The app stays read-only; the only persisted state for this feature is the URL hash.
- Anything interpolated into `innerHTML` goes through `W.escape`.
- Intersection is **per family**, over **participating visible models** (models with at least one row in that family), and "has a result" means a finite value for **the metric currently selected**.
- Label text: `common languages only`. Hash keys: `common-asr`, `common-st`. Checkbox ids: `common-asr`, `common-st`.
- Browser tests need the system libraries: `LD_LIBRARY_PATH=$(cat /tmp/opencode/pwlibs.path) pytest tests/test_ui.py -q`.
- Python edits keep ruff's line length 119 and no `typing` imports.

## Review Focus

1. **Tick the box with no common column left** — the block header (and therefore the checkbox the reader needs to untick it) must still render, with a sentence explaining the emptiness instead of an all-`–` table.
2. **A model that ran the family but scored nothing on the selected metric** — it must empty the family rather than be silently dropped from the comparison.
3. **Ticking ASR must not disturb ST** — independent per-task state, independent sets.
4. **The heatmap must not show a column the table excluded**, and its family tab must survive the shrink.
5. **A model that never ran the family must not veto it** — cohere has no ST rows and must not empty every ST table.

---

### Task 1: Shared column helpers and one visible-slices predicate

**Files:**
- Modify: `worklog/static/util.js` (append before `W.escape`)
- Modify: `worklog/static/charts.js:7-12`, `:14-24`, `:34-54`, `:73-84`
- Modify: `worklog/static/matrix.js:37-47`
- Test: `tests/test_ui.py` (new test at the end)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `W.visibleSlices(snapshot, state) -> Array<row>` — rows passing the campaign, model-group and language filters.
  - `W.languageOf(row) -> string | null`
  - `W.axisFor(rows) -> {mode: "language" | "benchmark", values: string[]}` — `values` sorted ascending.
  - `W.columnOf(row, mode) -> string | null`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ui.py`:

```python
def test_column_helpers_are_shared(live_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(live_server)
        page.wait_for_function("window.Worklog && window.Worklog.axisFor")
        assert page.evaluate(
            """() => window.Worklog.axisFor([
                 {benchmark: 'librispeech-validation-clean', lang: 'en'},
                 {benchmark: 'librispeech-validation-other', lang: 'en'},
               ])"""
        ) == {
            "mode": "benchmark",
            "values": ["librispeech-validation-clean", "librispeech-validation-other"],
        }
        assert page.evaluate(
            """() => window.Worklog.axisFor([
                 {benchmark: 'voxpopuli-dev', lang: 'de'},
                 {benchmark: 'voxpopuli-dev', lang: 'en'},
               ])"""
        ) == {"mode": "language", "values": ["de", "en"]}
        assert page.evaluate(
            """() => {
                 const W = window.Worklog;
                 const row = {lang: null, direction: 'xen', src: 'de', tgt: 'en'};
                 return [W.languageOf(row), W.columnOf(row, 'language')];
               }"""
        ) == ["de", "de"]
        browser.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `LD_LIBRARY_PATH=$(cat /tmp/opencode/pwlibs.path) pytest tests/test_ui.py::test_column_helpers_are_shared -q`
Expected: FAIL — `page.wait_for_function` times out because `window.Worklog.axisFor` is undefined.

- [ ] **Step 3: Move the helpers into `util.js` and point both renderers at them**

Append to `util.js` (before `W.escape`), copying the exact bodies from `charts.js`:

```js
  /* A slice's column within its family: its language, or its benchmark when the
     family has no language axis (LibriSpeech). */
  W.languageOf = function (row) { /* charts.js languageOf, moved verbatim */ };
  W.axisFor = function (rows) { /* charts.js axisFor, moved verbatim */ };
  W.columnOf = function (row, mode) { /* charts.js columnOf, moved verbatim */ };
  /* The campaign / model-group / language filter both renderers apply. */
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
```

In `charts.js`, delete `languageOf` and its three call sites, `visible`, and the duplicated predicate inside `familiesWithResults`; call `W.languageOf`, `W.axisFor`, `W.columnOf` and `W.visibleSlices` instead. `commonPrefix` and `variantLabel` stay in `charts.js` — they are display-only. In `matrix.js`, `visibleSlices` becomes a one-line wrapper over `W.visibleSlices` (or its two call sites are replaced).

- [ ] **Step 4: Run the tests to verify the refactor is behaviour-neutral**

Run: `LD_LIBRARY_PATH=$(cat /tmp/opencode/pwlibs.path) pytest tests/test_ui.py -q`
Expected: PASS — the new test plus the ten existing browser tests.

- [ ] **Step 5: Commit**

```bash
git add worklog/static/util.js worklog/static/charts.js worklog/static/matrix.js tests/test_ui.py
git commit -m "refactor: share the column helpers between matrix and heatmap"
```

---

### Task 2: The intersection itself

**Files:**
- Modify: `worklog/static/util.js` (after `W.visibleSlices`)
- Test: `tests/test_ui.py` (new test at the end)

**Interfaces:**
- Consumes: `W.visibleSlices`, `W.axisFor`, `W.columnOf`, `W.modelVisible` (Task 1 and `util.js`).
- Produces:
  - `W.commonColumns(snapshot, state, task, metric) -> Map<familyName, {mode: string, columns: Set<string>}>`
  - `W.keepsRow(common, row) -> boolean` — `true` when the row's family/column survived (`false` for a family absent from the map).

- [ ] **Step 1: Write the failing test**

```python
def test_common_columns_intersects_participating_models(live_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(live_server)
        page.wait_for_function("window.Worklog && window.Worklog.commonColumns")
        result = page.evaluate(
            """() => {
                 const W = window.Worklog;
                 const slice = (model, lang, wer) => ({
                   model, task: 'asr', family: 'VoxPopuli', benchmark: 'voxpopuli-dev',
                   lang, src: null, tgt: null, samples: 10,
                   metrics: wer === null ? {} : {corpus_wer_raw: wer},
                 });
                 const snapshot = {
                   slices: [
                     slice('a', 'en', 0.1), slice('a', 'de', 0.2), slice('a', 'fr', 0.3),
                     slice('b', 'en', 0.1), slice('b', 'de', 0.2),
                     {model: 'c', task: 'asr', family: 'LibriSpeech', benchmark: 'librispeech-validation-clean',
                      lang: 'en', src: null, tgt: null, samples: 10, metrics: {corpus_wer_raw: 0.05}},
                   ],
                   filters: {models: ['a', 'b', 'c'], families: ['LibriSpeech', 'VoxPopuli']},
                   model_families: {a: 'baseline', b: 'baseline', c: 'baseline'},
                   checkpoints: {},
                 };
                 const common = W.commonColumns(snapshot, {aggregate: 'macro'}, 'asr', 'corpus_wer_raw');
                 const entry = common.get('VoxPopuli');
                 return {mode: entry.mode, columns: [...entry.columns].sort(),
                         kept: snapshot.slices.filter((row) => W.keepsRow(common, row)).length};
               }"""
        )
        assert result == {"mode": "language", "columns": ["de", "en"], "kept": 5}
        browser.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `LD_LIBRARY_PATH=$(cat /tmp/opencode/pwlibs.path) pytest tests/test_ui.py::test_common_columns_intersects_participating_models -q`
Expected: FAIL — `window.Worklog.commonColumns` is undefined.

- [ ] **Step 3: Implement both functions in `util.js`**

`commonColumns` groups `W.visibleSlices(snapshot, state)` rows of that `task` by family, fixes each family's `mode` with `W.axisFor` **on the unfiltered rows**, takes the participating models (`snapshot.filters.models` filtered by `W.modelVisible(snapshot, state, model, task)` that have at least one row in the family), and intersects, per model, the set of `W.columnOf(row, mode)` values whose `row.metrics[metric]` is finite. A family with no participating model gets an empty `columns` set. `keepsRow` looks the row's family up and tests `columns.has(W.columnOf(row, entry.mode))`.

- [ ] **Step 4: Run test to verify it passes**

Run: `LD_LIBRARY_PATH=$(cat /tmp/opencode/pwlibs.path) pytest tests/test_ui.py::test_common_columns_intersects_participating_models -q`
Expected: PASS. `c` is not participating in VoxPopuli, so it does not veto `fr`.

- [ ] **Step 5: Commit**

```bash
git add worklog/static/util.js tests/test_ui.py
git commit -m "feat: intersect the columns every visible model has a result on"
```

---

### Task 3: The matrix checkbox, the hash state and the shrunken cells

**Files:**
- Modify: `worklog/static/app.js:6-41`, `:81-95`, `:177-190`, `:193-204`
- Modify: `worklog/static/matrix.js:62-98`, `:100-157`
- Modify: `worklog/static/style.css` (after `.metric-select`, line ~210)
- Test: `tests/test_ui.py` (fixture `coverage_server` plus three tests)

**Interfaces:**
- Consumes: `W.commonColumns`, `W.keepsRow`, `W.visibleSlices` (Tasks 1–2).
- Produces:
  - `state.common = {[task]: boolean}`; hash `common-<task>=1`.
  - `W.app.setCommon(task, on) -> void` — sets, writes the hash, re-renders.
  - DOM: `input#common-<task>` inside `div.matrix-controls` next to the metric `select`.

- [ ] **Step 1: Add the fixture and write the failing tests**

```python
@pytest.fixture
def coverage_server(tmp_path):
    asr = (("en", 0.10, "10:00"), ("de", 0.30, "10:10"), ("fr", 0.30, "10:20"))
    for lang, wer, at in asr:
        make_log(
            tmp_path,
            campaign="speechval-coverage",
            benchmark="voxpopuli-dev",
            slice_id=f"asr-{lang}",
            task="asr",
            dataset_id=f"voxpopuli-{lang}-dev",
            src=None,
            tgt=None,
            lang=lang,
            metrics={"corpus_wer_raw": wer},
            created=f"2026-11-12T{at}:00+00:00",
        )
    for lang, bleu, at in (("de", 20.0, "11:00"), ("fr", 30.0, "11:10")):
        make_log(
            tmp_path,
            campaign="speechval-coverage",
            benchmark="fleurs24-st-xen-dev",
            slice_id=f"st-fleurs-{lang}_en",
            dataset_id=f"fleurs-{lang}_en",
            src=lang,
            tgt="en",
            metrics={"corpus_bleu": bleu},
            created=f"2026-11-12T{at}:00+00:00",
        )
    for lang, wer, at in (("en", 0.05, "12:00"), ("de", 0.15, "12:10")):
        make_log(
            tmp_path,
            campaign="speechval-coverage",
            model="cohere-transcribe-03-2026",
            eval_model="melt/hf/cohere_transcribe",
            benchmark="voxpopuli-dev",
            slice_id=f"asr-{lang}",
            task="asr",
            dataset_id=f"voxpopuli-{lang}-dev",
            src=None,
            tgt=None,
            lang=lang,
            metrics={"corpus_wer_raw": wer},
            created=f"2026-11-12T{at}:00+00:00",
        )
    make_log(
        tmp_path,
        campaign="speechval-coverage",
        model="empty-baseline",
        eval_model="melt/hf/empty",
        benchmark="fleurs24-st-xen-dev",
        slice_id="st-fleurs-it_en",
        dataset_id="fleurs-it_en",
        src="it",
        tgt="en",
        metrics={},
        created="2026-11-12T13:00:00+00:00",
    )
    cfg = load_config(
        overrides={"roots": [str(tmp_path)], "include": ["*"], "cache": str(tmp_path / "w.sqlite")}
    )
    store = Store(cfg.cache)
    store.sync([row for path in iter_log_paths(cfg) if (row := scan_run(path))])
    server = make_server("127.0.0.1", 0, create_app(cfg, store))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_common_languages_shrinks_matrix_cells(coverage_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(coverage_server)
        page.wait_for_function("window.Worklog && window.Worklog.matrix")
        snapshot = page.evaluate("() => fetch('/api/snapshot').then((r) => r.json())")
        page.evaluate(
            "([snap, state]) => window.Worklog.matrix.render(snap, state)",
            [snapshot, {"aggregate": "macro"}],
        )
        asr = page.locator(".matrix-block").nth(0)
        st_before = page.locator(".matrix-block").nth(1).inner_text()
        assert "23.33" in asr.inner_text()  # mean of 0.10, 0.30, 0.30
        assert "3/3" in asr.inner_text()
        page.evaluate(
            "([snap, state]) => window.Worklog.matrix.render(snap, state)",
            [snapshot, {"aggregate": "macro", "common": {"asr": True}}],
        )
        asr = page.locator(".matrix-block").nth(0)
        assert "20.00" in asr.inner_text()  # en + de only
        assert "2/2" in asr.inner_text()
        assert "23.33" not in asr.inner_text()
        assert page.locator(".matrix-block").nth(1).inner_text() == st_before
        browser.close()


def test_common_languages_toggle_persists_and_resets(coverage_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(coverage_server)
        page.wait_for_selector("#common-asr")
        page.check("#common-asr")
        page.wait_for_function(
            "() => document.getElementById('matrix').innerText.includes('20.00')"
        )
        assert "common-asr=1" in page.url
        page.reload()
        page.wait_for_selector("#common-asr")
        assert page.is_checked("#common-asr")
        page.click("#filters >> text=Reset")
        page.wait_for_function("() => !location.hash.includes('common-asr')")
        assert not page.is_checked("#common-asr")
        assert "23.33" in page.locator(".matrix-block").nth(0).inner_text()
        browser.close()


def test_common_languages_empty_state_keeps_the_control(coverage_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(coverage_server)
        page.wait_for_function("window.Worklog && window.Worklog.matrix")
        snapshot = page.evaluate("() => fetch('/api/snapshot').then((r) => r.json())")
        page.evaluate(
            "([snap, state]) => window.Worklog.matrix.render(snap, state)",
            [snapshot, {"aggregate": "macro", "common": {"st": True}}],
        )
        st = page.locator(".matrix-block").nth(1)
        assert "every visible model" in st.inner_text()
        assert page.locator("#common-st").is_checked()
        browser.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `LD_LIBRARY_PATH=$(cat /tmp/opencode/pwlibs.path) pytest tests/test_ui.py -q -k common_languages`
Expected: FAIL — no `#common-asr`, and the rendered cells ignore `state.common`.

- [ ] **Step 3: Implement the checkbox, the state and the filtering**

`app.js`: in `readHash`, loop over the hash entries and set `state.common[key.slice("common-".length)] = true` for keys starting with `common-` (mirroring the `metric-` loop); in `writeHash`, emit `common-<task>=1` for each truthy entry; in `Reset`, `state.common = {}`; export

```js
    setCommon(task, on) {
      if (!state.common) state.common = {};
      state.common[task] = !!on;
      writeHash();
      renderAll();
    },
```

`matrix.js`: in `renderTable`, after `slices` and `models` are computed, wrap the header controls in `div.matrix-controls` holding the existing `select` plus a new `label.check` with `input#common-<task>` (checked from `state.common && state.common[task]`, `change` → `W.app.setCommon(task, event.target.checked)`, `title` explaining that LibriSpeech uses its benchmark variants as columns). When `state.common && state.common[task]`, compute `const common = W.commonColumns(snapshot, state, task, metric)` once and filter `slices` through `W.keepsRow(common, slice)`. If every family of that task has an empty `columns` set — and a family absent from the map counts as empty — replace the `<table>` with a `<div class="placeholder">` whose text contains `every visible model`; the header (and so the checkbox) still renders.

`style.css`: `.matrix-controls { display: flex; align-items: center; gap: 10px; }` and `.matrix-controls .check { margin-left: 0; }`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `LD_LIBRARY_PATH=$(cat /tmp/opencode/pwlibs.path) pytest tests/test_ui.py -q -k common_languages`
Expected: PASS (three tests).

- [ ] **Step 5: Commit**

```bash
git add worklog/static/app.js worklog/static/matrix.js worklog/static/style.css tests/test_ui.py
git commit -m "feat: common-languages checkbox for the results matrix"
```

---

### Task 4: The heatmap follows the same set

**Files:**
- Modify: `worklog/static/charts.js:119-153`, `:256-258`
- Test: `tests/test_ui.py` (two tests, reusing `coverage_server`)

**Interfaces:**
- Consumes: `W.commonColumns`, `W.keepsRow`, `W.visibleSlices`, `state.common`.
- Produces: nothing new — the x-axis, its cells and the hint.

- [ ] **Step 1: Write the failing tests**

```python
def test_common_languages_shrinks_heatmap(coverage_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(coverage_server)
        page.wait_for_function("window.Worklog && window.Worklog.charts")
        snapshot = page.evaluate("() => fetch('/api/snapshot').then((r) => r.json())")
        page.evaluate(
            "([snap, state]) => window.Worklog.charts.render(snap, state)",
            [snapshot, {"aggregate": "macro", "heatmap": "VoxPopuli"}],
        )
        option = page.evaluate(
            "() => echarts.getInstanceByDom(document.getElementById('heatmap')).getOption()"
        )
        assert option["xAxis"][0]["data"] == ["de", "en", "fr"]
        page.evaluate(
            "([snap, state]) => window.Worklog.charts.render(snap, state)",
            [snapshot, {"aggregate": "macro", "heatmap": "VoxPopuli", "common": {"asr": True}}],
        )
        option = page.evaluate(
            "() => echarts.getInstanceByDom(document.getElementById('heatmap')).getOption()"
        )
        assert option["xAxis"][0]["data"] == ["de", "en"]
        assert "common only" in page.inner_text("#heatmap-hint")
        assert page.locator("#heatmap-tabs .tab").count() == 3
        browser.close()


def test_common_languages_heatmap_explains_an_empty_axis(coverage_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(coverage_server)
        page.wait_for_function("window.Worklog && window.Worklog.charts")
        snapshot = page.evaluate("() => fetch('/api/snapshot').then((r) => r.json())")
        page.evaluate(
            "([snap, state]) => window.Worklog.charts.render(snap, state)",
            [snapshot, {"aggregate": "macro", "heatmap": "FLEURS XtoEN", "common": {"st": True}}],
        )
        assert "every visible model" in page.inner_text("#heatmap")
        assert page.locator("#heatmap canvas").count() == 0
        browser.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `LD_LIBRARY_PATH=$(cat /tmp/opencode/pwlibs.path) pytest tests/test_ui.py -q -k "common_languages and heatmap"`
Expected: FAIL — the axis still lists `fr`, and the empty case draws a chart.

- [ ] **Step 3: Filter the axis and the rows, and say why**

In `charts.js` `render`, once `metric` and `rows` are known: when `state.common && state.common[task]`, compute `const common = W.commonColumns(snapshot, state, task, metric)`, keep `rows` through `W.keepsRow`, and filter `axis.values` to the family's kept `columns` (preserving the sorted order). `axis.mode` stays as computed from the unfiltered rows. When the filtered `axis.values` is empty, call `showPlaceholder` with a message containing `every visible model` instead of drawing. Append ` · common only` to the hint text while the box is ticked.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `LD_LIBRARY_PATH=$(cat /tmp/opencode/pwlibs.path) pytest tests/test_ui.py -q -k "common_languages and heatmap"`
Expected: PASS (two tests).

- [ ] **Step 5: Commit**

```bash
git add worklog/static/charts.js tests/test_ui.py
git commit -m "feat: restrict the heatmap to the common columns"
```

---

### Task 5: Drill-down, and the design note

**Files:**
- Modify: `worklog/static/matrix.js:212-247`
- Modify: `docs/design.md` (new `### B5b` after §B5, before §B6)
- Test: `tests/test_ui.py` (one test, reusing `coverage_server`)

**Interfaces:**
- Consumes: `W.commonColumns`, `W.keepsRow`, `state.common`.
- Produces: the drill-down header `"<kept> of <total> languages · common only"` (`variants` instead of `languages` in benchmark mode) — no new exported symbol.

- [ ] **Step 1: Write the failing test**

```python
def test_common_languages_drilldown_reports_the_shrink(coverage_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(coverage_server)
        page.wait_for_function("window.Worklog && window.Worklog.matrix")
        snapshot = page.evaluate("() => fetch('/api/snapshot').then((r) => r.json())")
        page.evaluate(
            """([snap, state]) => window.Worklog.matrix.drill(
                 snap, state, 'canary-1b-v2', 'VoxPopuli', 'asr', 'corpus_wer_raw')""",
            [snapshot, {"aggregate": "macro", "common": {"asr": True}}],
        )
        page.wait_for_selector("#modal[open]")
        body = page.inner_text("#modal-body")
        assert "2 of 3 languages" in body
        assert "common only" in body
        assert page.locator("#modal-body tbody tr").count() == 2
        browser.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `LD_LIBRARY_PATH=$(cat /tmp/opencode/pwlibs.path) pytest tests/test_ui.py::test_common_languages_drilldown_reports_the_shrink -q`
Expected: FAIL — the header is the current `N slice(s) · …` line and three rows are listed.

- [ ] **Step 3: Filter the drill-down list and report both counts**

In `drill`, keep `rows` as today for the total, then when `state.common && state.common[task]` compute `const common = W.commonColumns(snapshot, state, task, metric)` and filter the listed rows through `W.keepsRow(common, row)`. Replace the hint line with `<kept> of <total> languages · <rule.label>` plus ` · common only` when filtered (`variants` when the family's `common` entry has `mode === "benchmark"`). Then add to `docs/design.md`, after §B5:

```markdown
### B5b. Common-languages filter

Each task table carries a "common languages only" checkbox. Ticked, the cell
aggregates are recomputed over the columns (languages, or benchmark variants
for LibriSpeech) every visible model of that family scored at the selected
metric; the heatmap axis and the drill-down list follow the same set. The
intersection is per family, because one global ASR intersection would reduce to
English and delete VoxPopuli. A model that never ran a benchmark does not veto
it, and a column with a pending metric is not common. The cell label shows
`k/N` on the reduced set, and an empty intersection is reported as such rather
than drawn.
```

- [ ] **Step 4: Run the full suite**

Run: `LD_LIBRARY_PATH=$(cat /tmp/opencode/pwlibs.path) pytest tests -q`
Expected: PASS — every test, including the eight new browser tests and the existing ones.

- [ ] **Step 5: Commit**

```bash
git add worklog/static/matrix.js docs/design.md tests/test_ui.py
git commit -m "feat: report the common-languages shrink in the drill-down"
```

---

## Manual check

With the server running (`python app.py --port 8765`), open the page on
`speechval-cohere` and tick ASR: the VoxPopuli cell's count should fall from
`7/14` to `7/7`, the heatmap should lose `ro hu cs fi hr sk sl`, and the
drill-down should read `7 of 14 languages · common only`. Unticking restores
the old numbers.
