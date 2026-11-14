# Run inspector — design

**Date:** 2026-11-14
**Status:** awaiting approval

## Intent

The worklog answers "what score did this slice get". When a score looks wrong
the next question is "what did the model actually see and say", and today the
only way to answer it is to open a 3–50 MB JSON by hand. This adds a page that
shows one log's setup and its per-sample generations, so a bad slice can be
diagnosed from the browser: wrong prompt, wrong language, wrong normalizer, a
checkpoint that emitted garbage, a truncated completion, a broken reference.

Scope is one **model × slice log** at a time. Comparing two models on the same
sample is explicitly out of scope.

## Goals

- Reachable from the results matrix drill-down and the run feed, and
  deep-linkable by URL.
- Show the evaluation setup: task args, prompt style, instruction text (including
  the file it came from), format spec, generate config, model args, declared
  scorers, package versions, and the slice's aggregate scores.
- Show, per sample: the prompt messages, the audio pointer, the reference, the
  raw completion, the normalized text the metric used, the per-sample score
  payload, timings, and flags that mark suspicious samples.
- Fast enough to page through 2000 samples: no multi-second wait per page, no
  full log in the browser.
- Survive a cold server: the page is stateless apart from a disk cache.

## Non-goals

- No audio playback or waveform rendering (the audio is a shar pointer; playing
  it needs lhotse and the corpora, which this app deliberately does not depend
  on).
- No cross-model or cross-checkpoint comparison, no diff view.
- No editing, re-running, or annotating.
- No change to the scanner or the SQLite schema. The snapshot payload gains
  exactly one field, `run_id` on each slice row (see "Run identity" below); that
  is the one relaxation of the otherwise frozen payload.
- No new runtime dependency.
- Sample text never goes into the SQLite cache: it stays metadata-only.

## Anti-goals

- A page that loads the whole log into the browser, or that re-parses 50 MB on
  every request.
- A second UI (for example proxying `inspect view`) that loses the matrix
  context and shows inspect's raw structure instead of melteval's meaning.

## Constraints from the existing code

- Flask serves one static page plus JSON; the frontend is plain JS with no
  build step and no framework (`worklog/static/*.js`, `index.html`).
- The scanner keeps only metadata per log; `Store.get(run_id)` returns its path
  plus `mtime_ns`/`size`, which is enough to find and validate the file.
- Logs are inspect JSON: `eval` (task, task_args, model_args,
  model_generate_config, config, scorers, packages, metadata), `results.scores`,
  `samples[]` (id, input, target, messages, output, scores, metadata, store,
  timing, events).
- Measured shapes: 2000 samples per slice; 3–50 MB per log in the `speechval-*`
  campaigns; `events` dominate size. The 542 MB outlier lives in
  `baselines-fill-asr`, which `include: ["speechval-*"]` does not scan, so the
  simple parse-once path is enough; a hard 256 MB cap still refuses an
  unexpectedly large log with a clear message rather than risking the host.

## Run identity

`/api/snapshot` currently publishes, per slice, only a composite string id
(`model|benchmark|slice`) and the log path. `Store.get()` takes the numeric row
id, and `snapshot.runs` only covers the 50 newest logs, so a drill-down row for
an older log has no id to link with.

A slice row gains `run_id` (the `ScoredRun.row_id`, already loaded). It is the
only snapshot change and it is additive. `views._slice_row` and the
`test_snapshot_shape` expectation are updated together; the frontend links to
`/inspect?run=<row.run_id>`.

## Approaches considered

1. **New `/inspect` page in the same app, with a lazily built per-log digest
   sidecar** (chosen). Fits the existing structure, deep-linkable, keeps the
   matrix context via a back link, and pays the parse cost once per log.
2. **Expand samples inside the existing drill-down modal.** Least code, but 2000
   samples in a dialog already listing slices, no URL, and the setup panel does
   not fit.
3. **Proxy or link to `inspect view`.** Zero code, but needs the eval venv on the
   worklog host, shows inspect's raw structure rather than "instruction,
   format_spec, normalized text, reference", and has no path back.

## Design

### Digest cache (`worklog/samples.py`)

`build_digest(path, mtime_ns, size, cache_dir)` returns a digest for one log.

- Sidecar file:
  `<cache_dir>/samples/<sha1(abspath)>-<mtime_ns>-<size>-v<SCHEMA_VERSION>.jsonl`.
  The fingerprint makes invalidation on a changed log automatic, and the schema
  version does the same for a changed digest layout (it will change while this
  is built); older sidecars for the same path are deleted when a new one is
  written.
- Line 1 is `{"_overview": {...}, "_sorts": [...]}`; each following line is one
  sample.
