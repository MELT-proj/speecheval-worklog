# speechval worklog + Canary-1B-v2 baseline — design

2026-11-12. Two related but independent pieces of work, in this order:

1. **Get Canary-1B-v2 running on `speechval-dev` on artemis**, as the first row of a
   suite that will later hold MELT and SMURF checkpoints.
2. **Build the worklog**: a read-only Flask dashboard that scans inspect logs from a
   source folder and renders them as a model × benchmark matrix, per-language
   heatmaps, and a live run feed.

The first produces the data the second displays; the second is also buildable
against the ~500 inspect logs already in `eval-runs/`.

## Intent and why

`speechval-dev` exists so that every speech model the group produces is scored the
same way, on the same frozen dev sets, and the numbers can be compared without
re-deriving where each one came from. Today those numbers live in per-campaign
Excel reports and someone has to remember which run superseded which. The worklog
makes the suite legible at a glance to people who did not run it — collaborators
who get a link, not a shell.

Canary-1B-v2 is the first external baseline: it is already a supported provider
(`melt/hf/canary`), staged in the artemis HF cache, and has a venv, so the
remaining work is operational rather than code.

## Goals

- Canary scored on every `speechval-dev` benchmark direction it actually supports,
  with logs in the standard campaign layout, WER/BLEU/chrF inline and MetricX
  post-hoc.
- A freeze pass that makes `speechval-dev` runnable on artemis at all (its full
  dev frozen sets currently exist only on jupiter).
- A dashboard that shows, per model and benchmark, the headline metric; lets a
  reader drill into languages and individual runs; and updates when a log lands.
- Zero re-scoring, zero audio indexing, zero writes into the eval path.

## Non-goals

- Sample-level transcript browsing. The dashboard links to the log file and to
  `inspect view`; it does not reimplement it.
- Editing or annotating results. The worklog is read-only by decision.
- Running MELT/SMURF checkpoints. They will be added later by config, not code.
- Multi-machine merging. Day one reads artemis logs only; results from other
  clusters are copied in if and when needed.
- Authentication. The link is shared by the human partner after the fact.

## Anti-goals

- The dashboard re-deriving a metric. If a number is not in an inspect log, it
  shows as pending, never as an estimate.
- A dashboard that reads `results/*.csv` and therefore cannot show failed,
  queued or unscored slices.
- Anything in this repo importing from `melteval` or becoming a
  dependency of the eval path (per AGENTS.md, `projects/` stays out of it).

## Constraints

- artemis GPU work goes through SLURM; freezing and the dashboard are CPU-only
  and may run directly. `gpu-short` on `a6000` is the default tier (4 h).
- artemis has no `MELT_PACK_GPUS`; one `inspect eval` per slice would mean ~100
  SLURM jobs each reloading Canary. A single-GPU pack avoids that.
- The full dev sets are not frozen on artemis; the nyx Shar tree
  (`/mnt/scratch-nyx/giuseppe/melt/melt-data/shar`) has every needed
  `validation`/`dev` split, indexed.
- `scratch-artemis` has ~500 GB free; frozen manifests are megabytes.
- Canary translates only to/from English and covers 25 European languages
  (`bg hr cs da nl en et fi fr de el hu it lv lt mt pl pt ro sk sl es sv ru uk`).

## Approaches considered

**Canary.** (a) Refreeze on artemis and run through `evaluate.py` with a
one-GPU pack — chosen. (b) Run on jupiter, where the sets already exist — ruled
out: the Canary venv and model cache are staged on artemis, and a run should not
change machine for a whole suite. (c) Ad-hoc `inspect eval` per benchmark —
ruled out: no campaign folder, no status/report, no way for the dashboard to
tell a finished run from a crashed one.

**Worklog.** (a) Flask JSON API + vanilla-JS SPA with a SQLite extraction cache —
chosen. (b) Flask + Jinja/HTMX — simpler JS, but server-side re-render makes the
matrix and heatmap interactions slow and awkward. (c) Static generator — easiest
to host, but not living; someone must re-run it after every batch. The chosen
shape is the only one where "drop a log in the folder and the page updates" holds
while remaining one `python app.py` process.

