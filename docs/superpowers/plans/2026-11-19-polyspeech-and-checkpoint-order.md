# PolySpeech MCQA and Checkpoint Order Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Classify and render the PolySpeech-100 MCQA logs as a normal results table, and order MELT/Fondue and SMURF checkpoints by numeric step ascending when all checkpoints are shown.

**Architecture:** Keep the existing config-driven pipeline: the generic log scanner already extracts `audio_mcq` logs, so add the task/family/headline to `worklog.yaml` and teach the shared client renderers about it. Sort `snapshot.filters.models` server-side by model group, base checkpoint name and numeric step so every view inherits the order. Surface MCQ correctness in the run inspector from the score value the scorer already writes.

**Tech Stack:** Python 3.10+, Flask, plain JavaScript (no build), YAML config, pytest + Playwright.

**Spec:** `docs/superpowers/specs/2026-11-19-polyspeech-and-checkpoint-order-design.md`

## Global Constraints

- The app stays read-only; only the SQLite cache and digest sidecars are writable.
- No new runtime dependency and no JavaScript build step.
- No inspect-log schema change; the scanner already stores the needed numeric metrics.
- `ruff` clean; line length 119; Python builtins for typing (no `typing` imports).
- Tests: `/mnt/scratch-artemis/giuseppe/venvs/speecheval-worklog/bin/python -m pytest tests -q`.
- Browser tests need `LD_LIBRARY_PATH=$(cat /tmp/opencode/pwlibs.path)`.

## Review Focus

- Leading-zero and unpadded step names (`step-0600000`, `step-600000`, `step-3000`) must sort numerically, never lexicographically.
- Different checkpoint bases must not interleave; baselines stay alphabetical and before MELT/SMURF.
- MCQA slices have empty src/tgt but a `lang`; the heatmap must use one column per language, not one column per benchmark.
- `choice_accuracy` is a fraction 0–1; the matrix and heatmap must multiply by 100, not print the raw fraction.
- A resolved-unsuccessful MCQ sample (`resolved: 0`) must read “unresolved”, not “incorrect”.

---

### Task 1: Checkpoint step ordering

**Files:**
- Modify: `worklog/views.py` (import area and `build_snapshot`)
- Test: `tests/test_views.py`

