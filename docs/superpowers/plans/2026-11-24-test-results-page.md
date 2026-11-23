# 🚀 Test Results Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `/test` page mirroring Results for test-split logs, with split classification driven entirely by `worklog.yaml` and the validation page bit-for-bit unchanged.

**Architecture:** Split is a pure function of a log's benchmark id against a new config set (`Config.test_benchmarks`). No scanner/store schema change: `app.py` filters the winning runs per split in Python and passes benchmark-set SQL filters to `Store.history`/`Store.count` for the feed and failed KPI; `views.py` matches matrix families per split; the page is the existing `index.html` + `app.js` detecting `/test` from the URL path.

**Tech Stack:** Flask app, SQLite store, vanilla JS + Playwright tests (repo has no build step; keep it that way).

**Spec:** `docs/superpowers/specs/2026-11-24-test-results-page-design.md` (this plan argues from it; read both). The benchmark ids it seeds come from melt-eval's `speechval-test` suite (`melt-eval/docs/superpowers/specs/2026-11-24-speechval-test-suite-design.md`).

## Global Constraints

- Read-only app; config-driven; no new runtime dependencies; no JS build step; password auth and loopback bind untouched.
- ruff line length 119; no `typing` imports; tests go in existing test files.
- Test runner: `/mnt/scratch-artemis/giuseppe/venvs/speecheval-worklog/bin/python -m pytest`; Playwright needs `env LD_LIBRARY_PATH="$(cat /tmp/opencode/pwlibs.path)"`.
- Branch `feature/test-page` off `master`.
- Every existing test keeps passing unchanged: the validation view's inputs must remain identical when no test campaign exists yet.
- `worklog.yaml` seed ids (family `test_benchmarks`, then the top-level feed-only list):
  - LibriSpeech: `librispeech-test-clean`, `librispeech-test-other`
  - VoxPopuli: `voxpopuli-test`
  - FLEURS XtoEN: `fleurs24-st-xen-test` · FLEURS ENtoX: `fleurs24-st-enx-test`
  - CoVoST2 XtoEN: `covost2-test` · CoVoST2 ENtoX: `covost2-enx-test`
  - Europarl XtoEN / ENtoX / other pairs: `europarl-st-test`
  - PolySpeech: none (stays validation-only)
  - Top-level feed-only: `mls-test`, `fleurs24-asr-test`, `mcif-chunked-asr`, `mcif-chunked-st`, `mcif-sfp`, `air-bench-chat-speech`

## Review Focus

Failure modes the spec implies that no task's happy path exercises — each pinned by a test in the task that owns it:

1. A benchmark id listed as **both** validation and test (the `covost2`/`covost2-dev` typo class) → must be a loud config-load `ValueError`, never a silent misclassification. Task 1.
2. A future test benchmark nobody has configured yet → lands on the validation page by default. That default is deliberate; pin it so a "fix" doesn't flip it silently. Task 3 (unknown-benchmark run stays validation).
3. `/test` before any test campaign exists → must render gracefully (zero KPIs, "not run" columns, no crash). Task 5's Playwright test runs against a fixture with no test logs.
4. `?split=<garbage>` → 400, never a silent fallback to validation. Task 4.
5. A test-only model (a checkpoint that only ran the test suite) leaking into the validation page's model list or KPIs via the derived fields. Task 4's API test asserts the leak cannot happen, because the filter sits on the input runs.

---

### Task 1: Config — split sets and validation