---

# Part A — Canary-1B-v2 on speechval-dev

## A0. Prefetch LibriSpeech (login node, once)

`librispeech-validation-{clean,other}` are `spec:` benchmarks read live from HF,
and compute nodes run `HF_HUB_OFFLINE=1`. The artemis HF cache has no
`openslr/librispeech_asr`, so two of the six wave-1 benchmarks — and the smoke
run — would fail at generation on an offline node:

```bash
python scripts/evaluate.py prefetch configs/eval/speechval-canary.yaml --site artemis
```

Run on the login shell (needs egress), before any `sbatch`.

## A1. Freeze the dev sets on artemis (CPU)

The artemis CoVoST2 set is **not** the suite's set: it is the old five-direction,
10 000-sample campaign set, and none of `covost2-enx-dev`'s directions are in it.
It must be refrozen too, all 36 directions. Use the nyx indexed tree for both
roles; `voxpopuli-dev` reads `$AZEROS_INDEXED_DIR`, the others
`$LOCAL_DATASETS_DIR`.

```bash
source /mnt/scratch-artemis/giuseppe/venvs/melteval/bin/activate
export LOCAL_DATASETS_DIR=/mnt/scratch-nyx/giuseppe/melt/melt-data/shar
export AZEROS_INDEXED_DIR=$LOCAL_DATASETS_DIR
F=/mnt/scratch-artemis/giuseppe/frozen/speechval-dev
for s in fleurs24-st-xen-dev-full fleurs24-st-enx-dev voxpopuli-dev st-eval-campaign-v1-dev; do
  melteval freeze configs/shar/$s.yaml -o $F/$s
done
```

The existing EuroparlST set is complete and artemis-local (72 directions,
36 000 samples, `shar_path`s on nyx) at
`/mnt/scratch-artemis/giuseppe/frozen/speechval/europarl-st-dev`; use it in place
rather than duplicating it into the campaign's root.

Verify expected counts before going further: VoxPopuli 13 034, FLEURS X→en 8 261,
FLEURS en→X 8 819, CoVoST2 61 452 (36 directions), EuroparlST 36 000 (72).
Record each `total_hours` for the runtime estimate. Re-frozen sets replace the
old ones under the same names; keep the previous `st-eval-campaign-v1-dev`
alongside as `.superseded` if any old campaign log must stay reproducible.

Then make the suite runnable without ad-hoc exports by adding the five set paths
to `infra/sites/artemis.sh` (only when the directory exists, so a fresh checkout
still fails loudly rather than silently pointing at nothing):

```bash
export VOXPOPULI_DEV_SET=.../frozen/speechval-dev/voxpopuli-dev
export FLEURS24_ST_XEN_DEV_SET=.../frozen/speechval-dev/fleurs24-st-xen-dev-full
export FLEURS24_ST_ENX_DEV_SET=.../frozen/speechval-dev/fleurs24-st-enx-dev
export COVOST2_DEV_SET=.../frozen/speechval-dev/st-eval-campaign-v1-dev
export EUROPARL_ST_DEV_SET=.../frozen/speechval/europarl-st-dev
```

## A2. Campaign config — `configs/eval/speechval-canary.yaml`

One model, the suite by reference, unsupported directions skipped by explicit
`dataset_id` globs. The skip lists are derived from `canary.py:LANGUAGES` plus its
"English on one side" rule, and are written out rather than computed so a reader
can check them against the model card.