**Interfaces:**
- Consumes: `checkpoint_of(model) -> (base, step | None)` and `model_family(provider) -> str`, both already in `worklog/views.py` / `worklog/scanner.py`.
- Produces: `_model_sort_key(model: str, group: str) -> tuple`, and `snapshot["filters"]["models"]` ordered by group, base, step, model.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_views.py`:

```python
def test_models_are_ordered_by_numeric_step_ascending(cfg):
    runs = [
        run(model="smurf-b-step-20", provider="smurf/x"),
        run(model="smurf-b-step-3", provider="smurf/x"),
        run(model="fondue-step-26000", provider="melt/fondue-step-26000"),
        run(model="fondue-step-3000", provider="melt/fondue-step-3000"),
        run(model="canary-1b-v2", provider="melt/hf/canary"),
    ]
    snap = build_snapshot(cfg, runs, [], "v1", "now")
    assert snap["filters"]["models"] == [
        "canary-1b-v2",
        "fondue-step-3000",
        "fondue-step-26000",
        "smurf-b-step-3",
        "smurf-b-step-20",
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_views.py::test_models_are_ordered_by_numeric_step_ascending -v`

Expected: FAIL, because the list is alphabetical (`fondue-step-26000` before `fondue-step-3000`).

- [ ] **Step 3: Implement `_model_sort_key` and use it**

In `worklog/views.py`, add near `checkpoint_of`:

```python
_GROUP_RANK = {"baseline": 0, "melt": 1, "smurf": 2, "other": 3}


def _model_sort_key(model: str, group: str) -> tuple:
    """Group, base checkpoint, numeric step, then name; steps ascend."""
    base, step = checkpoint_of(model)
    return (_GROUP_RANK.get(group, 3), base, -1 if step is None else step, model)
```

In `build_snapshot`, replace the current `models = sorted({run.model for run in runs})`
line and its surrounding provider block with:

```python
    providers = {run.model: (run.provider or run.model) for run in runs}
    model_groups = {model: model_family(provider) for model, provider in providers.items()}
    models = sorted(providers, key=lambda model: _model_sort_key(model, model_groups[model]))
```

Leave the later `checkpoint_flags(models, model_groups)` call unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_views.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worklog/views.py tests/test_views.py
git commit -m "feat: order checkpoints by numeric step"
```

---

### Task 2: Classify and render PolySpeech MCQA

**Files:**
- Modify: `worklog.yaml`
- Modify: `worklog/static/matrix.js` (`TASKS`, `isPercent`)
- Modify: `worklog/static/app.js` (`TASK_LABELS`)
- Modify: `worklog/static/charts.js` (percent check)
- Test: `tests/test_views.py`, `tests/test_ui.py`
- Test helper: `tests/helpers.py` (add `make_mcq_log`)

**Interfaces:**
- Consumes: the scanner row fields `task`, `benchmark`, `slice`, `lang`, `metrics`.
- Produces: config keys `metrics.choice_accuracy`, `metrics.unresolved_rate`, `headline.audio_mcq`, family `PolySpeech`; `snapshot["matrix"]["audio_mcq"]`; helper `make_mcq_log(base, *, ...) -> Path`.

- [ ] **Step 1: Write the failing Python tests**

Append to `tests/test_views.py`:

```python
def test_polyspeech_audio_mcq_is_a_classified_family(cfg):
    runs = [
        run(
            benchmark="polyspeech-100-pa_qt-200",
            slice="audio_mcq-polyspeech-pa_qt-de",
            task="audio_mcq",
            src=None,
            tgt=None,
            lang="de",
            metrics={"choice_accuracy": 0.465, "unresolved_rate": 0.0},
        )
    ]
    snap = build_snapshot(cfg, runs, [], "v1", "now")
    assert snap["slices"][0]["family"] == "PolySpeech"
    assert snap["matrix"]["audio_mcq"]["metric"] == "choice_accuracy"
    cell = next(c for c in snap["matrix"]["audio_mcq"]["cells"] if c["family"] == "PolySpeech")
    assert cell["value"] == 0.465
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_views.py::test_polyspeech_audio_mcq_is_a_classified_family -v`

Expected: FAIL with `KeyError: 'audio_mcq'` (and `family == "unclassified"`).

- [ ] **Step 3: Add the config**

In `worklog.yaml`, extend `families`, `metrics` and `headline`:

```yaml
  - {name: PolySpeech, task: audio_mcq, benchmarks: [polyspeech-100-pa_qt-200]}
```

```yaml
  choice_accuracy: {label: Accuracy, lower: false}
  unresolved_rate: {label: Unresolved, lower: true}
```

```yaml
  audio_mcq: choice_accuracy
```

- [ ] **Step 4: Run the Python test again**

Run: `pytest tests/test_views.py::test_polyspeech_audio_mcq_is_a_classified_family -v`

Expected: PASS.

- [ ] **Step 5: Write the failing browser test**

Add this fixture near the other server fixtures in `tests/test_ui.py`:

```python
@pytest.fixture
def mcqa_server(tmp_path):
    make_mcq_log(
        tmp_path,
        model="eurollm-speech-16n-step-0600000",
        eval_model="smurf//checkpoints/step=600000.ckpt",
        lang="de",
        correct=1,
        resolved=1,
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
```

Add the test:

```python
def test_audio_mcq_table_renders_accuracy_as_a_percentage(mcqa_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(mcqa_server)
        page.wait_for_function("window.Worklog && window.Worklog.matrix")
        snapshot = page.evaluate("() => fetch('/api/snapshot').then((r) => r.json())")
        page.evaluate(
            "([snap, state]) => window.Worklog.matrix.render(snap, state)",
            [snapshot, {"aggregate": "macro", "showAllCheckpoints": True}],
        )
        text = page.inner_text("#matrix")
        assert "MULTIPLE-CHOICE QA" in text.upper()
        assert "46.50" in text
        assert "UNCLASSIFIED" not in text.upper()
        assert snapshot["slices"][0]["family"] == "PolySpeech"
        assert snapshot["slices"][0]["lang"] == "de"
        browser.close()
```

- [ ] **Step 6: Run test to verify it fails**

Run: `LD_LIBRARY_PATH=$(cat /tmp/opencode/pwlibs.path) pytest tests/test_ui.py::test_audio_mcq_table_renders_accuracy_as_a_percentage -v`

Expected: FAIL with `ImportError` / `NameError` for `make_mcq_log`.

- [ ] **Step 7: Add the `make_mcq_log` helper**

At the end of `tests/helpers.py`:

```python
def make_mcq_log(
    base,
    *,
    model="eurollm-speech-16n-step-0600000",
    eval_model="smurf//checkpoints/step=600000.ckpt",
    benchmark="polyspeech-100-pa_qt-200",
    slice_id="audio_mcq-polyspeech-pa_qt-de",
    task="audio_mcq",
    lang="de",
    campaign="speechval-polyspeech",
    accuracy=0.465,
    unresolved=0.0,
    correct=1,
    resolved=1,
    samples=3,
    created="2026-11-19T01:00:00+00:00",
):
    """Write a minimal inspect log for the PolySpeech multiple-choice task."""
    body = {
        "version": 2,
        "status": "success",
        "eval": {
            "eval_id": slice_id,
            "created": created,
            "task": f"speech-{task}-{slice_id}",
            "task_args": {"dataset_id": f"polyspeech-pa_qt-{lang}", "lang": None},
            "dataset": {"name": benchmark, "location": "/frozen", "samples": samples},
            "model": eval_model,
            "metadata": {
                "melteval_model": model,
                "melteval_benchmark": benchmark,
                "melteval_slice": slice_id,
                "melteval_task": task,
                "melteval_campaign": campaign,
            },
        },
        "results": {
            "total_samples": samples,
            "completed_samples": samples,
            "scores": [{
                "name": "mcq_scorer",
                "metrics": {
                    "choice_accuracy": {"name": "choice_accuracy", "value": accuracy},
                    "unresolved_rate": {"name": "unresolved_rate", "value": unresolved},
                },
            }],
        },
        "stats": {"started_at": created, "completed_at": created},
        "samples": [
            {
                "id": f"09-{i:06d}",
                "epoch": 1,
                "input": "{audio_token}\nQuestion?",
                "target": "Correct option",
                "messages": [],
                "output": {"completion": "B. Correct option", "stop_reason": "stop"},
                "scores": {"mcq_scorer": {
                    "value": {"correct": correct, "resolved": resolved},
                    "answer": "B. Correct option",
                    "metadata": {"chosen": "Correct option", "reference": "Correct option"},
                    "history": [],
                }},
                "metadata": {"task": task, "lang": lang, "src_lang": "", "tgt_lang": ""},
                "total_time": 1.0,
                "working_time": 1.0,
                "turn_count": 1,
                "error_retries": [],
            }
            for i in range(samples)
        ],
    }
    folder = Path(base) / "logs" / model / benchmark / slice_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{created.replace(':', '-')}.json"
    path.write_text(json.dumps(body))
    return path.resolve()
```

- [ ] **Step 8: Run the browser test to see the display failure**

Run the command from Step 6 again.

Expected: FAIL because the QA table is missing, not because of `make_mcq_log`.

- [ ] **Step 9: Teach the renderers about `audio_mcq` and percentages**

In `worklog/static/matrix.js`:

```js
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
```

In `worklog/static/app.js`:

```js
  const TASK_LABELS = { asr: "ASR", st: "ST", audio_mcq: "QA" };
```

In `worklog/static/charts.js`, replace `const percent = metric.includes("wer") || metric.includes("cer");` with:

```js
    const percent =
      metric.includes("wer") ||
      metric.includes("cer") ||
      metric.includes("accuracy") ||
      metric.includes("unresolved");
```

- [ ] **Step 10: Run the browser test and the full suite**

Run: `LD_LIBRARY_PATH=$(cat /tmp/opencode/pwlibs.path) pytest tests/test_ui.py::test_audio_mcq_table_renders_accuracy_as_a_percentage -v`

Expected: PASS.

Run: `LD_LIBRARY_PATH=$(cat /tmp/opencode/pwlibs.path) pytest tests -q`

Expected: all tests pass.

- [ ] **Step 11: Commit**

```bash
git add worklog.yaml worklog/static/matrix.js worklog/static/app.js worklog/static/charts.js tests/helpers.py tests/test_views.py tests/test_ui.py
git commit -m "feat: show PolySpeech multiple-choice results"
```

---

### Task 3: MCQ outcome in the run inspector

**Files:**
- Modify: `worklog/static/inspect.js` (`renderSample`, add `outcomeOf`)
- Modify: `worklog/static/style.css` (outcome colours)
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: `sample.scores[*].value` as recorded by `worklog/samples.py` (already keeps the full score `value`).
- Produces: `outcomeOf(sample) -> {text: str, cls: str} | None`, and `.sample-outcome` markup in each card head.

- [ ] **Step 1: Write the failing browser test**

Add a fixture and test to `tests/test_ui.py`:

```python
@pytest.fixture
def mcq_inspector_server(tmp_path):
    path = make_mcq_log(tmp_path, samples=1, correct=0, resolved=1)
    store = Store(tmp_path / "w.sqlite")
    store.sync([_scan(path)])
    cfg = dataclasses.replace(load_config(), cache=tmp_path / "w.sqlite")
    app = create_app(cfg, store)
    server = make_server("127.0.0.1", 0, app)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", store.all()[0].row_id
    server.shutdown()


def test_inspector_shows_mcq_outcome_instead_of_wer(mcq_inspector_server):
    base, rid = mcq_inspector_server
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.goto(f"{base}/inspect?run={rid}")
        page.wait_for_selector("#samples .sample")
        outcome = page.inner_text("#samples .sample-outcome")
        assert "incorrect" in outcome.lower()
        assert page.locator("#samples .sample-wer").count() == 0
        browser.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `LD_LIBRARY_PATH=$(cat /tmp/opencode/pwlibs.path) pytest tests/test_ui.py::test_inspector_shows_mcq_outcome_instead_of_wer -v`

Expected: FAIL because `.sample-outcome` does not exist.

- [ ] **Step 3: Implement `outcomeOf` and use it in `renderSample`**

In `worklog/static/inspect.js`, above `renderSample`:

```js
  function outcomeOf(sample) {
    for (const entry of Object.values(sample.scores || {})) {
      const value = entry && entry.value;
      if (!value || typeof value !== "object" || !("correct" in value)) continue;
      if (value.resolved === 0) return { text: "unresolved", cls: "unresolved" };
      return value.correct
        ? { text: "\u2713 correct", cls: "correct" }
        : { text: "\u2717 incorrect", cls: "incorrect" };
    }
    return null;
  }
```

In `renderSample`, replace the fixed WER span with:

```js
    const outcome = outcomeOf(sample);
    const headMetric = outcome
      ? `<span class="sample-outcome ${outcome.cls}">${W.escape(outcome.text)}</span>`
      : `<span class="sample-wer">${W.escape(fmtPercent(sample.wer))}</span>`;
```

and use `${headMetric}` where `${W.escape(fmtPercent(sample.wer))}` was inside the `.sample-wer` span.

In `worklog/static/style.css`, next to `.sample-wer`:

```css
.sample-outcome { margin-left: auto; font-weight: 700; }
.sample-outcome.correct { color: var(--good); }
.sample-outcome.incorrect { color: var(--bad); }
.sample-outcome.unresolved { color: var(--warn); }
```

- [ ] **Step 4: Run the inspector test**

Run: `LD_LIBRARY_PATH=$(cat /tmp/opencode/pwlibs.path) pytest tests/test_ui.py::test_inspector_shows_mcq_outcome_instead_of_wer -v`

Expected: PASS.

- [ ] **Step 5: Add the unresolved regression test**

Append:

```python
def test_inspector_calls_an_unresolved_mcq_sample_unresolved(tmp_path):
    path = make_mcq_log(tmp_path, samples=1, correct=0, resolved=0)
    store = Store(tmp_path / "w.sqlite")
    store.sync([_scan(path)])
    cfg = dataclasses.replace(load_config(), cache=tmp_path / "w.sqlite")
    app = create_app(cfg, store)
    server = make_server("127.0.0.1", 0, app)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            page.goto(f"http://127.0.0.1:{server.server_port}/inspect?run={store.all()[0].row_id}")
            page.wait_for_selector("#samples .sample")
            assert "unresolved" in page.inner_text("#samples .sample-outcome").lower()
            browser.close()
    finally:
        server.shutdown()
```

Run: `LD_LIBRARY_PATH=$(cat /tmp/opencode/pwlibs.path) pytest tests/test_ui.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add worklog/static/inspect.js worklog/static/style.css tests/test_ui.py
git commit -m "feat: show MCQ outcomes in the run inspector"
```

---

### Task 4: Documentation and whole-branch verification

**Files:**
- Modify: `README.md` (task/format note)
- Test: no new test; run the suite and lint.

- [ ] **Step 1: Update the README result note**

In the paragraph that lists matrix behaviour, add one sentence:

```markdown
The same tables cover `audio_mcq` benchmarks (for example PolySpeech): accuracy
and unresolved rates render as percentages, and the drill-down and heatmap use
one column per language.
```

- [ ] **Step 2: Run the full suite and lint**

Run:

```bash
LD_LIBRARY_PATH=$(cat /tmp/opencode/pwlibs.path) pytest tests -q
ruff check .
```

Expected: all tests pass; `ruff` reports no issues.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: mention multiple-choice benchmark tables"
```