**Files:**
- Modify: `worklog/config.py` (`Family`, `load_config`)
- Modify: `worklog.yaml` (seed the lists)
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Family.test_benchmarks: tuple[str, ...] = ()`; `Config.test_benchmarks: frozenset[str]` — the union of every family's `test_benchmarks` and the new top-level `test_benchmarks:` list. Every later task consumes only `Config.test_benchmarks` and `Family.test_benchmarks`.

- [ ] **Step 1: Write the failing tests in `tests/test_config.py`**

- `test_test_benchmarks_parse`: a `tmp_path` YAML with one family carrying `test_benchmarks: [a-test]` and a top-level `test_benchmarks: [b-test]` parses into `Family.test_benchmarks == ("a-test",)` and `cfg.test_benchmarks == {"a-test", "b-test"}`.
- `test_seeded_test_ids`: `load_config()` (the repo's real `worklog.yaml`) has exactly the seed ids from Global Constraints in `cfg.test_benchmarks`, and the PolySpeech family has an empty `test_benchmarks`.
- `test_a_benchmark_cannot_be_validation_and_test`: a YAML listing the same id in a family's `benchmarks` and in any `test_benchmarks` (family or top-level) raises `ValueError` naming the id.

- [ ] **Step 2: Run them to verify they fail**

Run: `/mnt/scratch-artemis/giuseppe/venvs/speecheval-worklog/bin/python -m pytest tests/test_config.py -q`
Expected: FAIL — no such field.

- [ ] **Step 3: Implement in `worklog/config.py`**

Add `test_benchmarks` to the `Family` dataclass (default `()`), parse it from family entries (a plain string or a list, mirroring how `benchmarks` is read), parse the top-level `test_benchmarks:` list, build the union into a new `Config.test_benchmarks: frozenset[str]` field, and validate: any id that is in the test union **and** in any family's validation `benchmarks` raises `ValueError(f"benchmark {id!r} is both a validation and a test benchmark")`.

- [ ] **Step 4: Seed `worklog.yaml`**

Add `test_benchmarks:` per family and the top-level feed-only list exactly as in Global Constraints, with a one-line comment: ids are the `speechval-test` suite's benchmark names; an id listed here is test, everything else is validation.

- [ ] **Step 5: Run the tests to verify they pass, then commit**

Run: `/mnt/scratch-artemis/giuseppe/venvs/speecheval-worklog/bin/python -m pytest tests/test_config.py -q` — PASS.

```bash
git add worklog/config.py worklog.yaml tests/test_config.py
git commit -m "feat: configure test benchmark classification"
```

### Task 2: Store — benchmark-set filters

**Files:**
- Modify: `worklog/store.py` (`Store.history`, `Store.count`)
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `Store.history(*, ..., benchmarks: Collection[str] | None = None, exclude_benchmarks: Collection[str] | None = None)` and `Store.count(*, status=None, benchmarks=None, exclude_benchmarks=None)` — SQL `IN` / `NOT IN` on the `benchmark` column. An empty `benchmarks` matches nothing; an empty `exclude_benchmarks` is no filter at all; `None` is no filter.

- [ ] **Step 1: Write the failing tests in `tests/test_store.py`**

Using the existing store fixture: seed runs with benchmarks `{"a", "b", "c"}`; assert `history(benchmarks={"a", "b"})` returns only a/b rows, `history(exclude_benchmarks={"a"})` drops a, `history(benchmarks=set())` returns none, `history(exclude_benchmarks=set())` returns all, and `count(status="error", exclude_benchmarks={"a"})` counts errors outside a only.

- [ ] **Step 2: Run to verify they fail**

Run: `/mnt/scratch-artemis/giuseppe/venvs/speecheval-worklog/bin/python -m pytest tests/test_store.py -q` — FAIL: unexpected keyword.

- [ ] **Step 3: Implement**

In the clause builder both methods share, turn a non-`None` `benchmarks`/`exclude_benchmarks` into `benchmark IN (?,?,…)`, parameters in a fixed order (sorted, so the SQL is deterministic). Guard the empty collection explicitly: `IN ()` matches nothing, `NOT IN ()` is dropped.

- [ ] **Step 4: Run the tests, then commit**

Run: the store tests — PASS.

```bash
git add worklog/store.py tests/test_store.py
git commit -m "feat: filter the store feed by benchmark set"
```

### Task 3: Views — per-split payload, and trends stays validation

**Files:**
- Modify: `worklog/views.py` (`match_family`, `family_of`, `build_snapshot`)
- Test: `tests/test_views.py`, `tests/test_trends.py`

**Interfaces:**
- Consumes: `Config.test_benchmarks`, `Family.test_benchmarks` (Task 1).
- Produces: `match_family(families, benchmark, task, direction, split="validation")`, `family_of(config, run, split="validation")`, and `build_snapshot(config, runs, feed, version, now, *, failed=None, warnings=0, split="validation")`. Defaults keep every existing call site and test working unchanged.

- [ ] **Step 1: Write the failing tests in `tests/test_views.py`**

- `test_match_family_per_split`: with the real `cfg`, `match_family(cfg.families, "covost2-test", "st", "xen", split="test")` returns the CoVoST2 XtoEN family; with `split="validation"` the same call returns `None`, and `"covost2-dev"` behaves the mirror way.
- `test_a_matrix_family_matches_only_its_own_split`: a run with benchmark `covost2-test` (src `de`, tgt `en`) lands in the CoVoST2 XtoEN column of `build_snapshot(cfg, [run], [], "v", "now", split="test")`, and is unclassified (no family) under the default validation split; the mirror holds for `covost2-dev` under validation vs test. The run-level purity across pages (a test-only model never in the validation page's derived fields) is Task 4's contract, tested there at the API where the input filtering lives.
- `test_empty_test_family_keeps_its_column`: `build_snapshot(..., split="test")` with a single LibriSpeech test run still lists every family name in `filters.families` and in each task's matrix (PolySpeech cells present and "not run"), so the mirrored matrix keeps its columns.
- `test_an_unknown_benchmark_stays_validation`: a run with benchmark `something-new` appears in the validation payload and not in the test payload (the deliberate default, pinned).

- [ ] **Step 2: Write the failing trends regression in `tests/test_trends.py`**

`test_trends_ignores_test_benchmarks`: a run whose benchmark is only in `test_benchmarks` (e.g. `covost2-test`, model matching a lineage) contributes no panel point and no checkpoint to `build_trends(cfg, [run])`, while the dev-benchmark twin does.

- [ ] **Step 3: Run to verify they fail**

Run: `/mnt/scratch-artemis/giuseppe/venvs/speecheval-worklog/bin/python -m pytest tests/test_views.py tests/test_trends.py -q` — FAIL.

- [ ] **Step 4: Implement in `worklog/views.py`**

Thread `split` through `match_family` (validation matches `family.benchmarks`, test matches `family.test_benchmarks`), `family_of`, and `build_snapshot` (store `split` in the payload dict as `"split"` so an API response is self-describing). `build_snapshot` does **not** filter its input runs — the caller (Task 4) does; this task's tests assert the per-split family matching, not run purity.

- [ ] **Step 5: Run the tests, then commit**

Run: `tests/test_views.py tests/test_trends.py` — PASS, and the rest of the suite is untouched.

```bash
git add worklog/views.py tests/test_views.py tests/test_trends.py
git commit -m "feat: build per-split view payloads"
```

### Task 4: App — `/test` route and split-aware snapshot

**Files:**
- Modify: `worklog/app.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: Task 1's `Config.test_benchmarks`, Task 2's store filters, Task 3's `build_snapshot(..., split=)`.
- Produces: `GET /test` (serves `index.html`); `GET /api/snapshot?split=test|validation` (default validation, unknown → 400). Internal: `snapshot(split)` filters `store.winning()` by `(run.benchmark in config.test_benchmarks) == (split == "test")`, passes `benchmarks=sorted(config.test_benchmarks)` (test) or `exclude_benchmarks=sorted(config.test_benchmarks)` (validation) to `store.history(limit=50)` and `store.count(status="error")`.

