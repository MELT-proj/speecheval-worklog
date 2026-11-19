# PolySpeech MCQA results and checkpoint step order — design

2026-11-19. Approved in conversation before implementation.

## Intent

Two changes to the read-only worklog:

1. MELT/Fondue and SMURF checkpoints shown in the tables should be ordered by
   their numeric step, ascending, when “Show all checkpoints” is ticked.
2. The 2016 PolySpeech-100 MCQA logs produced by the Eval Session on jupiter
   should be classified and rendered as a normal benchmark table instead of
   appearing under “Unclassified · 2016 slice(s) not in worklog.yaml”.

## What the logs already give us

They are standard inspect logs under
`<root>/speechval-polyspeech/logs/<model>/<benchmark>/<slice>/<log>.json`:

- `melteval_task: audio_mcq`
- `melteval_benchmark: polyspeech-100-pa_qt-200`
- one `melteval_slice` per language (`audio_mcq-polyspeech-pa_qt-<lang>`)
- `mcq_scorer` metrics `choice_accuracy` and `unresolved_rate`
- 84 SMURF checkpoints × 24 languages.

The generic scanner already extracts all of this. There is no new file format to
parse; the gap is configuration and presentation.

## Approaches

- **Extend the config-driven pipeline (chosen).** Add the task, family,
  headline metric and metric labels to `worklog.yaml`; teach the shared client
  renderers about `audio_mcq`. Keeps one ingestion path for every benchmark.
- **A PolySpeech-specific parser/reader (rejected).** It would duplicate the
  generic inspect-log scanner for a log that already matches the standard
  shape, and would be a second place to keep language/scoring rules.
- **Read `results/summary.csv` (rejected).** The worklog is deliberately
  log-only; a CSV path cannot show failed, pending or superseded runs.

## Design

### Checkpoint order

`views.build_snapshot` currently publishes `filters.models` as a plain
alphabetical set. It will sort by, in order:

1. model group (`baseline`, `melt`, `smurf`, `other`),
2. base checkpoint name (`checkpoint_of`),
3. numeric step ascending (models without a step use `-1`),
4. the model name as a tie-breaker.

The existing “latest checkpoint” rule is unchanged. Because the matrix,
heatmap and filters all consume `filters.models`, the order is consistent
everywhere, both with and without “Show all checkpoints”.

### PolySpeech configuration

`worklog.yaml` gains:

```yaml
metrics:
  choice_accuracy: {label: Accuracy, lower: false}
  unresolved_rate: {label: Unresolved, lower: true}

headline:
  audio_mcq: choice_accuracy

families:
  - {name: PolySpeech, task: audio_mcq, benchmarks: [polyspeech-100-pa_qt-200]}
```

No schema change: the scanner already stores those numeric metrics.

### Presentation

- `matrix.js` adds the `audio_mcq` → “Multiple-choice QA” table.
- `app.js` adds the task-filter label.
- Accuracy and unresolved metrics render as percentages in the matrix and
  heatmap, alongside the existing WER/CER handling.
- The inspector shows ✓ correct / ✗ incorrect / unresolved for a sample whose
  MCQ score carries `value.correct` / `value.resolved`, instead of a WER dash.
- The existing drill-down and language heatmap work unchanged: each language
  slice has `lang`, because `audio_mcq` has no src/tgt pair.

## Goals

- All 2016 PolySpeech slices classified under one `PolySpeech` family.
- One row per checkpoint, in ascending step order when all are shown.
- Accuracy visible as a percentage in the table, drill-down and heatmap.

## Non-goals

- Re-scoring, reading audio, reading CSV/XLSX reports.
- Changing the scorer or the evaluation harness.
- Authentication, caching or write-path changes.
- A general “arbitrary new task” framework; the existing config schema already
  covers this case.

## Verification

- Unit: `snapshot.filters.models` numeric ordering for mixed step names,
  including `fondue-step-3000` before `fondue-step-26000`.
- Unit/config: an `audio_mcq` run matches the PolySpeech family and the
  `audio_mcq` matrix uses `choice_accuracy`.
- Browser: an MCQA fixture renders the QA table, shows accuracy as a
  percentage, and leaves no unclassified rim.
- Full suite green, `ruff check .` clean.