```yaml
name: speechval-canary
output_dir: ${EVAL_ROOT}/speechval-canary

models:
  - name: canary-1b-v2
    model: melt/hf/canary
    venv: /mnt/scratch-artemis/giuseppe/melt-data/pyextras/canary-shim/activate
    args: [-M, batch_size=16]
    instruction:
      asr: "asr|{lang_code}|{lang_code}"
      st: "st|{src_lang_code}|{tgt_lang_code}"

benchmarks: configs/eval/suites/speechval-dev.yaml

skip:
  # FLEURS ga: Irish is not one of the 25 languages canary covers.
  - fleurs-ga_en-dev
  - fleurs-en_ga-dev
  # CoVoST2 directions with a language canary does not cover (ar ca cy fa id ja
  # mn ta tr zh), in both directions of travel.
  - covost2-ar_en-dev
  - covost2-ca_en-dev
  - covost2-cy_en-dev
  - covost2-fa_en-dev
  - covost2-id_en-dev
  - covost2-ja_en-dev
  - covost2-mn_en-dev
  - covost2-ta_en-dev
  - covost2-tr_en-dev
  - covost2-zh_en-dev
  - covost2-en_ar-dev
  - covost2-en_ca-dev
  - covost2-en_cy-dev
  - covost2-en_fa-dev
  - covost2-en_id-dev
  - covost2-en_ja-dev
  - covost2-en_mn-dev
  - covost2-en_ta-dev
  - covost2-en_tr-dev
  - covost2-en_zh-dev
  # Europarl pairs with no English side (56 of the 72), e.g.
  - europarl-st-fr_de-dev
  # ... one line per pair; the 16 en↔X directions stay.

pack:
  time: "04:00:00"
  max_slices: 200
  gpus: 1
```

The full skip list is generated from `canary.py:LANGUAGES` plus its
English-on-one-side rule and pasted into the config, not globbed: a direction
added to the suite later then fails loudly with canary's own
`does not cover language ...` error instead of silently dropping out of the
report. Supported coverage after skipping: LibriSpeech 2, VoxPopuli 14, FLEURS
X→en 22, FLEURS en→X 22, CoVoST2 11 + 5, EuroparlST 8 + 8.

MetricX stays declared for the ST slices (the suite already carries it); the
`score` command runs it in `$METRICS_VENV_PATH`, and both MetricX and COMET
checkpoints are already in the artemis cache.

Canary on EuroparlST is kept for the 16 directions that have English on one side
(8 X→en, 8 en→X), not the whole benchmark.

## A3. Waves and commands

Two waves against the same output folder, so a 4 h pack is enough for each:

```bash
python scripts/evaluate.py plan  configs/eval/speechval-canary.yaml --site artemis
python scripts/evaluate.py run   configs/eval/speechval-canary.yaml --site artemis \
    --benchmarks librispeech-validation-clean librispeech-validation-other \
                 voxpopuli-dev fleurs24-st-xen-dev fleurs24-st-enx-dev europarl-st-dev \
    --limit 20                              # smoke: writes to smoke-limit20/
python scripts/evaluate.py run   configs/eval/speechval-canary.yaml --site artemis \
    --benchmarks librispeech-validation-clean librispeech-validation-other \
                 voxpopuli-dev fleurs24-st-xen-dev fleurs24-st-enx-dev europarl-st-dev \
    --wait
python scripts/evaluate.py run   configs/eval/speechval-canary.yaml --site artemis \
    --benchmarks covost2-dev covost2-enx-dev --wait
python scripts/evaluate.py score configs/eval/speechval-canary.yaml --site artemis --wait
python scripts/evaluate.py report configs/eval/speechval-canary.yaml
```

The pack comes from `pack.gpus: 1` in the config, not an environment variable, so
`plan` and `run` agree on it from one place.

Wave 1 is ≈44 k samples (LibriSpeech 5.6 k, VoxPopuli 13 k, FLEURS ST 17 k,
EuroparlST 8 k); wave 2 is ≈32 k. If wave 1 runs out of wall clock, the next
`run` picks up only the slices without a successful log — no change in procedure.

## A4. Verification

- The five frozen sets exist with the counts above, and `plan` reports only the
  documented language gaps as skipped — no "environment variable is not set".
- LibriSpeech is in the artemis datasets cache before the first `sbatch`
  (`ls .../hf_cache/datasets | grep openslr`).
- `status` shows every supported slice `done`; the only `skipped` entries are the
  documented language gaps.