- [ ] **Step 1: Write the failing tests in `tests/test_api.py`**

Using the existing `client` fixture pattern with a temp store seeded via the `run` helper with both `covost2-dev` and `covost2-test` rows (distinct model names, e.g. `t/val` and `t/only-test`):
- `get("/api/snapshot")` and `get("/api/snapshot?split=validation")` list only the dev row in `runs`, and `t/only-test` appears nowhere in the payload — not in `slices`, `filters.models`, `checkpoints` or `model_families` (the input-run filter proof); `get("/api/snapshot?split=test")` lists only the test row, its payload's `split` is `"test"`, and it reports `kpis.failed` from test rows alone.
- `get("/api/snapshot?split=banana")` → 400.
- `get("/test")` → 200 and serves the same HTML as `get("/")`.
- With no test ids configured (default config), `?split=validation` returns byte-identical `runs` to the pre-change behaviour (empty exclusion set).

- [ ] **Step 2: Run to verify they fail**

Run: `/mnt/scratch-artemis/giuseppe/venvs/speecheval-worklog/bin/python -m pytest tests/test_api.py -q` — FAIL: no `/test` route, no `split` param.

- [ ] **Step 3: Implement in `worklog/app.py`**

Add the `/test` route next to `index()`; extend `snapshot(split="validation")` per the Interfaces block; in `api_snapshot`, read `request.args.get("split", "validation")`, reject anything outside `{"validation", "test"}` with `jsonify({"error": ...}), 400`.

