# Trends Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only `/trends` page that plots each MELT/SMURF training lineage over checkpoint steps, per benchmark and language, with best/worst markers and a merge shortlist.

**Architecture:** A new `worklog/trends.py` folds winning cache rows into lineages, panel points, checkpoints and language series; two JSON endpoints expose that data; a new `trends.js` renders it with the vendored ECharts and adds no build step. The existing auth guard, cache versioning and theme apply unchanged.

**Tech Stack:** Python 3.10+, Flask, SQLite cache, vanilla JS, ECharts (vendored), pytest, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-23-trends-page-design.md`

## Global Constraints

- Read-only app: never write logs, caches, or training state from any route.
- No new runtime dependency, no CDN, no npm/bundler; ECharts stays vendored.
- Python style: `ruff`, line length 119, no imports from `typing` (use `X | None`, `list[str]`).
- Do not change `worklog.views.checkpoint_of`; the matrix depends on its current `latest` semantics.
- Every new page/API is behind the existing login shield; only `/static/*` is public.
- Tests: `pytest tests -q`; browser tests need `LD_LIBRARY_PATH=$(cat /tmp/opencode/pwlibs.path)`.
- CPU only; no GPU or SLURM work in this plan.
- Never write sizeable files under `/mnt/home/giuseppe`.

## Review Focus

- **Aliases, multi-slice families and padded steps.** `eurollm-speech-16n-step-0600000`, `eurollm-speech-16n-step-600000`, bare `step-108000`, LibriSpeech's two benchmarks and Europarl's several pairs must fold and de-duplicate without losing a real evaluation or drawing a second point for one checkpoint.
- **A partial final checkpoint.** Languages absent at the highest step must not disappear from `worst_now`/`regressions`; each uses its own last observed point and shows that step.
- **Direction-aware numbers.** WER/CER/MetricX/unresolved are lower-better; BLEU/chrF/accuracy are higher-better. Best/worst markers, `worst_now` and checkpoint ranks must flip with `lower`, not sort raw numbers.
- **Hiding a lineage.** An unchecked lineage must leave every chart and must not influence the leaderboard's ranks or its minimum-coverage control.
- **Sparse metrics.** MetricX/COMET/pending metrics must not inherit the headline metric's coverage, must not render a missing value as zero, and must not create a panel point with no value.

---

### Task 1: Configured lineages

**Files:**
- Modify: `worklog/config.py`
- Modify: `worklog.yaml`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Lineage(id: str, name: str, patterns: tuple[str, ...])` with `matches(model: str) -> bool`.
- Produces: `Config.lineages: tuple[Lineage, ...]` and `Config.lineage_for(model: str) -> Lineage | None`.
- YAML shape: `trends.lineages: [{id?, name, match: str | [str, ...]}]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_trends_lineages_parse_ids_names_and_pattern_lists(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(
        "trends:\n"
        "  lineages:\n"
        "    - {id: smurf/16n, name: SMURF 16n, match: '^eurollm'}\n"
        "    - {name: SMURF batch, match: ['^step-', '^batch-']}\n"
    )
    cfg = load_config(path)
    assert [lineage.id for lineage in cfg.lineages] == ["smurf/16n", "smurf-batch"]
    assert cfg.lineage_for("eurollm-speech-16n-step-0108000").id == "smurf/16n"
    assert cfg.lineage_for("batch-3000").id == "smurf-batch"
    assert cfg.lineage_for("canary-1b-v2") is None


def test_trends_lineage_regex_must_compile(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("trends:\n  lineages:\n    - {name: Broken, match: '[unclosed'}\n")
    with pytest.raises(ValueError, match="Broken"):
        load_config(path)
```

Add `import pytest` at the top of `tests/test_config.py` if absent.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_config.py -k trends -v`
Expected: FAIL — `load_config(...).lineages` does not exist.

- [ ] **Step 3: Implement the config type and parsing**

In `worklog/config.py`, add `import re`, a frozen dataclass above `Config`, and the field:

```python
@dataclass(frozen=True)
class Lineage:
    """One training line in the trends page: several model prefixes, one label."""

    id: str
    name: str
    patterns: tuple[str, ...]

    def matches(self, model: str) -> bool:
        """Whether *model* belongs to this configured lineage."""
        return any(re.search(pattern, model) for pattern in self.patterns)
```

```python
    lineages: tuple[Lineage, ...] = ()
```

Add a module-level `_slug(name: str) -> str` (lowercase, non-alphanumerics to `-`, strip `-`), a `Config.lineage_for` method returning the first match, and parse `raw.get("trends", {}).get("lineages", [])`. Accept `match` as a string or list; compile each pattern inside `try/except re.error` and raise `ValueError(f"lineage {name!r}: invalid match pattern {pattern!r}")`.

Append to `worklog.yaml`:

```yaml
trends:
  lineages:
    - {id: melt/fondue, name: MELT/Fondue, match: "^fondue"}
    - {id: smurf/16n, name: "SMURF · 16n", match: "^eurollm-speech-16n"}
    - {id: smurf/batch, name: "SMURF · batch", match: '^step-\d+$'}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worklog/config.py worklog.yaml tests/test_config.py
git commit -m "feat: configure trend lineages"
```

---

### Task 2: Lineage folding and de-duplication

**Files:**
- Create: `worklog/trends.py`
- Test: `tests/test_trends.py`

**Interfaces:**
- Consumes: `Lineage`, `Config.lineage_for`, `views.family_of`, `scanner.model_family`.
- Produces: `step_of(model: str) -> tuple[int, str] | tuple[None, str]` returning `(step, base)`.
- Produces: `LineageRef(id: str, name: str, group: str)`, `TrendRecord(run: ScannedRun, lineage: LineageRef, family: Family, step: int)`.
- Produces: `fold_runs(config: Config, runs: Sequence[ScannedRun]) -> list[TrendRecord]`.
- Produces: `build_trends(config: Config, runs: Sequence[ScannedRun]) -> dict` with `aggregate`, `lineages`, and empty `tasks`/`panels`/`checkpoints` until Task 3.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_trends.py`:

```python
from __future__ import annotations

import dataclasses

from worklog.config import Lineage
from worklog.trends import build_trends, fold_runs, step_of

from .helpers import run


def test_step_of_handles_padded_bare_and_plain_names():
    assert step_of("fondue-step-3000") == (3000, "fondue")
    assert step_of("eurollm-speech-16n-step-0108000") == (108000, "eurollm-speech-16n")
    assert step_of("step-108000") == (108000, "step")
    assert step_of("canary-1b-v2") == (None, "canary-1b-v2")


def test_bare_step_names_fold_into_one_lineage(cfg):
    runs = [
        run(model="step-108000", provider="smurf//batch/step=108000.ckpt"),
        run(model="step-114000", provider="smurf//batch/step=114000.ckpt"),
        run(model="eurollm-speech-16n-step-0108000", provider="smurf//sweep/step=108000.ckpt"),
    ]
    payload = build_trends(cfg, runs)
    assert [lineage["id"] for lineage in payload["lineages"]] == ["smurf/16n", "smurf/batch"]
    batch = next(lineage for lineage in payload["lineages"] if lineage["id"] == "smurf/batch")
    assert batch["steps"] == [108000, 114000]
    assert batch["max_step"] == 114000


def test_one_config_entry_can_merge_several_model_prefixes(cfg):
    merged = dataclasses.replace(
        cfg,
        lineages=(Lineage(id="smurf/all", name="SMURF all", patterns=("^eurollm", "^step-")),),
    )
    runs = [
        run(model="step-108000", provider="smurf//batch/step=108000.ckpt"),
        run(model="eurollm-speech-16n-step-0108000", provider="smurf//sweep/step=108000.ckpt"),
    ]
    assert {record.lineage.id for record in fold_runs(merged, runs)} == {"smurf/all"}


def test_two_names_for_one_slice_and_step_keep_the_newest(cfg):
    common = {
        "provider": "smurf//x",
        "benchmark": "polyspeech-100-pa_qt-200",
        "slice": "audio_mcq-polyspeech-pa_qt-de",
        "task": "audio_mcq",
        "src": None,
        "tgt": None,
        "lang": "de",
    }
    runs = [
        run(model="eurollm-speech-16n-step-0600000", created="2026-11-18T10:00:00+00:00",
            metrics={"choice_accuracy": 0.40}, **common),
        run(model="eurollm-speech-16n-step-600000", created="2026-11-19T10:00:00+00:00",
            metrics={"choice_accuracy": 0.50}, **common),
    ]
    records = fold_runs(cfg, runs)
    assert len(records) == 1
    assert records[0].run.metrics["choice_accuracy"] == 0.50


def test_fold_skips_errors_unclassified_and_unstepped_runs(cfg):
    runs = [
        run(model="fondue-step-3000", provider="melt/fondue-step-3000", status="error"),
        run(model="fondue-step-3000", provider="melt/fondue-step-3000",
            benchmark="something-new", slice="x"),
        run(model="canary-1b-v2", provider="melt/hf/canary"),
    ]
    assert fold_runs(cfg, runs) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_trends.py -v`
Expected: FAIL — `ModuleNotFoundError: worklog.trends`.

- [ ] **Step 3: Implement the types, folding and the first payload**

Create `worklog/trends.py`. Use `_GROUP_RANK = {"baseline": 0, "melt": 1, "smurf": 2, "other": 3}` and `_GROUP_LABELS = {"baseline": "Baselines", "melt": "MELT", "smurf": "SMURF", "other": "Other"}`.

For `step_of`, special-case `^step-(\d+)$` to `(int(step), "step")`, otherwise delegate to `views.checkpoint_of` and return `(step, base)` or `(None, model)`.

For `fold_runs`:

- skip `run.status != "success"`, `family_of(...) is None`, or `step is None`;
- assign `group = model_family(run.provider or run.model)`, then the first configured `lineage_for(run.model)` or an auto `LineageRef(f"{group}/{base}", f"{label} · {base}", group)`;
- de-duplicate on `(lineage.id, run.benchmark, run.slice, step)`, keeping the larger `((run.created or ""), run.mtime_ns)`.

For `build_trends`, group records by lineage, emit `"lineages"` sorted by `(_GROUP_RANK[group], name)` with `id`, `name`, `group`, `steps` (sorted union), `max_step`; return `{"aggregate": config.aggregate, "lineages": [...], "tasks": {}, "panels": [], "checkpoints": []}`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_trends.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worklog/trends.py tests/test_trends.py
git commit -m "feat: fold trend lineages from cached runs"
```

---

### Task 3: Panels, per-metric coverage and checkpoint feed

**Files:**
- Modify: `worklog/trends.py`
- Test: `tests/test_trends.py`

**Interfaces:**
- Consumes: `fold_runs`, `views.aggregate`, `config.families`, `config.headline`, `config.metrics`.
- Produces: `build_trends` also returns `tasks`, `panels`, `checkpoints` in the shape in the spec.
- Panel fields: `family`, `task`, `languages`, `points[{lineage, step, values, coverage}]`.
- Checkpoint fields: `lineage`, `model`, `step`, `values{family: number}`, `coverage{family: int}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_trends.py`:

```python
def _panel(payload, family):
    return next(panel for panel in payload["panels"] if panel["family"] == family)


def _point(payload, family, lineage, step):
    return next(
        point
        for point in _panel(payload, family)["points"]
        if point["lineage"] == lineage and point["step"] == step
    )


def test_panel_aggregates_slices_and_reports_per_metric_coverage(cfg):
    runs = [
        run(model="fondue-step-3000", provider="melt/fondue-step-3000",
            slice="st-fleurs-de_en", src="de", tgt="en",
            metrics={"corpus_bleu": 20.0}),
        run(model="fondue-step-3000", provider="melt/fondue-step-3000",
            slice="st-fleurs-fr_en", src="fr", tgt="en",
            metrics={"corpus_chrf": 50.0}, pending=("metricx_scorer",)),
    ]
    payload = build_trends(cfg, runs)
    point = _point(payload, "FLEURS XtoEN", "melt/fondue", 3000)
    assert point["values"]["corpus_bleu"] == 20.0
    assert point["coverage"]["corpus_bleu"] == {"n": 1, "total": 2, "status": "partial"}
    assert point["coverage"]["metricx"] == {"n": 0, "total": 2, "status": "pending"}
    assert "metricx" not in point["values"]
    assert _panel(payload, "FLEURS XtoEN")["languages"] == ["de", "fr"]


def test_panel_uses_the_configured_aggregate(cfg):
    weighted = dataclasses.replace(cfg, aggregate="weighted")
    runs = [
        run(model="fondue-step-3000", provider="melt/fondue-step-3000",
            slice="st-fleurs-de_en", src="de", tgt="en", total_samples=1,
            metrics={"corpus_bleu": 10.0}),
        run(model="fondue-step-3000", provider="melt/fondue-step-3000",
            slice="st-fleurs-fr_en", src="fr", tgt="en", total_samples=3,
            metrics={"corpus_bleu": 20.0}),
    ]
    point = _point(build_trends(weighted, runs), "FLEURS XtoEN", "melt/fondue", 3000)
    assert point["values"]["corpus_bleu"] == 17.5
    point = _point(build_trends(cfg, runs), "FLEURS XtoEN", "melt/fondue", 3000)
    assert point["values"]["corpus_bleu"] == 15.0


def test_checkpoint_feed_has_headline_values_and_slice_counts(cfg):
    runs = [
        run(model="fondue-step-3000", provider="melt/fondue-step-3000",
            slice="st-fleurs-de_en", src="de", tgt="en", metrics={"corpus_bleu": 20.0}),
        run(model="fondue-step-3000", provider="melt/fondue-step-3000",
            slice="st-fleurs-fr_en", src="fr", tgt="en", metrics={"corpus_bleu": 30.0}),
        run(model="fondue-step-3000", provider="melt/fondue-step-3000",
            benchmark="voxpopuli-dev", slice="asr-voxpopuli-de", task="asr",
            src=None, tgt=None, lang="de", metrics={"corpus_wer_raw": 0.12}),
    ]
    payload = build_trends(cfg, runs)
    row = next(row for row in payload["checkpoints"] if row["step"] == 3000)
    assert row["model"] == "fondue-step-3000"
    assert row["values"] == {"FLEURS XtoEN": 25.0, "VoxPopuli": 0.12}
    assert row["coverage"] == {"FLEURS XtoEN": 2, "VoxPopuli": 1}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_trends.py -k "panel or checkpoint" -v`
Expected: FAIL — `panels` is empty / key missing.

- [ ] **Step 3: Implement tasks, panels and checkpoints**

In `build_trends`:

- Build `tasks` for every task in `config.headline` that has configured metrics: `label` from `{"asr": "Speech recognition", "st": "Speech translation", "audio_mcq": "Multiple-choice QA"}`, `metric` = the headline name, `metrics` = `[{name, label, lower}]` over `config.metrics` (same as the matrix).
- Group records by `(family.name, lineage.id, step)`. For each configured family with records, emit a panel in config order; `languages` = sorted distinct `language_column(run)` values, `points` sorted by `(lineage, step)`.
- For each metric name in that task's metric list, collect finite `record.run.metrics[metric]`, aggregate with `aggregate(values, config.aggregate)`, and fill `values` only when non-empty.
- Coverage per metric: `total` = records in the cell, `n` = finite values, `status` = `pending` when `n == 0 and any(record.run.pending)`, `empty` when `n == 0`, `partial` when `n < total`, else `ok`.
- Checkpoints: group by `(lineage.id, step)`, aggregate each family's headline value; `coverage[family]` = number of finite slices; `model` = the model of the newest record for that lineage+step by `((created or ""), mtime_ns)`. Keep only rows with at least one value, sorted by `(lineage, step)`.

Add `language_column(run: ScannedRun) -> str | None` in this task (needed by `languages`): `run.lang`, else `tgt` for `direction_of(src, tgt) == "enx"`, else `src` for `"xen"`, else `tgt or src`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_trends.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worklog/trends.py tests/test_trends.py
git commit -m "feat: build trend panels and checkpoint feed"
```

---

### Task 4: `/api/trends`

**Files:**
- Modify: `worklog/app.py`
- Test: `tests/test_api.py`, `tests/test_auth.py`

**Interfaces:**
- Consumes: `build_trends(config, store.winning())`.
- Produces: `GET /api/trends[?version=]` → JSON with `version`, `generated_at`, `aggregate`, `lineages`, `tasks`, `panels`, `checkpoints`; `204` on version match.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api.py`:

```python
def test_trends_payload_shape(client, store):
    store.sync([
        run("a.json", model="fondue-step-3000", provider="melt/fondue-step-3000",
            metrics={"corpus_bleu": 20.0}),
    ])
    body = client.get("/api/trends").get_json()
    assert set(body) == {
        "version", "generated_at", "aggregate", "lineages", "tasks", "panels", "checkpoints",
    }
    assert body["lineages"][0]["id"] == "melt/fondue"
    assert body["panels"][0]["family"] == "FLEURS XtoEN"
    assert body["panels"][0]["points"][0]["values"]["corpus_bleu"] == 20.0


def test_trends_version_match_is_204(client, store):
    store.sync([run()])
    version = client.get("/api/trends").get_json()["version"]
    assert client.get(f"/api/trends?version={version}").status_code == 204
```

Append to `tests/test_auth.py`:

```python
def test_trends_api_is_locked(cfg, store, tmp_path):
    client = locked_app(cfg, store, tmp_path).test_client()
    assert client.get("/api/trends").status_code == 401
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_api.py tests/test_auth.py -k trends -v`
Expected: FAIL — 404.

- [ ] **Step 3: Implement the route**

In `create_app`, before `install_auth(app, store, password)`, add:

```python
    @app.get("/api/trends")
    def api_trends():
        version = request.args.get("version")
        if version and version == store.version():
            return "", 204
        payload = build_trends(config, store.winning())
        payload.update(version=store.version(), generated_at=_now())
        return jsonify(payload)
```

Import `build_trends` from `.trends`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_api.py tests/test_auth.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worklog/app.py tests/test_api.py tests/test_auth.py
git commit -m "feat: expose the trends feed"
```

---

### Task 5: Language series, worst list and onset

**Files:**
- Modify: `worklog/trends.py`
- Modify: `worklog/app.py`
- Test: `tests/test_trends.py`, `tests/test_api.py`, `tests/test_auth.py`

**Interfaces:**
- Produces: `build_languages(config: Config, runs: Sequence[ScannedRun], family: str, lineage: str, metric: str, threshold: float = 0.1, n: int = 5) -> dict | None`.
- Returns `family`, `lineage`, `task`, `metric{name,label,lower}`, `points[{step, lang, value, n, total}]`, `worst_now`, `regressions`; each list entry has `lang`, `latest`, `latest_step`, `best`, `best_step`, `gap`, `onset`.
- Produces: `GET /api/trends/languages?family=&lineage=&metric=&threshold=&n=&version=`; `404` for unknown family/lineage/metric.

- [ ] **Step 1: Write the failing tests**

Add `build_languages` to the existing `worklog.trends` import at the top of `tests/test_trends.py`, then append:

```python
def _bleu_runs(step_values, lang="de"):
    return [
        run(
            model=f"eurollm-speech-16n-step-{step:06d}",
            provider="smurf//sweep/step.ckpt",
            benchmark="fleurs24-st-xen-dev",
            slice=f"st-fleurs-{lang}_en",
            task="st",
            src=lang,
            tgt="en",
            metrics={"corpus_bleu": value},
            created=f"2026-11-{10 + index:02d}T10:00:00+00:00",
        )
        for index, (step, value) in enumerate(step_values)
    ]


def test_languages_rank_worst_in_the_metric_direction(cfg):
    runs = (
        _bleu_runs([(102000, 30.0), (162000, 32.0), (270000, 31.0)], "de")
        + _bleu_runs([(102000, 20.0), (162000, 10.0), (270000, 9.0)], "mt")
    )
    body = build_languages(cfg, runs, "FLEURS XtoEN", "smurf/16n", "corpus_bleu", threshold=0.1, n=1)
    assert len(body["points"]) == 6
    assert body["worst_now"][0]["lang"] == "mt"
    assert body["worst_now"][0]["latest"] == 9.0
    assert body["worst_now"][0]["latest_step"] == 270000
    assert body["regressions"][0]["lang"] == "mt"
    assert body["regressions"][0]["onset"] == 162000


def test_lower_is_better_flips_the_worst_language(cfg):
    runs = []
    for step, de, fr in [(3000, 0.10, 0.20), (17000, 0.12, 0.25)]:
        for lang, value in (("de", de), ("fr", fr)):
            runs.append(
                run(
                    model=f"fondue-step-{step}",
                    provider="melt/fondue",
                    benchmark="voxpopuli-dev",
                    slice=f"asr-voxpopuli-{lang}",
                    task="asr",
                    src=None,
                    tgt=None,
                    lang=lang,
                    metrics={"corpus_wer_raw": value},
                )
            )
    body = build_languages(cfg, runs, "VoxPopuli", "melt/fondue", "corpus_wer_raw", n=1)
    assert body["worst_now"][0]["lang"] == "fr"


def test_latest_is_each_languages_own_last_point(cfg):
    runs = _bleu_runs([(102000, 20.0), (162000, 10.0)], "mt") + _bleu_runs(
        [(102000, 30.0), (162000, 32.0), (270000, 31.0)], "de"
    )
    body = build_languages(cfg, runs, "FLEURS XtoEN", "smurf/16n", "corpus_bleu", n=5)
    mt = next(item for item in body["worst_now"] if item["lang"] == "mt")
    assert mt["latest"] == 10.0
    assert mt["latest_step"] == 162000


def test_onset_is_the_start_of_the_final_sustained_drop(cfg):
    runs = _bleu_runs([(102000, 20.0), (108000, 10.0), (162000, 20.0), (270000, 10.0)], "mt")
    body = build_languages(cfg, runs, "FLEURS XtoEN", "smurf/16n", "corpus_bleu", threshold=0.1)
    assert body["regressions"][0]["onset"] == 270000


def test_unknown_family_or_metric_returns_none(cfg):
    runs = _bleu_runs([(102000, 20.0), (162000, 10.0)])
    assert build_languages(cfg, runs, "Nope", "smurf/16n", "corpus_bleu") is None
    assert build_languages(cfg, runs, "FLEURS XtoEN", "smurf/16n", "nope") is None
```

Append to `tests/test_api.py`:

```python
def test_languages_endpoint_returns_lists(client, store):
    store.sync([
        run("a.json", model="eurollm-speech-16n-step-0108000",
            provider="smurf//sweep/step.ckpt",
            slice="st-fleurs-de_en", src="de", tgt="en", metrics={"corpus_bleu": 30.0}),
        run("b.json", model="eurollm-speech-16n-step-0162000",
            provider="smurf//sweep/step.ckpt",
            slice="st-fleurs-de_en", src="de", tgt="en", metrics={"corpus_bleu": 20.0}),
    ])
    response = client.get(
        "/api/trends/languages",
        query_string={
            "family": "FLEURS XtoEN",
            "lineage": "smurf/16n",
            "metric": "corpus_bleu",
        },
    )
    body = response.get_json()
    assert response.status_code == 200
    assert body["family"] == "FLEURS XtoEN"
    assert body["regressions"][0]["lang"] == "de"
    assert set(body) >= {"version", "points", "worst_now", "regressions"}


def test_languages_endpoint_unknown_family_is_404(client, store):
    assert client.get(
        "/api/trends/languages",
        query_string={"family": "Nope", "lineage": "smurf/16n", "metric": "corpus_bleu"},
    ).status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_trends.py -k languages tests/test_api.py -k languages -v`
Expected: FAIL — `build_languages` missing / 404.

- [ ] **Step 3: Implement the language aggregation and route**

In `trends.py`, resolve the family by exact name from `config.families`, the metric from `config.metrics`, and the lineage from `build_trends(config, runs)["lineages"]`; return `None` if any is missing. Reuse `fold_runs`, filter on family name and lineage id, group by `(step, language_column(run))`, aggregate finite values and record `n`/`total`.

For each language with at least two points, compute `best`/`best_step` by direction, `latest`/`latest_step` from its own highest step, and `gap = max(0, latest - best)` when lower is better or `max(0, best - latest)` otherwise. `bad(point)` is `gap > 0 and gap >= abs(threshold * best)`; scan points by step, resetting the onset on every non-bad point, so the retained onset is the start of the final bad run (this is the algorithm the `recovery` test pins).

`worst_now` sorts by `-latest` for lower-better, `latest` otherwise, and takes `n`. `regressions` keeps bad languages, sorts by `-gap`, and takes `n`. Both entries include `onset`.

In `app.py`, add `_as_float(name, default)` next to `_as_int`, then:

```python
    @app.get("/api/trends/languages")
    def api_trends_languages():
        version = request.args.get("version")
        if version and version == store.version():
            return "", 204
        payload = build_languages(
            config,
            store.winning(),
            family=request.args.get("family", ""),
            lineage=request.args.get("lineage", ""),
            metric=request.args.get("metric", ""),
            threshold=min(max(_as_float("threshold", 0.1), 0.0), 1.0),
            n=max(_as_int("n", 5), 1),
        )
        if payload is None:
            return jsonify({"error": "unknown family, lineage or metric"}), 404
        payload.update(version=store.version(), generated_at=_now())
        return jsonify(payload)
```

Add `/api/trends/languages` to the locked-API assertions in `tests/test_auth.py`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_trends.py tests/test_api.py tests/test_auth.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worklog/trends.py worklog/app.py tests/test_trends.py tests/test_api.py tests/test_auth.py
git commit -m "feat: add language trends and regression onset"
```

---

### Task 6: Page shell, nav and benchmark panels

**Files:**
- Create: `worklog/static/trends.html`, `worklog/static/trends.js`
- Modify: `worklog/app.py` (`/trends` route), `worklog/static/index.html`, `worklog/static/inspect.html`, `worklog/static/style.css`
- Test: `tests/test_static.py`, `tests/test_ui.py`, `tests/test_auth.py`

**Interfaces:**
- Consumes: `/api/trends`.
- Produces: `GET /trends` serves the page; `.nav` with `Results` and `Trends` on all three pages.
- Produces in DOM: `.trend-panel[data-family]`, `.trend-chart` with an ECharts instance, `input.lineage-toggle[data-lineage]`, `#trend-task`, `#metric-<task>`, `#x-step`, `#x-progress`.
- Produces: `window.Worklog.trends = { render, rankCheckpoints }`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_static.py`:

```python
def test_trends_page_and_nav_are_served(client):
    index = client.get("/").get_data(as_text=True)
    inspect = client.get("/inspect").get_data(as_text=True)
    for html in (index, inspect):
        assert 'class="nav"' in html
        assert 'href="/trends"' in html
    page = client.get("/trends")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    for needle in ('id="trend-panels"', "/static/trends.js", 'id="leaderboard"', 'id="language-pane"'):
        assert needle in body
```

Append to `tests/test_auth.py`:

```python
def test_trends_page_is_locked(cfg, store, tmp_path):
    client = locked_app(cfg, store, tmp_path).test_client()
    assert client.get("/trends").status_code == 302
```

Append to `tests/test_ui.py`:

```python
@pytest.fixture
def trends_server(tmp_path):
    def asr(model, eval_model, wer, created):
        make_log(
            tmp_path,
            model=model,
            eval_model=eval_model,
            benchmark="librispeech-validation-clean",
            slice_id="asr-librispeech-validation-clean",
            task="asr",
            dataset_id=None,
            src=None,
            tgt=None,
            lang="en",
            metrics={"corpus_wer_raw": wer},
            campaign="speechval-trends",
            created=created,
        )

    asr("fondue-step-3000", "melt//fondue/checkpoint-3000", 0.20, "2026-11-10T10:00:00+00:00")
    asr("fondue-step-17000", "melt//fondue/checkpoint-17000", 0.15, "2026-11-10T11:00:00+00:00")
    asr("eurollm-speech-16n-step-0108000", "smurf//sweep/step=108000.ckpt", 0.18, "2026-11-10T12:00:00+00:00")
    asr("eurollm-speech-16n-step-0162000", "smurf//sweep/step=162000.ckpt", 0.14, "2026-11-10T13:00:00+00:00")
    for step, de, mt in [(102000, 30.0, 20.0), (162000, 32.0, 10.0), (270000, 31.0, 9.0)]:
        for lang, value in (("de", de), ("mt", mt)):
            make_log(
                tmp_path,
                model=f"eurollm-speech-16n-step-{step:06d}",
                eval_model="smurf//sweep/step.ckpt",
                benchmark="fleurs24-st-xen-dev",
                slice_id=f"st-fleurs-{lang}_en",
                task="st",
                dataset_id=f"fleurs-{lang}_en",
                src=lang,
                tgt="en",
                metrics={"corpus_bleu": value},
                campaign="speechval-trends",
                created=f"2026-11-11T{step // 60000:02d}:00:00+00:00",
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


def test_trends_page_renders_panels_and_one_line_per_lineage(trends_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(f"{trends_server}/trends")
        page.wait_for_selector(".trend-panel")
        assert page.locator(".nav a[aria-current='page']").inner_text() == "Trends"
        assert page.locator(".trend-panel[data-family='LibriSpeech']").count() == 1
        assert page.locator(".trend-panel[data-family='FLEURS XtoEN']").count() == 1
        option = page.evaluate(
            """() => {
                 const el = document.querySelector('.trend-panel[data-family="LibriSpeech"] .trend-chart');
                 return echarts.getInstanceByDom(el).getOption();
               }"""
        )
        assert sorted(series["name"] for series in option["series"]) == ["MELT/Fondue", "SMURF · 16n"]
        assert option["xAxis"][0]["type"] == "value"
        browser.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_static.py -k trends tests/test_ui.py -k trends_page -v`
Expected: FAIL — `/trends` 404, then missing panels.

- [ ] **Step 3: Implement the shell, nav and panel rendering**

Add `/trends` to `create_app` exactly like `/inspect`, serving `trends.html`.

Create `trends.html` from `inspect.html`'s topbar, inserting:

```html
<nav class="nav" aria-label="Pages">
  <a class="nav-link" href="/">Results</a>
  <a class="nav-link" href="/trends" aria-current="page">Trends</a>
</nav>
```

and a `<main class="trends">` with the controls skeleton, `<section id="trend-panels" class="trend-panels"></section>`, the leaderboard card (`id="leaderboard"`), the tab buttons (`#tab-benchmarks`, `#tab-languages`), `#language-pane` and `#language-chart`. Scripts: vendored ECharts, `util.js`, `trends.js`.

Add the same `<nav>` to `index.html` (Results current, no current on Trends) and `inspect.html` (no current entry). Add `.nav`, `.nav-link`, `.trend-panels`, `.trend-panel`, `.trend-chart` rules to `style.css`; the grid is `repeat(auto-fit, minmax(520px, 1fr))`, chart height 300px.

`trends.js` v1: on `DOMContentLoaded`, fetch `/api/trends`, default all lineages checked, render lineage checkboxes, task select and one metric select per task, then one panel per panel in the payload for the selected task. Each panel is an ECharts line chart with a `value` x-axis on steps (name `Step`), a `value` y-axis with `scale: true`, one series per checked lineage with `data: [[step, value]]` (skip points without the selected metric), `name` = lineage name, `connectNulls: false`. Re-render on checkbox, task or metric change. When the payload has no stepped lineages or no points for the selected task, render `.placeholder` text ("No checkpoints to trend yet" / "No results for this task") instead of an empty grid. Expose `W.trends = { render, rankCheckpoints }` where `rankCheckpoints` is a placeholder returning `[]` until Task 7.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_static.py tests/test_ui.py -k "trends or nav" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worklog/static/trends.html worklog/static/trends.js worklog/app.py worklog/static/index.html worklog/static/inspect.html worklog/static/style.css tests/test_static.py tests/test_ui.py tests/test_auth.py
git commit -m "feat: add the trends page and navigation"
```

---

### Task 7: Chart interactions and the checkpoint leaderboard

**Files:**
- Modify: `worklog/static/trends.js`, `worklog/static/trends.html`, `worklog/static/style.css`
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: the DOM and payload from Task 6.
- Produces: best/worst `markPoint` per series, `#x-step`/`#x-progress` modes, `dataZoom` + `Reset zoom`, lineage hiding, `#leaderboard` table with `tr.checkpoint-row[data-lineage][data-step]`, row-click pinning via `markLine`, `#trend-refresh`, `W.trends.rankCheckpoints(snapshot, visibleIds)`.
- Produces: hash state keys `lineages`, `task`, `metric-<task>`, `x`, `pinned`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ui.py`:

```python
def _trend_panel_option(page, family):
    return page.evaluate(
        """(family) => {
             const el = document.querySelector(`.trend-panel[data-family="${family}"] .trend-chart`);
             return echarts.getInstanceByDom(el).getOption();
           }""",
        family,
    )


def test_trends_hiding_a_lineage_removes_its_series(trends_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(f"{trends_server}/trends")
        page.wait_for_selector(".trend-panel")
        page.uncheck("input.lineage-toggle[data-lineage='smurf/16n']")
        page.wait_for_function(
            """() => {
                 const el = document.querySelector('.trend-panel[data-family="LibriSpeech"] .trend-chart');
                 return echarts.getInstanceByDom(el).getOption().series.length === 1;
               }"""
        )
        option = _trend_panel_option(page, "LibriSpeech")
        assert [series["name"] for series in option["series"]] == ["MELT/Fondue"]
        browser.close()


def test_trends_marks_best_and_worst_in_the_metric_direction(trends_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(f"{trends_server}/trends")
        page.wait_for_selector(".trend-panel")
        option = _trend_panel_option(page, "LibriSpeech")
        series = next(item for item in option["series"] if item["name"] == "MELT/Fondue")
        coords = [item["coord"] for item in series["markPoint"]["data"]]
        assert [17000, 0.15] in coords
        assert [3000, 0.20] in coords
        browser.close()


def test_trends_progress_mode_scales_the_x_axis(trends_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(f"{trends_server}/trends")
        page.wait_for_selector(".trend-panel")
        page.click("#x-progress")
        page.wait_for_function(
            """() => document.querySelector('.trend-panel .trend-chart') &&
                     echarts.getInstanceByDom(document.querySelector('.trend-panel .trend-chart'))
                       .getOption().xAxis[0].name === 'Progress (%)'"""
        )
        option = _trend_panel_option(page, "LibriSpeech")
        xs = [point[0] for series in option["series"] for point in series["data"]]
        assert xs and max(xs) <= 100
        browser.close()


def test_trends_leaderboard_rank_ignores_hidden_lineages(trends_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(f"{trends_server}/trends")
        page.wait_for_selector("#leaderboard tbody tr")
        assert page.locator("#leaderboard tbody tr[data-step='162000']").count() == 1
        page.uncheck("input.lineage-toggle[data-lineage='smurf/16n']")
        page.wait_for_function(
            "() => document.querySelector('#leaderboard tbody tr').dataset.step === '17000'"
        )
        browser.close()


def test_trends_pinning_a_checkpoint_draws_it_on_every_panel(trends_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(f"{trends_server}/trends")
        page.wait_for_selector("#leaderboard tbody tr")
        page.click("#leaderboard tbody tr[data-step='162000']")
        option = _trend_panel_option(page, "LibriSpeech")
        marks = [
            mark["xAxis"]
            for series in option["series"]
            for mark in (series.get("markLine") or {}).get("data", [])
        ]
        assert 162000 in marks
        browser.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_ui.py -k "trends" -v`
Expected: FAIL — no checkboxes/markPoint/leaderboard.

- [ ] **Step 3: Implement the interactions**

- Add best/worst `markPoint` data per series: `{coord: [step, value], name: "best"|"worst"}` chosen by the metric's `lower`; skip duplicates and single-point series.
- Toggle `state.lineages` from the checkboxes; rebuild all panels.
- `#x-step`/`#x-progress` set `state.x`; progress data is `step / lineage.max_step * 100`, axis name `Progress (%)`.
- Add `dataZoom` (inside + slider) on the first panel, a y-axis `inside` zoom, `chart.group = "trends"` and `echarts.connect("trends")`; `#reset-zoom` rebuilds charts at full range and re-applies auto y-scaling.
- `rankCheckpoints(snapshot, visibleIds)`: for each family, rank finite values among rows of visible lineages (lower-better → ascending), average-rank ties, then mean over covered families; return rows sorted by `(mean_rank, -coverage, step)`.
- Render the leaderboard from the rankings: `tr.checkpoint-row[data-lineage][data-step]`, one `td` per family with best ● / worst ○, mean rank and coverage; a `#min-coverage` number input filters rows.
- Row click sets `state.pinned = step`; every series gets `markLine: {data: [{xAxis: step}], ...}`.
- Column headers sort the table (default mean rank); `#trend-refresh` posts `/api/refresh` then reloads `/api/trends`.
- Listen for `worklog:theme`, dispose and rebuild the ECharts instances, and poll `/api/trends?version=…` every 10 s, re-rendering only when the version changed.
- Persist state in the hash (`lineages`, `task`, `metric-<task>`, `x`, `pinned`) and read it on boot.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_ui.py -k "trends" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worklog/static/trends.js worklog/static/trends.html worklog/static/style.css tests/test_ui.py
git commit -m "feat: add trend best/worst marks and checkpoint ranking"
```

---

### Task 8: Languages tab

**Files:**
- Modify: `worklog/static/trends.js`, `worklog/static/style.css`, `worklog/static/trends.html`
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: `/api/trends/languages` and `panels[].languages`.
- Produces: tab switching (`#tab-benchmarks` / `#tab-languages`), `#lang-family`, `#lang-lineage`, `#lang-metric`, `#lang-threshold`, `#lang-n`, `#language-chart`, `#language-list`, `.lang-row[data-lang]`, `.lang-onset`.
- Produces: hash keys `tab`, `lang-family`, `lang-lineage`, `lang-metric`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ui.py`:

```python
def test_trends_languages_tab_lists_weakness_and_onset(trends_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(f"{trends_server}/trends")
        page.wait_for_selector("#leaderboard tbody tr")
        page.click("#tab-languages")
        page.wait_for_selector("#language-list .lang-row")
        rows = page.locator("#language-list .lang-row")
        assert rows.first.get_attribute("data-lang") == "mt"
        onset = page.inner_text("#language-list .lang-row[data-lang='mt'] .lang-onset")
        assert "162000" in onset
        option = page.evaluate(
            """() => {
                 const el = document.getElementById('language-chart');
                 return echarts.getInstanceByDom(el).getOption();
               }"""
        )
        assert sorted(series["name"] for series in option["series"]) == ["de", "mt"]
        browser.close()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_ui.py -k languages_tab -v`
Expected: FAIL — no tab/list.

- [ ] **Step 3: Implement the tab**

Switch panes on tab click. Populate the family select from `panels` whose `languages.length > 1`, the lineage select from checked lineages that have points in the chosen family, and the metric select from the selected family's task metrics. The default family is the first qualifying panel and the default lineage the first checked lineage with points there (MELT has no FLEURS rows, so the FLEURS default is the SMURF lineage).

On any change (debounced 300ms for the two number inputs), fetch `/api/trends/languages` with `family`, `lineage`, `metric`, `threshold`, `n`; render one line per language on `#language-chart` (x = step, y = metric, `scale: true`), and render `#language-list` with a "biggest regressions" section first and a "weakest now" section second. Each `.lang-row[data-lang]` shows `latest` and `latest_step`, `best`, `gap` and `onset`; `onset` shows the step or `—`. Clicking a line or row isolates that language. Persist tab, family, lineage and metric in the hash.

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_ui.py -k languages_tab -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worklog/static/trends.js worklog/static/trends.html worklog/static/style.css tests/test_ui.py
git commit -m "feat: add the trends language explorer"
```

---

### Task 9: Documentation and whole-suite verification

**Files:**
- Modify: `README.md`
- No new tests; run the suite and lint.

- [ ] **Step 1: Update the README**

Add a **Trends** paragraph next to the matrix/heatmap description: one line per configured lineage over checkpoint steps, best/worst marks, the leaderboard's mean-rank shortlist, and the Languages tab's weakest/regressed/onset lists. Document the `trends.lineages` config keys (`id`, `name`, `match`, string or list) and that lineage identity is by model name, so campaign renames do not affect it. Mention `/trends` in the page list near the top.

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
git commit -m "docs: describe the trends page"
```

---

## Self-Review Notes

- Every spec section maps to a task: config lineages (1), folding/dedup (2), panels/coverage/feed (3), API (4), languages/onset (5), shell/nav/panels (6), marks/ranks/pin/state (7), Languages tab (8), docs (9).
- Types are consistent: `Lineage` (config) vs `LineageRef` (payload), `step_of` returns `(step, base)`, `build_trends` keys match the spec, `build_languages` keys match both the endpoint and the UI.
- `checkpoint_of` is untouched; bare `step-N` parsing is trends-only.
- Review Focus lines each have a pinning test: aliases/dedup (Task 2), partial final (Task 5), direction (Task 3/5/7), hidden lineage (Task 7), sparse metrics (Task 3).