- One ASR log and one ST log opened by hand: `melteval_slice`, `melteval_task`
  present; `instruction` is the control spec; samples > 0.
- ST logs carry `corpus_bleu`/`corpus_chrf`/`corpus_spbleu` inline and a MetricX
  score after `score`.
- `report` writes `results.xlsx`; the campaign folder is what the worklog points
  at in Part B.

---

# Part B — the worklog

## B1. Where it lives

this repo, a self-contained app:

```
.<!--repo-root-->
  app.py              Flask app + CLI (--root, --port, --host, --interval)
  scanner.py          walk roots, parse inspect logs, extract one row per log
  store.py            SQLite cache keyed by (path, mtime_ns, size)
  views.py            aggregation: matrix, heatmap, feed, filter options
  config.py           YAML/env config: roots, ignore globs, metric preferences
  static/index.html   the SPA shell
  static/app.js       fetch, filter state, render
  static/style.css    the look
  static/vendor/      echarts.min.js, tabulator.min.js (+ css) — vendored, no CDN
  tests/
  README.md
  requirements.txt
```

No `melteval` import. Its only write is the SQLite cache, under
`$WORKLOG_CACHE` (default `/mnt/scratch-artemis/giuseppe/worklog/worklog.sqlite`,
created if missing), with `./.cache/` as a fallback only when that path is not
writable. Never on `$HOME`: the repo lives on GlusterFS and the scanner rewrites
the version row every interval.

## B2. Ingestion

Walk each configured root for `*.json`. A file is an inspect log when it parses
and has an `eval` object; it belongs to the worklog when it carries
`metadata.melteval_benchmark` (or its task starts with `speech-`, for older
campaigns). Everything else is ignored without an error.

Per log, extract:

| field | source |
| --- | --- |
| campaign | `eval.metadata.melteval_campaign`, else the parent of `logs/` |
| model | `eval.metadata.melteval_model`, else `eval.model` |
| benchmark, slice, task | `eval.metadata.melteval_*` |
| language / direction | `eval.task_args.lang` / `dataset_id`, plus a parse of the slice id (`fleurs-de_en-dev` → de→en) |
| status | top-level `status` (`success`, `error`, `cancelled`) |
| samples | `eval.dataset.samples`, `results.completed_samples` |
| created / finished | `eval.created`, max `sample.completed_at` |
| metrics | every `results.scores[].metrics` entry, namespaced by scorer on collision |
| pending metrics | `eval.metadata.melteval_post_scorers` entries absent from metrics |
| log path | absolute path, for drill-down links |

Duplicate policy mirrors `evaluate.py`: per `(campaign, model, benchmark, slice)`
the newest log with `status == success` wins; older successes and all errors are
kept as history for the feed, not shown in the matrix. Smoke runs
(`smoke-limit*`, `smoke/`) are ignored by default (configurable).

Incremental scan: a stat pass over the roots rebuilds only logs whose
`(mtime_ns, size)` changed or that are new; deleted logs are removed. A full
rescan is cheap enough to keep in a background thread every N seconds (default
30).

## B3. Store

SQLite, one file, two tables:

- `runs(id PK, path, mtime_ns, size, campaign, model, benchmark, slice, task,
  src_lang, tgt_lang, status, total_samples, completed_samples, created,
  finished, log_id, n_metrics)`
- `metrics(run_id FK, scorer, metric, value REAL, is_pending INTEGER)`

Plus a small `meta(key, value)` row for the last scan time and a content version
(hash of run ids + mtimes) the API uses for change detection. No audio, no
prompts, no transcripts.

## B4. API

All JSON, all read-only except the refresh endpoint.

- `GET /` — SPA shell.
- `GET /api/snapshot?version=<v>` — `204` when the version is current, else the
  whole payload: filter options, KPI counts, the **winning run per
  `(model, benchmark, slice)` with its metrics**, and the latest 50 runs. The
  slice rows are the contract: the matrix, the heatmap, the language filter and
  the drill-down are all computed from them client-side, so changing benchmark or
  metric never costs a round-trip. A few hundred slices with ten metrics each is
  a few hundred KB.
