# Run Inspector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/inspect?run=<id>` page to the SpeechEval worklog that shows one
log's evaluation setup and its per-sample generations, reached from the matrix
drill-down and the run feed.

**Architecture:** A new `worklog/samples.py` turns one inspect log into a compact
JSONL digest (overview line + one line per sample), cached on disk next to the
SQLite cache and invalidated by path, mtime, size and a schema version. Two GET
endpoints expose the digest (overview, paged samples); a static `inspect.html` +
`inspect.js` renders it with the existing styles and helpers. The only snapshot
change is a `run_id` field on slice rows, so drill-down links have a key.

**Tech Stack:** Python 3.10+, Flask 3, PyYAML, vanilla JS (no build step),
pytest, Playwright for the browser tests.

**Spec:** `docs/superpowers/specs/2026-11-14-run-inspector-design.md`

## Global Constraints

- Python ≥ 3.10; `from __future__ import annotations`; no imports from `typing`
  (`X | None`, `list[dict]`, `dict[str, int]`); ruff line length 119.
- No new runtime dependency. Flask, PyYAML and the vendored ECharts only.
- Read-only app: the only writes are the SQLite cache and the digest sidecars.
- Sidecars live under `<cache parent>/samples`, never under `$HOME`.
- Sample text never enters SQLite; the cache stays metadata-only.
- Context-file contents may only be read from paths under the configured
  `roots`.
- The worklog app is `/home/giuseppe/melt-proj/speecheval-worklog`; all paths
  below are relative to it.
- Unit/API run: `pytest tests -q`. Browser tests additionally need
  `LD_LIBRARY_PATH=$(cat /tmp/opencode/pwlibs.path)`.

## Review Focus

- **Log rewritten between scan and request.** The scanner records `(mtime_ns,
  size)`; if the file changed since, the digest must be rebuilt under the new
  fingerprint rather than served stale. Pinned in Task 3.
- **2000-sample slice under search and sort.** Paging must be stable and each
  page small; a page must never load the whole sidecar into the browser. Pinned
  in Tasks 2 and 4.
- **Scores without WER counts.** `chunked_asr_scorer` keeps only
  `hyp_tokens`/`ref_tokens`, and `ref_words` can be 0 on the scorer's
  empty-reference path. Neither may produce a WER, a sort key, or a
  `ZeroDivisionError`. Pinned in Task 2.
- **Context files outside the roots, or missing.** The page must show the path
  and a note, never the contents, and a missing file inside the roots must read
  as a finding. Pinned in Task 2.
- **A failed run (`samples: []`).** The overview and samples endpoints answer
  200 with an empty list, and the page says why instead of erroring. Pinned in
  Tasks 4 and 5.

---

### Task 1: `run_id` on slice rows

**Files:**
- Modify: `worklog/views.py` (`_slice_row`)
- Modify: `tests/helpers.py` (`run(...)`)
- Test: `tests/test_views.py`

**Interfaces:**
- Consumes: `ScannedRun.row_id` (`int | None`, already set by `Store._to_run`).
- Produces: `snapshot["slices"][i]["run_id"]`, an `int | None` used by the
  drill-down link in Task 6.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_views.py`:

```python
def test_slice_rows_carry_the_run_id(cfg):
    snap = build_snapshot(cfg, [run(row_id=42)], [], "v1", "now")
    assert snap["slices"][0]["run_id"] == 42
```

and give `tests/helpers.py`'s `run()` a `row_id: int | None = None` keyword that
is passed to `ScannedRun(...)`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_views.py::test_slice_rows_carry_the_run_id -v`
Expected: FAIL — `KeyError: 'run_id'`.

- [ ] **Step 3: Add the field**

In `worklog/views.py::_slice_row`, add `"run_id": run.row_id,` next to `"id"`.

- [ ] **Step 4: Run the view and API tests**