- Written atomically (temp file + `os.replace`), under a per-path
  `threading.Lock` so two requests do not build it twice.
- A log above 256 MB, a vanished file, or unparsable JSON raises a typed error
  that the API turns into a 404/409 with the reason; never a 500.
- Prune: after a successful build, delete the oldest sidecars (by mtime) so the
  directory stays under 1 GB; log a warning instead of failing when pruning
  cannot happen.

Overview extraction: `eval.task`, `task` args, `model`, `model_args`,
`model_generate_config`, `config`, `scorers` (name + metrics), `packages`,
`dataset` name/location/samples, `stats` start/completed, `results.scores`
(each scorer's metric name, value and label), and the `config` of the first
`model` event (the effective generate config, e.g. `max_tokens`).

Context files: for `task_args.instruction_file`, `model_args.system_prompt_file`
and `model_args.model_config_override`, return `{path, exists, text}` with the
text capped at 20 KB. Resolution rules:

- An absolute path is used as recorded — it is the path the eval host saw.
- A relative path is reported as recorded and not resolved; silently guessing a
  base directory turns a moved log into a confident wrong answer.
- Files are only read when they resolve under one of the configured `roots`
  (which covers the `instructions/` directory written into the eval output
  folder). A path outside them is shown with `text: null` and
  `note: "outside the configured roots"` — the app can be shared with
  `--host 0.0.0.0`, and the log is writable input, so the page must not become a
  general file reader.
- A file inside the roots that no longer exists is a finding: `exists: false`,
  no text.

Per sample, keep only what the page renders:

| field | source |
| --- | --- |
| `id`, `epoch` | `sample.id`, `sample.epoch` |
| `input` | `sample.input` |
| `target` | `sample.target` |
| `messages` | `sample.messages` (verbatim: text parts and `melteval_audio`) |
| `prompt`, `format_spec` | `sample.store["melteval:prompt"]`, `["melteval:format_spec"]` |
| `completion`, `stop_reason`, `model` | `sample.output`; the stop reason is `output.choices[0].stop_reason`, falling back to `output.stop_reason` when there is no choice |
| `scores` | `sample.scores` verbatim (value, answer, metadata, history length) |
| `metadata` | `sample.metadata` (task, langs, `source_text`, `cut_id`, `duration`, `instruction`, `audio`, and any `extra` a reader wrote) |
| `timing` | `total_time`, `working_time`, `started_at`, `completed_at`, `turn_count`, `len(error_retries)` |
| `error` | `sample.error` if present |

`events` are dropped except the single generate config lifted into the overview.

Flags computed at digest time, per sample: `empty` (no completion), `truncated`
(stop reason of `max_tokens`/`length`), `error` (sample error), and `preamble`
when any scorer's value carries a non-zero `preamble` count.

Per-sample WER is discovered generically rather than by scorer name, because a
slice may be scored by `asr_scorer`, `chunked_asr_scorer` (which keeps only
`hyp_tokens`/`ref_tokens`, so it has none) or a rescoring pass: scan every
scorer's `value` dict for integer `wer_errors` and `ref_words`, take the first
that has them, and derive `wer = wer_errors / ref_words`. When `ref_words` is 0
(the scorer's empty-reference skip path) or the fields are absent, the sample has
no per-sample WER and the card shows "–"; such samples are not reordered by the
`worst` sort.

`_sorts` in the digest header lists which of `order`, `worst`, `slowest`,
`longest` are meaningful for this log — `worst` only when at least one sample
has a derived WER.

### API (`worklog/app.py`)

- `GET /inspect` → `static/inspect.html`.
- `GET /api/run/<id>/overview` → `{run, digest_version, setup, scores, stats,
  digest:{samples, failed, flagged}}`. 404 when the run or the log is gone.
- `GET /api/run/<id>/samples?offset=0&limit=25&q=&sort=order&filter=all` →
  `{total, offset, limit, samples, sorts}`.
  `q` is a case-insensitive substring over id, reference, completion and
  normalized answer. `filter=issues` keeps `empty|truncated|error|preamble`.
  `limit` is capped at 100. Sorting is server-side so paging is stable.

Both endpoints take the path from `Store.get(run_id)` and validate
`(mtime_ns, size)` against the file before using a sidecar; a mismatch rebuilds.
Error mapping: unknown run id → 404, log vanished → 404 with the recorded path,
unreadable or oversized log → 409 with the reason, so the page can say what is
wrong instead of showing a stack trace.

### Page (`static/inspect.html`, `static/inspect.js`)

Reuses `style.css`, `util.js` and the theme. No framework.

```
[SE] SpeechEval worklog                                  ← Back to results
eurollm-speech-16n-step-0120000 · librispeech-validation-clean · asr
────────────────────────────────────────────────────────────────────────
Slice setup                                             scores
 prompt_style  smurf          instruction  "Transcribe this {lang} audio: "
 format_spec   provider=smurf, apply_chat_template=false
 generate      max_tokens 256      model_args  batch 8, override …
 scorers       asr_scorer → corpus_wer_raw, corpus_cer, wer_en, cer_en
 packages      inspect_ai 0.3.254      2000 samples · 0 failed · 0 flagged
 ▸ instruction file (path, 41 B)   ▸ system prompt (path, missing)
────────────────────────────────────────────────────────────────────────
[search reference or hypothesis…] [sort: WER ↓] [x] only issues    1–25 of 2000
┌ 03-000000   WER 4.3%   21.1 s ────────────────────────────────────────┐
│ reference  The branch line connected …                               │
│ normalized he was in a fevered state of mind owing to …              │
│ generated  he was in a fevered state of mind owing to …              │
│ prompt     "Transcribe this English audio: "   audio shar:…/clean:0  │
│ ▸ messages (2)   ▸ scores + metadata                               │
└──────────────────────────────────────────────────────────────────────┘
```

- Header keeps a back link to the matrix with the filters it came from
  (`document.referrer` is unreliable; the drill-down passes its hash through as
  `?back=<encoded hash>`).
- Long text is clamped to five lines with a "show all" toggle.
- Absent fields render as "not recorded" rather than an empty box.
- When `melteval:format_spec.apply_chat_template` is true, the page says the
  chat template is applied inside the provider and is not in the log — the
  messages shown are pre-template.
- Empty state and error state: one line explaining what is missing (log deleted,
  no samples because the run failed, digest build failed).

### Entry points

- `matrix.js` drill-down table: a new **Samples** column linking to
  `/inspect?run=<row.run_id>` (the new slice field), for every row.
- `feed.js`: each run row links to the same page using its existing `id`.

### Configuration

No new keys. The sidecar directory is `<cache parent>/samples` (the cache lives
on scratch; sample digests are rebuildable and must not land in `$HOME`).

## Decisions (mine, correctable)

Calls made while writing this, including the ones a builder review raised:

- **Run identity:** add `run_id` to slice rows rather than keying the new API by
  log path. A path key would put a filesystem string in a URL, need its own
  escaping, and break when a log is re-scanned after a rewrite; the numeric id is
  what `Store` already offers.
- Sidecar on disk over an in-memory cache: keeps memory flat and survives a
  restart; the cache is disposable, so it lives beside the SQLite file.
- A `SCHEMA_VERSION` in the sidecar name, so changing the digest layout during
  this work (and any later upgrade) cannot silently serve an older shape.
- 256 MB hard cap on a log: the scanned campaigns top out around 50 MB, and the
  alternative is a multi-GB RSS spike on the machine that also runs the scanner.
- The digest drops `events`; everything the page needs is either in `store`,
  `scores`, `metadata` or the one `model` event.
- Per-sample WER is discovered from scorer values (`wer_errors`/`ref_words`),
  not by scorer name; logs without those fields simply have no WER and no
  `worst` sort. `chunked_asr_scorer` is exactly such a case.
- Stop reason is read from `choices[0]`, falling back to `output.stop_reason`.
- Context files are read only under the configured `roots`; anything else is
  reported by path. Instruction files written by `evaluate.py` into the eval
  output folder are inside the roots, so the common case is unaffected.
- `limit` default 25 with server-side paging: 2000 samples × ~12 KB of digest is
  ~2 MB per page at 100, which is too much for a browser page.
- The back link is passed explicitly rather than guessed from history.
- No URL state for `q`/`sort` beyond optional round-tripping; paging is in the
  URL so a specific sample page can be shared.

## Open questions for the human

- None that change the shape; the two below are cosmetic.
- Should the sample card collapse the raw messages by default? (assumed yes)
- Should flagged samples be counted in the header even when the filter hides
  them? (assumed yes)

## Verification

- `pytest tests -q` (unit + API + Playwright UI).
- New unit tests: digest extraction from a synthetic log (fields, normalized
  answer, reference, audio pointer, flags, stop reason from `choices[0]`,
  per-sample WER discovery and its absence for token-count-only scores), cache
  reuse, rebuild on mtime change, schema-version rebuild, oversized log, missing
  log, context-file confinement, pagination/filter/sort, pruning.
- New API tests: overview and samples endpoints, `/inspect` serves the page, 404
  for an unknown run, 409 for an unreadable log, `limit` clamp, `run_id` present
  on slice rows in the snapshot.
- New UI test: drill-down → Samples → overview + first card → search narrows the
  list → paging control advances.
- Manual check against `http://127.0.0.1:8765` on a real `speechval-smurf` log.