- `GET /api/runs?campaign=&model=&benchmark=&status=&offset=` — history
  pagination (superseded successes and errors, which the snapshot omits).
- `GET /api/run/<id>` — one log's extracted row, every metric, and its path.
- `POST /api/refresh` — synchronous rescan, returns the new version (used by the
  "Refresh" button and by tests).

Metric selection is data, not code: a `metrics:` block in the config names the
headline metric and direction per task (`asr: {metric: corpus_wer_raw, lower:
true}`, `st: {metric: corpus_bleu, lower: false, also: [metricx, comet]}`), with
a documented fallback to the first numeric metric when a log has none of them.

## B4b. What a matrix cell means

A **column is a benchmark family** (`LibriSpeech`, `VoxPopuli`, `FLEURS X→EN`,
`FLEURS EN→X`, `CoVoST2 X→EN`, `CoVoST2 EN→X`, `Europarl X→EN`,
`Europarl EN→X`), declared in a `families:` block in the config as
`{name, task, direction?, benchmarks}`. A family is not inferred from the
benchmark name: EuroparlST ships all 72 directions under one
`melteval_benchmark`, so a column also selects on direction, derived per slice
from its source/target languages (`tgt == en` → X→EN, `src == en` → EN→X, and
neither → ASR). A new benchmark is assigned to a column deliberately.

A **cell is the macro average of the slice values** in that family for that
model: one value per language/direction, equally weighted, labelled `avg (N)`.
Macro rather than sample-weighted because the suite deliberately caps languages
and directions at different sizes, so weighting would let CoVoST2's cap decide
the FLEURS number. The cell tooltip says "macro average of N slice values"; the
drill-down shows every slice value with its own sample count, and the config's
`aggregate: macro | weighted` switches the default for a reader who wants the
other quantity. Neither is called corpus WER: the per-slice number is the corpus
figure, the cell is an average of corpus figures, and the UI says so.

A cell is `–` when no slice in the family has run, `…` when every present slice
is scored pending, and shows the average over the scored slices (with `k/N` in
the tooltip) when only some are. Failed runs never contribute a value; they mark
the cell.

The snapshot ships both the slice rows and the server-computed matrix for the
**default** metric of each task, so the first paint needs no client maths. When
the reader switches metric (BLEU → chrF → MetricX), the page re-aggregates the
slice rows with one small function; `views.py` implements the identical rule and
the two are cross-checked in the snapshot smoke test. Keeping the rule in the
slice rows, not in a precomputed table, is what lets the heatmap and language
filter work without a round-trip.

## B5. Views

One page, four regions:

1. **Header** — title, last refresh, KPI strip (models, slices scored, pending
   metrics, failed runs), refresh button, source roots in a tooltip.
2. **Filters** — campaign, model family, task, benchmark, language; a search box;
   "only complete slices" toggle. State lives in the URL hash so a filtered view
   can be sent to a collaborator.
3. **Matrix** — rows models (grouped by family: baselines / MELT / SMURF),
   columns benchmark families (B4b), cells the macro-average headline metric.
   Best value per column marked; lower-is-better metrics colour the scale
   inverted; empty = not run; hatched = pending; a small dot marks a failed run
   for that cell. Click a cell → drill-down panel: slice table (one row per
   language/direction, same metric, sample count), the runs behind it, log path.
4. **Heatmap + feed** — the heatmap is models × languages for the selected
   benchmark family and metric (ECharts `heatmap`, sequential palette, numeric
   labels), built from the same slice rows; the feed lists the newest runs with
   status chips, campaign, duration and a path tooltip.

Charts are ECharts; the tables are Tabulator (sortable, resizable, sticky
header). Both vendored under `static/vendor/`. All filtering is client-side on
the snapshot; only the feed paginates.

## B6. Live refresh

Client polls `/api/snapshot?version=` every 10 s; `204` means nothing changed.
The server's scanner thread walks the roots every 30 s, so a log written by a
finishing SLURM job appears without a reload. The page keeps scroll and filter
state across refreshes and flashes changed cells very briefly (a 200 ms
background pulse, not an animation).

