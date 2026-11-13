# Worklog view fixes — design

**Date:** 2026-11-13
**Status:** approved (short design, agreed in conversation)

## Intent

Four read-only view fixes in the SpeechEval worklog, driven by how the
`speechval-*` campaigns actually score:

1. LibriSpeech's heatmap is unreadable: `clean` and `other` both carry
   `lang: "en"` and land on the same `(model, "en")` cell.
2. Models that cannot do a task (Cohere Transcribe has no ST) still get an
   all-dashes row in the ST matrix and an empty row in the ST heatmap.
3. The heatmap shows one auto-picked family; switching datasets means using
   the global Benchmark filter, which also shrinks the matrix.
4. MELT/SMURF groups list every checkpoint; only the newest matters for a
   first read.

## What changes

### 1. Heatmap x-axis is the family's variant

When every slice in a family shares one language, the columns are the
benchmarks (LibriSpeech: `clean`, `other`, label derived by stripping the
longest common benchmark prefix). Otherwise columns are languages, as today.
Rows that would still share a cell are averaged rather than overdrawn.

### 2. Unsupported tasks are omitted

A model appears in a task's matrix table and heatmap only if the snapshot has
at least one slice for it in that task. Campaign and model-group filters still
apply. The heatmap model axis uses the same rule.

### 3. One heatmap tab per dataset

The heatmap card gets a tab per configured family that has results under the
current campaign/model-group filters (LibriSpeech, VoxPopuli, FLEURS X→EN,
FLEURS EN→X, CoVoST2 X→EN, CoVoST2 EN→X, Europarl X→EN, Europarl EN→X). Tabs
are local to the heatmap: clicking one does not change the matrix's global
Benchmark filter. A global Benchmark filter, when set, limits the tabs to that
family. The active tab lives in the URL as `heatmap=<family>`.

### 4. Latest checkpoint by default

The snapshot gains `checkpoints: {model: {base, step, latest}}`. A model name
of the form `<base>-step-<n>` is grouped by `(model family, base)`; the highest
`n` is `latest`. Models without a step are always `latest`. The matrix header
gets a **Show all checkpoints** checkbox, off by default, persisted in the URL
as `showAll=1`, and applied to both the matrix and the heatmap model lists.

## Decisions

- Item 2 applies to both results views; "relative result page" was read as the
  matrix and heatmap together.
- Tabs list only families with at least one result under the campaign and
  model-group filters, so a campaign that skipped ST does not show eight empty
  ST tabs.
- The checkbox is global state (one flag), although it is rendered in the
  matrix card header.
- Checkpoint ordering uses the numeric step prefix, not log timestamps: a
  re-scored older checkpoint must not become "latest".

## Non-goals

- No change to ingestion, the SQLite cache, the API shape beyond the new
  `checkpoints` key, or the run feed.
- No new configuration keys.

## Verification

- `pytest tests` (48 currently), including Playwright UI tests with
  `LD_LIBRARY_PATH=$(cat /tmp/opencode/pwlibs.path)`.
- Manual look at `http://127.0.0.1:8765` after the change.
