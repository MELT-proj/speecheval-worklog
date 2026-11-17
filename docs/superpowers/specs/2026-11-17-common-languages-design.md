# Common-languages filter — design

**Date:** 2026-11-17
**Status:** awaiting approval

## Intent

The matrix compares models on the aggregate of a benchmark family, but the
aggregate is computed over whatever languages each model happens to have. That
is not a like-for-like comparison: cohere-transcribe covers 7 of the 14
VoxPopuli languages, canary skips a dozen CoVoST2 directions, and a checkpoint
still in flight has half its languages. A cell can therefore look better only
because the model was scored on the easier half.

This adds one checkbox per task — ASR and ST — that restricts the comparison to
the columns every visible model has a result on, and recomputes the aggregates
on that common set. Ticked: "same languages for everyone". Unticked: today's
behaviour, unchanged.

## Goals

- One checkbox per task in the results matrix, ticked state in the URL hash.
- When ticked for a task, the matrix cells, the language heatmap and the cell
  drill-down all restrict to the same common set.
- The restriction is visible, not silent: the cell's `n/total` and a line in the
  drill-down say how many columns survived.
- No server, schema or scanner change.

## Non-goals

- No per-benchmark checkbox. The control is per task, as asked; families are
  still intersected separately (see below).
- No change to the KPI row, the run feed, or the headline-metric selection.
- No persistence beyond the URL hash (no cookie, no server-side setting).
- No new runtime dependency, no build step, no framework.

## Anti-goals

- Promoting a model into the comparison that is not otherwise visible (for
  example unioning across checkpoints to "find" coverage). The filter may only
  remove columns, never add models.
- A checkbox that shrinks the table but not the heatmap: the page would show a
  language in the heatmap that the table just excluded, and contradict itself.

## Definitions

**Column.** A slice's position within its family: its language for VoxPopuli,
FLEURS and CoVoST2/Europarl; its benchmark for LibriSpeech, which has no
language axis (clean vs other). Today this logic lives in `charts.js`
(`languageOf`, `axisFor`, `columnOf`) and decides the heatmap's x-axis; it
becomes shared, so the filter and the heatmap cannot disagree.

**Visible model.** A model that passes the existing campaign, model-group and
"Show all checkpoints" rules for that task (`W.modelVisible`).

**Participating model** (per family). A visible model with at least one row in
that family. A model that never ran the benchmark must not veto it: cohere has
no ST results at all, and requiring it to cover every ST language would empty
every ST table.

**Has a result.** The model has a finite value for **the metric currently
selected** in that task's dropdown. Presence is therefore metric-specific:
WER and CER can admit different language sets, and a MetricX value that is
still pending does not count. The alternative — freezing presence to the
headline metric — was rejected: it would let a cell aggregate a number for a
language where the displayed metric is missing, which is the thing this filter
exists to prevent.

## Behaviour

For a task with the box ticked, and for each family of that task:

1. Take the rows under the existing filters (campaign, model group, language,
   Show all checkpoints), **before** the common filter.
2. Fix the family's axis mode from those rows, and group them by column.
3. Keep only the columns where every participating model has a result.
4. Restrict the family's rows to the kept columns.

That set feeds three consumers: the cell aggregate (so `n/total` becomes
`7/7` where it was `7/14`), the heatmap x-axis and cells, and the drill-down
list. Columns stay in the axis order the heatmap already uses (sorted).

The intersection is **per family**, not per task: one global ASR intersection
would reduce to `en`, because LibriSpeech is English-only, and would delete
VoxPopuli.

Edge cases, all resolved by the rules above:

- **One visible model, or full coverage.** Intersection equals everything; only
  the ticked box changes.
- **A family with no common column left.** The family keeps its column and the
  table shows the existing empty state; the heatmap shows its placeholder with
  a message naming the reason, not a blank chart.
- **A participating model with no finite value for the metric at all** (for
  example every ST MetricX still pending). It vetoes every column, and the
  family empties. That is honest — "every model has a result" is false — and
  unticking restores the old view. The empty-state text says so.
- **The global Language filter is set.** The intersection is computed inside
  the already-filtered universe, so ticking with `de` selected either keeps
  `de` or empties the family. The same empty-state text covers it.
- **LibriSpeech.** Columns are `clean`/`other`; every model runs both, so
  ticking is normally a no-op there. If one is missing, both disappear, which
  is correct for a mean over the two.

## UI

```
Speech recognition · WER (%) ↓   [WER ▾]  [x] common languages only
                     LibriSpeech   VoxPopuli
  smurf-step-600000      1.96        8.41
                             2/2       7/7      ← was 7/14
```

- Label `common languages only`, with a `title` explaining that LibriSpeech
  uses its two benchmark variants as columns instead. One checkbox per task
  block, next to that block's metric dropdown.
- URL hash keys `common-asr=1` and `common-st=1`, alongside the existing
  `metric-<task>` and `showAll`; `Reset` clears them.
- The heatmap hint gains `· common only` while the box for that family's task
  is ticked, so a shrunken axis is explained where it is seen.
- Drill-down header becomes `7 of 14 languages · common only`.

## Implementation shape (approach A, the one picked)

- `util.js` gains the shared pieces: `W.visibleSlices` (the campaign /
  model-group / language filter that `matrix.js` and `charts.js` currently
  duplicate), `W.languageOf`, `W.axisFor`, `W.columnOf`, and
  `W.commonColumns(snapshot, state, task, metric)` returning the kept column set
  per family.
- `matrix.js` and `charts.js` drop their private copies and consume the shared
  ones; both apply the common set when the task's box is ticked.
- `app.js` reads and writes `common-<task>` in the hash, keeps
  `state.common = {asr: bool, st: bool}`, exposes `W.app.setCommon(task, on)`,
  and clears it in `Reset`.

Why not filter `snapshot.slices` centrally in `app.js`: the two task tables
share one snapshot with independent checkboxes, so it would need task-scoped
copies of the list, and the per-family column logic is still required to know
what to remove — same helper, more plumbing. Why not server-side: the state
lives in the hash, so the server would have to ship an availability map for
every family × metric combination, and filtering is the browser's job today.

## Constraints

- Read-only app: no new writes, no new files at runtime.
- Everything reaching `innerHTML` keeps going through `W.escape`; the new
  checkbox carries a fixed label, not user data.
- Rows are already in the snapshot (~7.6k today at 83 checkpoints × 92
  slices); the intersection is a single pass per render and stays client-side.
- Follow the repo's JS style: plain ES2017, no build step, one `W` namespace.

## Tests

Playwright UI tests in `tests/test_ui.py`, using the existing `make_log`
fixture: two models where one covers three languages and the other two.

- Ticking the ASR box turns a cell from `3/3`-style counts into the common set,
  and leaves an unticked ST table untouched.
- The heatmap loses the non-common column and its hint says `common only`.
- The drill-down lists only common languages and its header says `2 of 3`.
- The ticked state survives a reload from the hash and is cleared by `Reset`.
- A family with no common column shows the empty state rather than a blank
  chart.

## Left to the builder

- Exact wording of the two empty-state messages and the checkbox `title`.
- Whether `W.commonColumns` returns a `Map` of `Set`s or a predicate function;
  either is fine as long as it is computed once per render and shared.
- Where in `docs/design.md` the filter gets its paragraph.