## B7. Configuration

`worklog.yaml`, overridable by flags:

```yaml
roots:
  - /mnt/scratch-artemis/giuseppe/melt-data/eval-runs
include: ["speechval-*"]        # campaign folder globs; "*" for everything
ignore: ["**/smoke-limit*/**", "**/smoke/**", "**/packs/**"]
cache: /mnt/scratch-artemis/giuseppe/worklog/worklog.sqlite
port: 8765
host: 127.0.0.1                 # --host 0.0.0.0 when the link is shared
interval_seconds: 30
aggregate: macro                # macro | weighted

# Matrix columns, in display order. A slice matches a family by benchmark and,
# when given, direction (derived from its source/target languages).
families:
  - {name: LibriSpeech, task: asr, benchmarks: [librispeech-validation-clean, librispeech-validation-other]}
  - {name: VoxPopuli, task: asr, benchmarks: [voxpopuli-dev]}
  - {name: FLEURS X→EN, task: st, direction: xen, benchmarks: [fleurs24-st-xen-dev]}
  - {name: FLEURS EN→X, task: st, direction: enx, benchmarks: [fleurs24-st-enx-dev]}
  - {name: CoVoST2 X→EN, task: st, direction: xen, benchmarks: [covost2-dev]}
  - {name: CoVoST2 EN→X, task: st, direction: enx, benchmarks: [covost2-enx-dev]}
  - {name: Europarl X→EN, task: st, direction: xen, benchmarks: [europarl-st-dev]}
  - {name: Europarl EN→X, task: st, direction: enx, benchmarks: [europarl-st-dev]}

# Headline metric per task; `also` are shown in the drill-down.
metrics:
  asr: {metric: corpus_wer_raw, lower: true}
  st:  {metric: corpus_bleu, lower: false, also: [corpus_chrf, corpus_spbleu, metricx]}
```

Default `host` is loopback; sharing is an explicit flag, never a default.

## B8. Failure handling

- Unparseable or half-written logs: skipped, counted, surfaced as a warning in
  the header (a SLURM job writing a log is a real race; the next scan picks it
  up).
- Error logs: never crash the scan; they show in the feed and as a cell marker.
- Missing frozen set / unknown benchmark: irrelevant to the dashboard, which
  groups by `melteval_benchmark` from the log, not by the suite.
- A metric whose value is `NaN` or non-numeric is stored as pending, not zero.

## B9. Tests

`pytest projects/worklog/tests`, no network, no GPU, no real logs required.

- Fixtures: four small hand-built inspect logs (ASR success, ST success with
  `post_scorers` declared but not run, ASR error, ST success superseded by a
  newer log).
- Extraction: fields above, including language/direction parsing and scorer
  namespacing.
- Duplicate precedence: newest success wins; error never shadows a success.
- Pending: declared post-scorer shows pending, not missing.
- Smoke ignore and deleted-log removal.
- Aggregation: headline metric per task, macro vs weighted, lower-is-better
  ordering, empty vs pending, partial coverage `k/N`.
- API: Flask test client returns the slice rows and a server matrix with the
  expected cell values, and `204` on an unchanged version.
- Cache: temp directory in tests; a cold start with an unwritable configured
  path falls back rather than crashing.

Also one test that runs the scanner over a real campaign folder when
`MELTEVAL_WORKLOG_SMOKE_ROOT` is set, skipped otherwise (mirrors the
`MELTEVAL_SHAR_ROOT` convention).

## B10. Deployment

```bash
cd /home/giuseppe/melt-proj/speecheval-worklog
uv pip install -r requirements.txt
python app.py --root /mnt/scratch-artemis/giuseppe/melt-data/eval-runs \
              --cache /mnt/scratch-artemis/giuseppe/worklog/worklog.sqlite
```

CPU-only, loopback by default. `--host 0.0.0.0` plus the human partner's SSH
tunnel or reverse proxy is how it gets shared; the README documents that, and
says plainly that there is no auth.