Run: `pytest tests/test_views.py tests/test_api.py -q`
Expected: PASS (the snapshot's top-level key set is unchanged).

- [ ] **Step 5: Commit**

```bash
git add worklog/views.py tests/helpers.py tests/test_views.py
git commit -m "worklog: expose the run id on slice rows"
```

---

### Task 2: Digest extraction (pure functions)

**Files:**
- Create: `worklog/samples.py`
- Test: `tests/test_samples.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces, all in `worklog/samples.py`:
  - `SCHEMA_VERSION: int = 1`, `MAX_LOG_BYTES: int = 256 * 1024 * 1024`,
    `MAX_CONTEXT_BYTES: int = 20 * 1024`
  - `sample_record(sample: dict) -> dict`
  - `per_sample_wer(scores: dict) -> float | None`
  - `sorts_for(samples: list[dict]) -> list[str]`
  - `select(samples: list[dict], q: str = "", sort: str = "order", issues_only: bool = False) -> list[dict]`
  - `context_file(recorded: str | None, roots: Sequence[Path]) -> dict | None`
  - `overview_of(body: dict, roots: Sequence[Path]) -> dict`

`sample_record` returns exactly these keys: `id`, `epoch`, `input`, `target`,
`messages`, `prompt`, `format_spec`, `completion`, `stop_reason`, `model`,
`scores`, `metadata`, `timing`, `error`, `flags`, `wer`. `scores` is
`{scorer: {"value", "answer", "metadata", "history"}}` with `history` reduced to
its length. `timing` is `{total_time, working_time, started_at, completed_at,
turn_count, retries}`. `flags` is a subset of `["error", "empty", "truncated",
"preamble"]` in that order.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_samples.py`:

```python
from pathlib import Path

from worklog.samples import context_file, overview_of, per_sample_wer, sample_record, select, sorts_for


def sample(**over):
    body = {
        "id": "s1",
        "epoch": 1,
        "input": "[asr 6.6s]",
        "target": "The reference sentence.",
        "messages": [{"role": "user", "content": [{"type": "text", "text": "Transcribe this English audio: "}]}],
        "output": {
            "model": "smurf//ckpt",
            "choices": [{"message": {"role": "assistant", "content": "the generated sentence"},
                         "stop_reason": "stop"}],
            "completion": "the generated sentence",
            "time": 2.5,
        },
        "scores": {
            "asr_scorer": {
                "value": {"wer_errors": 2, "ref_words": 10, "preamble": 0},
                "answer": "the generated sentence",
                "metadata": {},
                "history": [],
            }
        },
        "metadata": {
            "task": "asr", "lang": "en", "src_lang": "en", "tgt_lang": "",
            "cut_id": "cut-1", "duration": 6.6,
            "audio": {"kind": "shar", "dir": "/corpus/clean", "index": 3},
        },
        "store": {
            "melteval:prompt": "Transcribe this English audio: ",
            "melteval:format_spec": {"provider": "smurf", "apply_chat_template": False},
        },
        "total_time": 2.5,
        "working_time": 2.4,
        "started_at": "2026-11-12T10:00:00+00:00",
        "completed_at": "2026-11-12T10:00:03+00:00",
        "turn_count": 1,
        "error_retries": [],
    }
    body.update(over)
    return body


def test_record_keeps_what_the_page_renders():
    rec = sample_record(sample())
    assert rec["id"] == "s1"
    assert rec["completion"] == "the generated sentence"
    assert rec["stop_reason"] == "stop"
    assert rec["prompt"] == "Transcribe this English audio: "
    assert rec["format_spec"]["provider"] == "smurf"
    assert rec["metadata"]["audio"]["index"] == 3
    assert rec["scores"]["asr_scorer"]["answer"] == "the generated sentence"
    assert rec["scores"]["asr_scorer"]["history"] == 0
    assert rec["timing"] == {
        "total_time": 2.5, "working_time": 2.4,
        "started_at": "2026-11-12T10:00:00+00:00",
        "completed_at": "2026-11-12T10:00:03+00:00",
        "turn_count": 1, "retries": 0,
    }
    assert rec["wer"] == 0.2
    assert rec["flags"] == []


def test_stop_reason_falls_back_when_choices_are_empty():
    rec = sample_record(sample(output={"completion": "x", "stop_reason": "max_tokens", "choices": []}))
    assert rec["stop_reason"] == "max_tokens"
    assert "truncated" in rec["flags"]


def test_flags_mark_empty_error_and_preamble():
    rec = sample_record(sample(
        output={"completion": "  ", "choices": []},
        error={"message": "boom"},
        scores={"asr_scorer": {"value": {"wer_errors": 0, "ref_words": 4, "preamble": 2}}},
    ))
    assert rec["flags"] == ["error", "empty", "preamble"]


def test_token_only_scores_have_no_wer():
    scores = {"chunked_asr_scorer": {"value": {"hyp_tokens": 12, "ref_tokens": 14}}}
    assert per_sample_wer(scores) is None
    rec = sample_record(sample(scores=scores))
    assert rec["wer"] is None
    assert "worst" not in sorts_for([rec])


def test_zero_reference_words_have_no_wer():
    assert per_sample_wer({"asr_scorer": {"value": {"wer_errors": 0, "ref_words": 0}}}) is None


def test_sorts_and_select():
    good = sample_record(sample())
    bad = sample_record(sample(id="s2", target="other", scores={
        "asr_scorer": {"value": {"wer_errors": 9, "ref_words": 10, "preamble": 0}, "answer": "wrong"}}))
    slow = sample_record(sample(id="s3", total_time=99.0))
    rows = [good, bad, slow]
    assert sorts_for(rows) == ["order", "worst", "slowest", "longest"]
    assert [r["id"] for r in select(rows, sort="worst")] == ["s2", "s1", "s3"]
    assert [r["id"] for r in select(rows, sort="slowest")] == ["s3", "s1", "s2"]
    assert [r["id"] for r in select(rows, q="WRONG")] == ["s2"]
    assert [r["id"] for r in select(rows, issues_only=True)] == []
    assert [r["id"] for r in select(rows, sort="nonsense")] == ["s1", "s2", "s3"]


def test_context_file_honours_the_roots(tmp_path):
    inside = tmp_path / "instructions" / "a.txt"
    inside.parent.mkdir()
    inside.write_text("Transcribe this {lang} audio: ")
    assert context_file(str(inside), [tmp_path]) == {
        "path": str(inside), "exists": True,
        "text": "Transcribe this {lang} audio: ", "note": "",
    }
    outside = context_file("/etc/hostname", [tmp_path])
    assert outside["exists"] is True and outside["text"] is None
    assert outside["note"] == "outside the configured roots"
    missing = context_file(str(tmp_path / "gone.txt"), [tmp_path])
    assert missing["exists"] is False and missing["text"] is None
    relative = context_file("configs/x.yaml", [tmp_path])
    assert relative["text"] is None and relative["exists"] is None
    assert context_file(None, [tmp_path]) is None


def test_overview_lifts_setup_and_generate_config(tmp_path):
    body = {
        "eval": {
            "task": "speech-asr-clean",
            "model": "smurf//ckpt",
            "task_args": {"prompt_style": "smurf", "instruction_file": str(tmp_path / "gone.txt")},
            "model_args": {"batch_size": 8},
            "model_generate_config": {},
            "config": {"epochs": 1},
            "packages": {"inspect_ai": "0.3.254"},
            "dataset": {"name": "frozen", "location": "/frozen/manifest.jsonl", "samples": 2000},
            "scorers": [{"name": "asr_scorer", "metrics": [{"name": "corpus_wer_raw"}]}],
        },
        "results": {"scores": [{"name": "asr_scorer", "metrics": {"corpus_wer_raw": {"value": 0.1}}}]},
        "stats": {"started_at": "a", "completed_at": "b", "model_usage": {}},
        "samples": [{"events": [{"event": "model", "config": {"max_tokens": 256}}]}],
    }
    ov = overview_of(body, [tmp_path])
    assert ov["task"] == "speech-asr-clean"
    assert ov["generate_config"] == {"max_tokens": 256}
    assert ov["packages"] == {"inspect_ai": "0.3.254"}
    assert ov["dataset"]["samples"] == 2000
    assert ov["scorers"] == [{"name": "asr_scorer", "metrics": ["corpus_wer_raw"]}]
    assert ov["context_files"]["instruction_file"]["exists"] is False
    assert ov["context_files"]["system_prompt_file"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_samples.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'worklog.samples'`.

- [ ] **Step 3: Implement the functions in `worklog/samples.py`**

Signatures as listed in **Interfaces**. Implementation notes that the tests do
not pin:

- `per_sample_wer` scans `scores.values()`, takes the first `value` dict with an
  `int` `wer_errors` and an `int` `ref_words > 0`, and returns
  `wer_errors / ref_words`.
- `sample_record` reads `output.choices[0].stop_reason` first, then
  `output.stop_reason`; a sample with `error` set also gets the `error` flag and
  keeps `error` verbatim.
- `context_file`: `None` in → `None` out; a non-absolute path → `exists: None`,
  `text: None`, note `"relative path, not resolved"`; a resolved path outside
  `roots` → note `"outside the configured roots"` and no text; a missing path
  inside the roots → `exists: False`; text is capped at `MAX_CONTEXT_BYTES` with
  note `"truncated"`.
- `overview_of` returns `task`, `model`, `task_args`, `model_args`,
  `model_generate_config`, `eval_config` (from `eval.config`), `packages`,
  `dataset` (`name`/`location`/`samples`), `scorers` (list of
  `{name, metrics: [names]}`), `generate_config` (the `config` of the first
  `model` event, else `None`), `scores` (the raw `results.scores`), `stats`, and
  `context_files` with keys `instruction_file`, `system_prompt_file`,
  `model_config_override` taken from the two args blocks.
- `select` is stable: ties keep digest order, `worst` puts `wer is None` last.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_samples.py -q && ruff check worklog/samples.py tests/test_samples.py`
Expected: PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add worklog/samples.py tests/test_samples.py
git commit -m "worklog: extract per-sample records from inspect logs"
```

---

### Task 3: Digest sidecar cache

**Files:**
- Modify: `worklog/samples.py`
- Modify: `tests/helpers.py` (new `make_sample_log`)
- Test: `tests/test_samples.py`

**Interfaces:**
- Consumes: `sample_record`, `overview_of`, `sorts_for` from Task 2.
- Produces, in `worklog/samples.py`:
  - `class DigestError(Exception)` with `status: int` and `message: str`
  - `@dataclass(frozen=True) class Digest` with `overview: dict`,
    `samples: list[dict]`, `sorts: list[str]`, `flagged: int`, `failed: int`
  - `sidecar_path(samples_dir: Path, path: str, mtime_ns: int, size: int) -> Path`
  - `load_digest(path: str, mtime_ns: int, size: int, samples_dir: Path, roots: Sequence[Path]) -> Digest`
  - `prune(samples_dir: Path, budget_bytes: int = 1024**3) -> int`

`load_digest` raises `DigestError(404)` when the file is gone and
`DigestError(409)` when it is larger than `MAX_LOG_BYTES` or is not valid JSON.
The sidecar name is
`<sha1(abspath)[:16]>-<mtime_ns>-<size>-v<SCHEMA_VERSION>.jsonl`; the first line
is `{"_overview": ..., "_sorts": [...], "_flagged": n, "_failed": n}` and every
following line is one `sample_record`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_samples.py`, plus a helper in `tests/helpers.py`:

```python
def make_sample_log(base, *, model="canary-1b-v2", benchmark="librispeech-validation-clean",
                    slice_id="asr-librispeech-validation-clean", task="asr",
                    samples=3, campaign="speechval-canary",
                    created="2026-11-12T10:00:00+00:00"):
    """Write a log with real per-sample records and return its path."""
    instructions = Path(base) / "instructions"
    instructions.mkdir(parents=True, exist_ok=True)
    instruction_file = instructions / "abc123.txt"
    instruction_file.write_text("Transcribe this {lang} audio: ")
    body = {
        "version": 2,
        "status": "success",
        "eval": {
            "eval_id": slice_id,
            "created": created,
            "task": f"speech-{task}-{slice_id}",
            "task_args": {"prompt_style": "smurf", "instruction_file": str(instruction_file)},
            "model_args": {"batch_size": 8},
            "model_generate_config": {},
            "config": {"epochs": 1},
            "packages": {"inspect_ai": "0.3.254"},
            "dataset": {"name": benchmark, "location": "/frozen", "samples": samples},
            "metadata": {
                "melteval_model": model, "melteval_benchmark": benchmark,
                "melteval_slice": slice_id, "melteval_task": task,
                "melteval_campaign": campaign,
            },
            "scorers": [{"name": "asr_scorer", "metrics": [{"name": "corpus_wer_raw"}]}],
        },
        "results": {
            "total_samples": samples,
            "completed_samples": samples,
            "scores": [{"name": "asr_scorer", "metrics": {"corpus_wer_raw": {"name": "corpus_wer_raw", "value": 0.1}}}],
        },
        "stats": {"started_at": created, "completed_at": created, "model_usage": {}},
        "samples": [
            {
                "id": f"03-{i:06d}", "epoch": 1, "input": "[asr 6.6s]",
                "target": f"the reference {i}.",
                "messages": [{"role": "user", "content": [{"type": "text", "text": "Transcribe this English audio: "}]}],
                "output": {
                    "model": "smurf//ckpt",
                    "choices": [{"stop_reason": "stop"}],
                    "completion": f"the generated {i}",
                    "time": 1.0 + i,
                },
                "scores": {"asr_scorer": {
                    "value": {"wer_errors": i, "ref_words": 10, "preamble": 0},
                    "answer": f"the generated {i}", "metadata": {}, "history": [],
                }},
                "metadata": {"task": task, "lang": "en", "cut_id": f"cut-{i}"},
                "store": {"melteval:prompt": "Transcribe this English audio: "},
                "total_time": 1.0 + i, "working_time": 1.0, "turn_count": 1, "error_retries": [],
                "events": [{"event": "model", "config": {"max_tokens": 256}}],
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

```python
def test_digest_is_written_and_reused(tmp_path):
    log = make_sample_log(tmp_path, samples=3)
    stat = log.stat()
    first = load_digest(str(log), stat.st_mtime_ns, stat.st_size, tmp_path / "s", [tmp_path])
    assert first.overview["task"] == "speech-asr-librispeech-validation-clean"
    assert [r["id"] for r in first.samples] == ["03-000000", "03-000001", "03-000002"]
    assert first.failed == 0 and first.flagged == 0
    assert first.sorts == ["order", "worst", "slowest", "longest"]
    sidecars = list((tmp_path / "s").glob("*.jsonl"))
    assert len(sidecars) == 1 and f"-v{SCHEMA_VERSION}" in sidecars[0].name
    second = load_digest(str(log), stat.st_mtime_ns, stat.st_size, tmp_path / "s", [tmp_path])
    assert second.samples == first.samples


def test_new_fingerprint_rebuilds_and_drops_the_old_sidecar(tmp_path):
    log = make_sample_log(tmp_path, samples=2)
    first = log.stat()
    load_digest(str(log), first.st_mtime_ns, first.st_size, tmp_path / "s", [tmp_path])
    load_digest(str(log), first.st_mtime_ns, first.st_size + 1, tmp_path / "s", [tmp_path])
    assert len(list((tmp_path / "s").glob("*.jsonl"))) == 1


def test_missing_and_broken_logs_raise_typed_errors(tmp_path):
    missing = tmp_path / "gone.json"
    with pytest.raises(DigestError) as err:
        load_digest(str(missing), 1, 1, tmp_path / "s", [tmp_path])
    assert err.value.status == 404
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    stat = broken.stat()
    with pytest.raises(DigestError) as err:
        load_digest(str(broken), stat.st_mtime_ns, stat.st_size, tmp_path / "s", [tmp_path])
    assert err.value.status == 409 and "json" in err.value.message.lower()


def test_failed_run_with_no_samples(tmp_path):
    log = make_sample_log(tmp_path, samples=0)
    stat = log.stat()
    digest = load_digest(str(log), stat.st_mtime_ns, stat.st_size, tmp_path / "s", [tmp_path])
    assert digest.samples == [] and digest.overview["dataset"]["samples"] == 0


def test_prune_keeps_the_newest(tmp_path):
    for i in range(3):
        path = tmp_path / f"x{i}.jsonl"
        path.write_text("x" * 10)
        os.utime(path, (1000 + i, 1000 + i))
    removed = prune(tmp_path, budget_bytes=15)
    assert removed == 2
    assert [p.name for p in tmp_path.glob("*.jsonl")] == ["x2.jsonl"]
```

Add the imports `os`, `json`, `pytest`, `DigestError`, `SCHEMA_VERSION`,
`load_digest`, `prune` and `from .helpers import make_sample_log` at the top.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_samples.py -q`
Expected: FAIL — `ImportError: cannot import name 'load_digest'`.

- [ ] **Step 3: Implement the cache in `worklog/samples.py`**

Implementation notes the tests do not pin:

- A module-level `_LOCKS: dict[str, threading.Lock]` keyed by sidecar path;
  build inside the lock and write to `<name>.tmp` then `os.replace`.
- `load_digest` stats the file first, prefers the *current* `(mtime_ns, size)`
  over the recorded pair, and rebuilds when the sidecar is absent, unparsable,
  or carries a different `_overview`/schema. Delete sibling sidecars for the
  same `sha1` prefix after a successful build.
- Parse the sidecar line by line with `json.loads`; a corrupt line raises
  `DigestError(409, "digest cache is corrupt: …")` and triggers one rebuild.
- `flagged` counts samples with a non-empty `flags`, `failed` counts samples
  with the `error` flag.
- `prune` sums file sizes, deletes oldest by `mtime` while over budget, and
  returns the number removed.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_samples.py -q && ruff check worklog/samples.py tests/test_samples.py tests/helpers.py`
Expected: PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add worklog/samples.py tests/helpers.py tests/test_samples.py
git commit -m "worklog: cache per-log sample digests on disk"
```

---

### Task 4: Overview and samples endpoints

**Files:**
- Modify: `worklog/app.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `load_digest`, `DigestError`, `select` (Tasks 2–3); `Store.get`.
- Produces:
  - `GET /api/run/<int:run_id>/overview` → `{run, setup, scores, stats, digest}`
    where `run` is the existing `_run_dict` shape and `digest` is
    `{samples, failed, flagged, sorts}`.
  - `GET /api/run/<int:run_id>/samples?offset&limit&q&sort&filter` →
    `{total, offset, limit, samples}`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_api.py`:

```python
def test_overview_returns_setup(client, store, tmp_path, cfg):
    path = make_sample_log(tmp_path, samples=2)
    store.sync([run(str(path), model="canary-1b-v2", task="asr")])
    rid = store.all()[0].row_id
    body = client.get(f"/api/run/{rid}/overview").get_json()
    assert body["run"]["model"] == "canary-1b-v2"
    assert body["setup"]["task_args"]["prompt_style"] == "smurf"
    assert body["setup"]["generate_config"] == {"max_tokens": 256}
    assert body["digest"] == {"samples": 2, "failed": 0, "flagged": 0,
                              "sorts": ["order", "worst", "slowest", "longest"]}


def test_samples_endpoint_pages_filters_and_sorts(client, store, tmp_path):
    path = make_sample_log(tmp_path, samples=30)
    store.sync([run(str(path), task="asr")])
    rid = store.all()[0].row_id
    page = client.get(f"/api/run/{rid}/samples?offset=10&limit=5").get_json()
    assert page["total"] == 30 and page["offset"] == 10 and len(page["samples"]) == 5
    assert page["samples"][0]["id"] == "03-000010"
    narrow = client.get(f"/api/run/{rid}/samples?q=reference%202&limit=100").get_json()
    assert [r["id"] for r in narrow["samples"]] == ["03-000002"]
    worst = client.get(f"/api/run/{rid}/samples?sort=worst&limit=3").get_json()
    assert [r["wer"] for r in worst["samples"]] == [0.9, 0.8, 0.7]
    capped = client.get(f"/api/run/{rid}/samples?limit=5000").get_json()
    assert capped["limit"] == 100


def test_inspector_errors_are_typed(client, store, tmp_path):
    store.sync([run(str(tmp_path / "gone.json"), task="asr")])
    rid = store.all()[0].row_id
    assert client.get(f"/api/run/{rid}/overview").status_code == 404
    assert client.get(f"/api/run/{rid}/samples").status_code == 404
    broken = tmp_path / "broken.json"
    broken.write_text("{nope")
    store.sync([run(str(broken), task="asr")])
    broken_id = next(r.row_id for r in store.all() if r.path == str(broken))
    response = client.get(f"/api/run/{broken_id}/overview")
    assert response.status_code == 409 and response.get_json()["error"]
    assert client.get("/api/run/9999/overview").status_code == 404
```

Add `from .helpers import make_sample_log` at the top of the file.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_api.py -q`
Expected: FAIL — 404 on the new routes.

- [ ] **Step 3: Implement the routes in `worklog/app.py`**

Implementation notes the tests do not pin:

- `samples_dir = config.cache.parent / "samples"` computed once inside
  `create_app`.
- A shared helper `load(row)` returns the digest or raises; both routes call it
  and translate `DigestError` into `jsonify({"error": err.message, "path": row.path}),
  err.status`.
- `offset` and `limit` parse with the existing tolerant `as_int` style; `limit`
  clamps to `1..100`, `offset` to `>= 0`; unknown `sort` falls back to `order`.
- `filter=issues` maps to `issues_only=True`.
- `setup` is `digest.overview`; `scores` is `overview["scores"]`; `stats` is
  `overview["stats"]`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_api.py -q && ruff check worklog/app.py`
Expected: PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add worklog/app.py tests/test_api.py
git commit -m "worklog: serve sample digests over HTTP"
```

---

### Task 5: The `/inspect` page

**Files:**
- Create: `worklog/static/inspect.html`
- Create: `worklog/static/inspect.js`
- Modify: `worklog/static/style.css`
- Modify: `worklog/app.py` (route)
- Test: `tests/test_api.py` (route), `tests/test_ui.py` (page)

**Interfaces:**
- Consumes: the endpoints from Task 4; `W.escape`, `W.when`, `W.theme` from
  `util.js`.
- Produces: `GET /inspect` → `static/inspect.html`; `window.Worklog.inspect`
  with `boot()` bound on `DOMContentLoaded`.

Element ids the UI test relies on: `run-title`, `run-meta`, `setup`,
`setup-files`, `scores`, `sample-search`, `sample-sort`, `sample-issues`,
`sample-count`, `samples`, `sample-prev`, `sample-next`, `back-link`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_api.py`:

```python
def test_inspect_page_is_served(client):
    response = client.get("/inspect")
    assert response.status_code == 200
    assert b"SpeechEval" in response.data
```

Add to `tests/test_ui.py` a dedicated fixture and test:

```python
@pytest.fixture
def inspector_server(tmp_path):
    path = make_sample_log(tmp_path, samples=30)
    store = Store(tmp_path / "w.sqlite")
    store.sync([_scan(path)])
    app = create_app(load_config(), store)
    server = make_server("127.0.0.1", 0, app)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", store.all()[0].row_id
    server.shutdown()


def test_inspector_renders_setup_and_samples(inspector_server, tmp_path):
    base, rid = inspector_server
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 1200})
        page.goto(f"{base}/inspect?run={rid}")
        page.wait_for_selector("#samples .sample")
        assert "canary-1b-v2" in page.inner_text("#run-title")
        assert "smurf" in page.inner_text("#setup")
        assert "the generated 0" in page.inner_text("#samples")
        assert "1–25 of 30" in page.inner_text("#sample-count")
        page.fill("#sample-search", "reference 2")
        page.wait_for_function("() => document.querySelector('#sample-count').textContent.includes('1–1 of 1')")
        assert "03-000002" in page.inner_text("#samples")
        page.fill("#sample-search", "")
        page.click("#sample-next")
        page.wait_for_function("() => document.querySelector('#samples').textContent.includes('03-000025')")
        page.screenshot(path=str(tmp_path / "inspector.png"), full_page=True)
        browser.close()
```

`_scan(path)` is `scan_run(Path(path))` and the fixture asserts it is not `None`;
import `scan_run` from `worklog.scanner` (already imported in that file) and
`make_sample_log` from `.helpers`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `LD_LIBRARY_PATH=$(cat /tmp/opencode/pwlibs.path) pytest tests/test_api.py::test_inspect_page_is_served tests/test_ui.py::test_inspector_renders_setup_and_samples -q`
Expected: FAIL — 404 on `/inspect`.

- [ ] **Step 3: Add the route and the page**

Route in `worklog/app.py`:

```python
@app.get("/inspect")
def inspect_page():
    return send_from_directory(app.static_folder, "inspect.html")
```

`inspect.html`: copy the `index.html` header (brand links to `/`, theme toggle,
no refresh button), then:

```html
<main class="inspector">
  <a id="back-link" class="btn ghost" href="/">← Results</a>
  <h2 id="run-title">Loading…</h2>
  <p id="run-meta" class="hint"></p>
  <section class="card"><div class="card-head"><h3>Slice setup</h3></div>
    <div id="setup" class="fieldgrid"></div><div id="setup-files"></div></section>
  <section class="card"><div class="card-head"><h3>Scores</h3></div><div id="scores"></div></section>
  <section class="card">
    <div class="card-head"><h3>Samples</h3><span id="sample-count" class="hint"></span></div>
    <div class="toolbar">
      <input id="sample-search" type="search" placeholder="search reference or hypothesis…">
      <select id="sample-sort"></select>
      <label class="check"><input id="sample-issues" type="checkbox"> only issues</label>
      <button id="sample-prev" class="btn ghost" type="button">← Prev</button>
      <button id="sample-next" class="btn ghost" type="button">Next →</button>
    </div>
    <div id="samples" class="samples"></div>
  </section>
</main>
```

Include `util.js` then `inspect.js`.

`inspect.js` — one IIFE on `window.Worklog`. Behaviour, not shape:

- Read `run`, `offset`, `q`, `sort`, `filter`, `back` from `location.search`.
  `boot()` fetches `/api/run/<run>/overview`, fills `#run-title` with
  `model · benchmark`, `#run-meta` with task, status, created/finished, sample
  counts and the recorded log path, then renders `setup` as a definition grid
  (`prompt_style`, `instruction`, `format_spec`, `generate_config`, `model_args`,
  `scorers`, `packages`, `dataset`) and `setup-files` as `<details>` blocks for
  each non-null context file, showing `path`, `exists` and `text` (or the note).
- `#scores` renders each scorer's metrics as a small table.
- Then fetch `/api/run/<run>/samples` with the current query and render cards:
  header `id` + WER (as a percentage, or "–") + `total_time`; rows labelled
  Reference, Normalized, Generated, Prompt, Audio, with `Generated` and
  `Normalized` clamped to five lines and a "show all" toggle; `<details>` for
  `messages`, `scores` and `metadata` rendered as `<pre>` JSON.
- Sorting options come from `digest.sorts`; `filter=issues` ticks the checkbox;
  search is debounced 250 ms; paging buttons move by `limit` and update the URL
  with `history.replaceState`; `#back-link` uses `back` when present. The count
  reads exactly `"{first}–{last} of {total}"` with an en dash (so 30 samples at
  the default limit render `1–25 of 30`), or `"0 of 0"` when nothing matches.
- Errors: a non-ok response renders one `.placeholder` line with
  `body.error`, and `run` missing renders "No run selected — pick a slice in the
  results matrix."
- When `digest.samples === 0`, render "This run has no samples (status …)."
- When `format_spec.apply_chat_template` is true, a note in `#setup` says the
  chat template is applied inside the provider and the messages shown are
  pre-template.

CSS: add `.inspector`, `.fieldgrid` (label/value grid), `.samples`, `.sample`,
`.sample-head`, `.sample-row`, `.sample-label`, `.clamped`, `.toolbar`, reusing
the existing colour variables and `.card`/`.chip`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `LD_LIBRARY_PATH=$(cat /tmp/opencode/pwlibs.path) pytest tests/test_api.py tests/test_ui.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worklog/app.py worklog/static/inspect.html worklog/static/inspect.js worklog/static/style.css tests/test_api.py tests/test_ui.py
git commit -m "worklog: add the run inspector page"
```

---

### Task 6: Link from the matrix and the feed

**Files:**
- Modify: `worklog/static/matrix.js` (drill-down table)
- Modify: `worklog/static/feed.js`
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: `slice.run_id` (Task 1), `run.id` in `snapshot.runs`.
- Produces: links to `/inspect?run=<id>` that carry the current filter hash as
  `back`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ui.py`:

```python
def test_drilldown_and_feed_link_to_the_inspector(live_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(live_server)
        page.wait_for_function("window.Worklog && window.Worklog.matrix")
        snapshot = page.evaluate("() => fetch('/api/snapshot').then((r) => r.json())")
        page.evaluate("([snap, state]) => window.Worklog.matrix.render(snap, state)",
                      [snapshot, {"aggregate": "macro"}])
        page.locator(".cell:not(.empty)").first.click()
        page.wait_for_selector("#modal[open]")
        href = page.get_attribute("#modal a.samples", "href")
        assert href.startswith("/inspect?run=") and "back=" in href
        page.click("#modal-close")
        page.wait_for_selector("#feed a.run")
        assert page.get_attribute("#feed a.run", "href").startswith("/inspect?run=")
        browser.close()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `LD_LIBRARY_PATH=$(cat /tmp/opencode/pwlibs.path) pytest tests/test_ui.py::test_drilldown_and_feed_link_to_the_inspector -q`
Expected: FAIL — no `a.samples` inside the dialog.

- [ ] **Step 3: Add the links**

`matrix.js::drill`: add a `<th>Samples</th>` column and, per row,
`<td><a class="samples" href="/inspect?run=${row.run_id}&back=${encodeURIComponent(location.hash)}">open</a></td>`,
keeping the existing `logpath` cell as the copy-on-click path.

`feed.js::render`: wrap each `.run` block in
`<a class="run" href="/inspect?run=${run.id}">…</a>` and add the matching
`.feed a.run { color: inherit; text-decoration: none; }` rule to `style.css`.

- [ ] **Step 4: Run the UI tests**

Run: `LD_LIBRARY_PATH=$(cat /tmp/opencode/pwlibs.path) pytest tests/test_ui.py -q`
Expected: PASS, including the earlier drill-down test.

- [ ] **Step 5: Commit**

```bash
git add worklog/static/matrix.js worklog/static/feed.js worklog/static/style.css tests/test_ui.py
git commit -m "worklog: link results and feed to the inspector"
```

---

### Task 7: Documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/design.md` (append a section, do not rewrite the existing one)

**Interfaces:**
- Consumes: everything above.
- Produces: no code.

- [ ] **Step 1: Update the README**

- Remove the "no sample browser" parenthetical in "What it is, and is not" and
  replace it with one sentence naming `/inspect?run=<id>` and the digest cache
  under `<cache parent>/samples`.
- Add a "Inspecting a run" subsection under Configuration: what the page shows,
  that audio is a pointer, that the chat-templated prompt is not in the log for
  provider-side templates, that digest files are rebuildable and pruned at 1 GB,
  and that `q`/`sort`/`filter`/`offset` are in the URL.
- Add `worklog/samples.py` to the Layout block with the line
  "one log -> per-sample digest, sidecar cache".

- [ ] **Step 2: Check the docs render and the suite is green**

Run: `pytest tests -q && ruff check .`
Expected: PASS, ruff clean.

- [ ] **Step 3: Commit**

```bash
git add README.md docs/design.md
git commit -m "docs: describe the run inspector"
```

---

## Self-review notes

- Spec coverage: snapshot `run_id` (Task 1), digest + sidecar + pruning + schema
  version (Tasks 2–3), API + error mapping (Task 4), page + setup panel + sample
  cards + empty/error states (Task 5), entry points (Task 6), docs (Task 7).
- Deliberately not planned: audio playback, cross-model comparison, auth — all
  listed as non-goals in the spec.
- The plan's only browser-test dependency is the Playwright staging documented in
  the README; without it those tests skip, so the unit and API suite stays green
  on a machine with no browser.