- [ ] **Step 4: Run the tests, then commit**

Run: `tests/test_api.py` — PASS.

```bash
git add worklog/app.py tests/test_api.py
git commit -m "feat: serve a split-aware snapshot and a test route"
```

### Task 5: Page — nav, split detection, badge

**Files:**
- Modify: `worklog/static/index.html` (nav: add the Test link; drop the static `aria-current` — JS owns it now)
- Modify: `worklog/static/app.js` (split from `location.pathname`, pass to every fetch, set `aria-current`, un-hide the badge)
- Modify: `worklog/static/style.css` (badge style, reusing existing token classes)
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: `/api/snapshot?split=…` (Task 4) and the payload's `split` field (Task 3).
- Produces: the visible page. Nothing else consumes it.

- [ ] **Step 1: Write the failing Playwright tests in `tests/test_ui.py`**

Reuse the `make_log` helper in a fixture that seeds one validation log (`covost2-dev`) and one test log (`covost2-test`, distinct model name):
- `test_test_page_renders_the_mirror_and_stays_split_pure`: at `/test`, `aria-current="page"` is on the 🚀 Test nav link, the feed lists the test run and not the validation run, the matrix keeps every family column (including PolySpeech), and the header badge reads "Test splits"; at `/`, the feed lists the validation run only and Results carries `aria-current`.
- `test_test_page_renders_before_any_test_campaign_exists`: at `/test` against a fixture with only validation logs, the page renders KPIs (zeros), all family columns "not run", and an empty feed — no crash.

- [ ] **Step 2: Run to verify they fail**

Run: `env LD_LIBRARY_PATH="$(cat /tmp/opencode/pwlibs.path)" /mnt/scratch-artemis/giuseppe/venvs/speecheval-worklog/bin/python -m pytest tests/test_ui.py -k test_page -v` — FAIL.

- [ ] **Step 3: Implement the page changes**

- `index.html`: nav between Results and Trends gains `<a class="nav-link" href="/test">🚀 Test</a>`; the static `aria-current="page"` on Results is removed; the header gains `<span id="split-badge" class="split-badge" hidden>Test splits</span>`.
- `app.js`: `const split = location.pathname === "/test" ? "test" : "validation";`; every `/api/snapshot` URL gets `&split=${split}` (also on versioned polls); on load, set `aria-current="page"` on the nav link matching the current path and drop it from the others; un-hide `#split-badge` when `split === "test"`.
- `style.css`: a small `.split-badge` rule matching the existing `.hint` look.

- [ ] **Step 4: Run the tests, then the whole suite**

Run: the two new tests — PASS. Then the full gate:

Run: `env LD_LIBRARY_PATH="$(cat /tmp/opencode/pwlibs.path)" /mnt/scratch-artemis/giuseppe/venvs/speecheval-worklog/bin/python -m pytest tests -q` and `ruff check .`
Expected: all pass (158 existing + new), ruff clean.

- [ ] **Step 5: Commit**

```bash
git add worklog/static/index.html worklog/static/app.js worklog/static/style.css tests/test_ui.py
git commit -m "feat: add the test results page"
```