---

## Left to the builder

- Exact ECharts/Tabulator versions to vendor, and the palette.
- The precise per-slice language parsing fallback when `task_args` is empty
  (older logs) — the slice id is the source of truth there.
- Whether the feed shows durations relative or absolute. Absolute, with relative
  on hover, unless it looks cluttered.
- How the UI affords switching the headline metric (a small selector above the
  matrix, per task) without making the header busy.

---

# Part C — the run inspector

2026-11-14. This supersedes one line of Part B: "sample-level transcript
browsing", ruled out as a non-goal there, turned out to be the question a
reader asks next. Spec:
`docs/superpowers/specs/2026-11-14-run-inspector-design.md`; build plan:
`docs/superpowers/plans/2026-11-14-run-inspector.md`.

## C1. Why it exists

The worklog answers "what score did this slice get". When a score looks wrong,
the next question is "what did the model actually see and say", and the only
ways to ask it were opening a 3–50 MB inspect JSON by hand, or `inspect view`
— which needs the eval venv on the worklog host, shows inspect's raw
structure instead of melteval's meaning, and drops the matrix context. The
inspector is a `/inspect?run=<id>` page that diagnoses one log from the
browser: wrong prompt, wrong language, wrong normalizer, a checkpoint that
emitted garbage, a truncated completion, a broken reference. Scope is one
model × slice log; cross-model comparison stays out.

## C2. Run identity

A URL needs a key, and the snapshot published only a composite string id per
slice while its `runs` list covers just the newest 50 logs — an older
drill-down row had nothing to link with. Slice rows gained exactly one
additive field, `run_id` (the `ScoredRun.row_id`), the only relaxation of the
otherwise frozen payload. The key is the SQLite row id, never a filesystem
path: a path in a URL needs its own escaping and breaks when a log is
rewritten and re-scanned.

## C3. The digest sidecar (`worklog/samples.py`)

Paging a 2000-sample slice through a 50 MB log must not re-parse the log per
request or ship it whole to the browser, so each log is parsed once into a
JSONL digest cached on disk at

```
<cache parent>/samples/<sha1(abspath)[:16]>-<mtime_ns>-<size>-v<SCHEMA_VERSION>.jsonl
```

— beside the SQLite cache (scratch, disposable, never `$HOME`); sample text
never enters SQLite. Line 1 is the header (`_schema`, `_overview`, `_sorts`,
`_flagged`, `_failed`); each following line is one sample: `id`, `epoch`,
`input`, `target`, `messages`, the rendered `prompt` and `format_spec` from
the sample store, `completion`, `stop_reason` (`choices[0]`, falling back to
`output.stop_reason`), `model`, the scorer entries (`value`, `answer`,
`metadata`, history only as its length), `metadata`, timing, `error`, plus
two derived fields. `flags` is any of `error`, `empty`, `truncated` (a
`max_tokens`/`length` stop), `preamble` (a scorer counted preamble tokens);
`wer` is discovered by shape — the first scorer whose value carries integer
`wer_errors` and `ref_words > 0` — not by scorer name, because the scoring
pass varies and `chunked_asr_scorer` (token counts only) simply has none.
`events` are dropped except the first `model` event's config, lifted into the
overview as the effective generate config. `_sorts` lists the sorts that are
meaningful for this log (`worst` only when some sample has a derived WER).

Invalidation lives in the name. The `(mtime_ns, size)` fingerprint means a
changed log hashes to a different file, and the loader re-stats the log before
trusting any sidecar; `SCHEMA_VERSION` does the same job for a changed digest
layout (v2 added the format spec) — old caches can never be served as a wrong
shape, they are simply misses. A rebuild deletes the superseded sidecars of
the same log; and after each fresh build `prune(samples_dir, budget_bytes)`
deletes the oldest sidecars while the directory is over its 1 GB budget. The
sweep is best-effort — failures are swallowed, so pruning can never fail a
request — and runs only on a rebuild, never on a cache hit. The fingerprint
scheme already bounds steady-state growth to one live sidecar per log; the
whole directory is disposable: every digest rebuilds from its log. Writes are
atomic (temp file + `os.replace`) under a per-sidecar lock, so concurrent
requests build a log once. A log over 256 MB (`MAX_LOG_BYTES`) is refused
with a clear reason rather than read into memory: the scanned campaigns top
out around 50 MB, and the alternative is a multi-GB RSS spike on the machine
that also runs the scanner.

The overview also describes the files the eval config pointed at
(`instruction_file`, `system_prompt_file`, `model_config_override`) as
`{path, exists, text, note}` with text capped at 20 KB — and reads them only
when the recorded absolute path resolves under a configured `root`. The app
can be shared with `--host 0.0.0.0` and a log is writable input, so the page
must not become a general file reader. A relative path is reported as
recorded, never resolved (guessing a base directory turns a moved log into a
confident wrong answer); a missing file inside the roots is itself a finding.

## C4. The two endpoints

`worklog/app.py` serves the digest over GET, taking the log path from
`Store.get(run_id)`:

- `GET /api/run/<id>/overview` → `{run, setup, scores, stats, digest:
  {samples, failed, flagged, sorts}}` — everything the setup panel and the
  score tables render.
- `GET /api/run/<id>/samples?offset=0&limit=25&q=&sort=order&filter=all` →
  `{total, offset, limit, samples}` — one page of cards. `limit` clamps to
  1..100, `offset` to ≥ 0; `q` is a case-insensitive substring over id,
  reference, completion and the scorers' answers; `filter=issues` keeps
  flagged samples; the sort (`order`, `worst`, `slowest`, `longest`) runs
  server-side so paging is stable.

Errors are typed and never a 500: unknown run id → 404; vanished log → 404
with the recorded path; oversized, non-JSON, or unreadable log — or a cache
that cannot be rebuilt — → 409 with the reason, so the page says what is
wrong instead of showing a stack trace.

## C5. The page

`static/inspect.html` + `static/inspect.js`: same styles, theme and `util.js`
helpers; no framework, no build step. The header shows `model · benchmark`
with task, status, created/finished, the digest's sample/failed/flagged
counts and the log path. The setup card renders `prompt_style`, the
`instruction` text, `format_spec`, the effective `generate_config`,
`model_args`, scorers, packages and dataset, with the context files as
collapsible blocks; scores render as one small table per scorer. Sample cards
show id, WER (percent, or "–"), time and flag chips, then Reference /
Normalized / Generated / Prompt / Audio — the audio row is the `input`
pointer, not playable — with collapsible messages/scores/metadata. Long text
clamps to five lines behind a "show all" toggle; absent fields render as
"not recorded". When `format_spec.apply_chat_template` is set the page states
that the chat template is applied inside the provider and the messages shown
are pre-template: the templated prompt is not in the log, and this says so
rather than implying otherwise. `run`, `offset`, `q`, `sort` and `filter` live
in the URL; search is debounced and changing it or the sort resets paging to
0; in-flight responses are dropped by sequence number so a slow page cannot
overwrite a newer one; the no-run, no-samples and error states are each one
explanatory line.

## C6. Entry points

The matrix drill-down gained an **Inspect** column whose link is
`/inspect?run=<row.run_id>`, and every run-feed row links the same page with
its existing `id`. Both carry the current filter hash as `back=…` — prefixed
with `/`, because the inspector accepts as `back` only same-origin relative
paths (rejecting `//host…`, absolute URLs and backslashes) and the bare hash
would not survive that check. The "← Results" link therefore restores the
exact filtered view the link came from, instead of guessing at history.

## C7. Deliberate limits

No audio playback (a shar pointer; playing it would pull lhotse and the
corpora into a metadata-only reader). No cross-model comparison, no diff
view, no editing or re-running. No new dependency, no change to the scanner
or the SQLite schema beyond `run_id`. The 256 MB log cap stands — refusing an
outlier with a clear message beats risking the host — and the sidecar cache
is cache: rebuildable, disposable, budgeted.
