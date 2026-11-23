# Trends page — design

**Status:** draft for approval
**Date:** 2026-11-23
**Branch:** to be cut from `master`

## Intent

The worklog answers "how did this checkpoint do?" one matrix cell at a time. It
cannot answer the question that now matters: **which checkpoint should be
merged?** That needs the shape of a training run, not a single column — where
each lineage peaks, where it regresses, which benchmark and which language is
pulling a checkpoint down, and how early the damage appears.

`/trends` is a read-only companion page to the results matrix. It plots one
line per training lineage over checkpoint steps, one panel per configured
benchmark family, across every task the logs contain, with per-language detail
and a checkpoint leaderboard.

## Goals

1. Show MELT/Fondue and each SMURF lineage as separate lines, over steps, per
   benchmark family and task.
2. Mark each line's best and worst checkpoint, in the metric's own direction.
3. Rank checkpoints across benchmarks into one merge shortlist, with coverage
   visible so a checkpoint cannot win on one easy benchmark alone.
4. Break a benchmark down by language: show the weakest languages now, the
   biggest regressions from their own best, and **when** each regression starts.
5. Work with renamed and newly added campaigns without code changes.
6. Stay read-only, dependency-free at runtime, no JS build step, same login
   shield and theme as the rest of the app.

## Non-goals

- No model merging, training, or job submission from the page.
- No replacement for the matrix, heatmap, run feed or inspector.
- No per-sample browsing here (the inspector owns that).
- No statistical significance testing or confidence intervals: logs do not
  carry per-sample variance in a form this page can use.
- No re-scoring or post-hoc metric computation.

## Anti-goals

- **Do not key a lineage on a campaign name.** Campaign folders are expected to
  be renamed; a model name maps to a configured lineage id, and matches are by
  model name only.
- **Do not treat each model name as a separate line.** `step-108000` and
  `eurollm-speech-16n-step-0108000` are different lineages by the user's
  decision, but the 34 bare `step-N` names are one lineage, not 34 lines.
- **Do not hide missing checkpoints by connecting across them.** A line with a
  hole must show a hole.
- **Do not mix metrics into one axis.** Each panel has one metric; the selector
  changes the metric for its whole task.

## Constraints

- Data comes from the existing SQLite cache through `Store.winning()`; no log
  JSON is read on the request path.
- ECharts is vendored; no CDN, no npm, no bundler.
- The existing auth guard covers every new route automatically. `/static/*`
  stays public.
- Current volume is ~5.5k winning slices, 3 baselines, 7 MELT checkpoints, 84
  SMURF steps in two lineages. Expect more campaigns; the design must stay
  cheap at several times this size.

## Approaches

**A. Server-computed trends feed + client rendering (chosen).**
A new `worklog/trends.py` turns winning rows into lineages, panel points,
leaderboard inputs and language series. `/api/trends` returns a compact payload;
`trends.js` renders it. Lineage folding, de-duplication, aggregation and the
language column live in Python where they can be unit-tested; the page keeps the
main snapshot poll untouched.

**B. Client-side derivation from `/api/snapshot` (rejected).**
No new endpoint, but the snapshot does not carry providers or enough lineage
information, and every folding/dedup rule would live in untested browser code.
It also makes the 10 s snapshot poll pay for data only this page uses.

## Decisions (calls made during design; correct any you disagree with)

- **A line is a lineage**, defined by a configured lineage id that model names
  match into, and labelled by `worklog.yaml`. It is not a campaign.
- **A `trends.lineages` entry is the lineage's identity**, not just a label.
  Every model base matching it lands on the same line, so a model-name rename
  can be absorbed by adding a pattern to the existing entry. Entries carry an
  optional `id` (default: a slug of `name`); the id is stable across label
  edits. Unmatched model bases keep auto-discovered identities.
- **Bare `step-N` models fold into one lineage** whose key is `step`.
  `checkpoint_of` is not changed (the matrix depends on its current `latest`
  semantics); trends uses its own `step_of`.
- **Unconfigured lineages still appear**, with a derived label
  (`SMURF · <base>`), so a new campaign is visible before anyone edits config.
- **Baselines are not trend lines.** They have no steps; the matrix is where
  they belong. (A dotted baseline reference is a possible follow-up, not this
  page.)
- **All discovered lineages are visible by default**, with checkboxes to hide
  them. This is a trends page; hiding a line is one click.
- **The leaderboard ranks across all visible lineages**, not within one, because
  the question is "which checkpoint should be merged", not "which is best per
  lineage". Per-lineage best is still visible on the charts and in the table by
  toggling lines.
- **The composite is mean rank across covered benchmark families**, lower is
  better, with coverage shown. Rank is robust to BLEU-vs-WER scales.
- **"Weak language" means two things, both shown**: worst now in the metric's
  bad direction, and largest regression from that language's own best. Onset is
  computed for the regression case. This avoids guessing which reading was
  intended.
- **Weak/regression/onset are computed in Python**, not the browser, and the
  language endpoint takes the adjustable `threshold` and `n` as query
  parameters. The page debounces a refetch when either changes.
- **`latest` for a language is that language's own last observed point**, not
  the lineage's maximum step. A final partial checkpoint therefore cannot make
  languages it did not evaluate disappear from the lists; the table shows each
  language's `latest_step` so the comparison stays honest.
- **Aggregation follows `config.aggregate`** (`macro` or `weighted`), the same
  rule as the matrix.
- **De-duplication is per evaluated slice**, `(lineage, benchmark, slice, step)`,
  not per language column. LibriSpeech's clean/other benchmarks and Europarl's
  several pairs are all kept; the panel aggregates them, and the language view
  aggregates slices that share a column.
- **Coverage is per metric.** `n`/`total` describe the selected metric, so
  switching to a sparsely scored metric (MetricX, COMET) cannot show the
  headline metric's coverage. `total` means the family's slices observed for
  that lineage and step after de-duplication, matching the matrix.

## Data model

### Lineage

For a winning run:

1. `group = model_family(run.provider or run.model)` (`melt`, `smurf`, ...).
2. `step, base = step_of(run.model)`:
   - `^(.+)-step-(\d+)$` → `(int, base)`, e.g. `fondue-step-3000` → `(3000, "fondue")`.
   - `^step-(\d+)$` → `(int, "step")` — the bare batch series.
   - otherwise → `(None, run.model)`.
3. The first configured entry whose `match` patterns hit `run.model` supplies
   `lineage_id`; without a match, `lineage_id = f"{group}/{base}"`.

Configured lineages, first match wins:

```yaml
trends:
  lineages:
    - {id: melt/fondue, name: MELT/Fondue, match: "^fondue"}
    - {id: smurf/16n, name: SMURF · 16n, match: "^eurollm-speech-16n"}
    - {id: smurf/batch, name: SMURF · batch, match: "^step-\\d+$"}
```

`match` is a regex **or a list of regexes** on the model name. A config entry is
the lineage's identity: several model bases matching the same entry form one
line, so a model-name rename can be absorbed by adding a pattern to the existing
entry. `id` is optional and defaults to a slug of `name`; keeping it stable
means a display-label edit does not change colours, URLs or tests. A model
matches at most one entry. Unknown model bases get
`name = "{group label} · {base}"`.

Only lineages with at least one numeric step are trend candidates. A lineage's
`max_step` is the highest step observed across all of its bases (used by the
progress x-mode); its `steps` are the union of its bases' steps.

Runs with `status != success` never reach `winning()`; runs whose metric is not
finite are skipped.

### De-duplication

`Store.winning()` already keeps one run per `(model, benchmark, slice)`. Two
model names can still evaluate the same checkpoint and slice — e.g.
`eurollm-speech-16n-step-0600000` and `eurollm-speech-16n-step-600000`. Within
one lineage, benchmark, slice and step, keep the row with the newest
`(created, mtime_ns)`.

The key deliberately includes `benchmark` and `slice`, not the language column:
LibriSpeech's clean and other sets, and Europarl's several pairs, are distinct
evaluations and must not overwrite each other. Equal language columns are
aggregated later, in the panel and language aggregations, never discarded.

### Panels

A panel is one configured `Family` (the matrix column). Its task determines the
metric set and default metric:

- `task = family.task` (`chunked_` normalised as today).
- default metric = `config.headline[task]`.
- selectable metrics = `config.metrics` for that task, as the matrix exposes.
- Value at `(lineage, step)` = `aggregate()` over the deduplicated slices for
  that family/step, using `config.aggregate`.
- Coverage is per metric: `n` = slices with a finite value for that metric,
  `total` = slices observed for that lineage/family/step after de-duplication,
  `status` = `ok` / `partial` / `pending` (pending when there are slices without
  the metric and one of them still lists it as pending). A metric with no finite
  value at a step has no point for that metric, not a zero.
- `lower` comes from the metric rule; percent metrics are rendered as
  percentages by the same rule the client already uses
  (`wer`, `cer`, `accuracy`, `unresolved`).

Panels are grouped by task in the UI, in config order, with the same titles the
matrix uses ("Speech recognition", "Speech translation", "Multiple-choice QA").

### Language column

Server-side `language_column(run)`, mirroring `util.js`'s `languageOf`:

- `run.lang` if present;
- `tgt` for `direction == "enx"`;
- `src` for `direction == "xen"`;
- otherwise `tgt` or `src`.

Several slices can share a column (Europarl pairs, for example); their values
are aggregated with `config.aggregate` for that step, never dropped.

Families whose rows have no language variety (LibriSpeech, one `en` column) are
hidden from the Languages tab with an explanation rather than shown as a single
flat line.

### Weak / onset

For one lineage × family × metric and each language with at least two points:

- `best`, `best_step`: best value in the metric's direction.
- `latest`, `latest_step`: the language's **own last observed value**, not the
  value at the lineage's maximum step. A partial final checkpoint leaves the
  unaffected languages on their previous point and shows the step it came from.
- `gap`: distance from best in the bad direction (`latest - best` when lower is
  better, `best - latest` when higher is better; never below 0).
- `worst_now`: the bottom-N languages by `latest` in the bad direction — highest
  WER, lowest BLEU/accuracy — N = 5 by default, adjustable in the UI.
- `regressed`: `gap >= threshold × |best|`; threshold defaults to 10% and is
  adjustable.
- `onset`: first step in the final uninterrupted run of points where the gap is
  at or above the threshold. A language that dips and recovers has its onset at
  the beginning of the last sustained drop, not at the first wobble.

Both lists are shown: "weakest now" sorted worst-first, "biggest regressions"
sorted by gap, with onset step and `latest_step`. This is a heuristic for
attention, not a claim of statistical significance.

## API

### `GET /api/trends`

Optional `version` query parameter; returns `204` when it matches the store
version, exactly like `/api/snapshot`.

```json
{
  "version": "…",
  "generated_at": "…",
  "aggregate": "macro",
  "lineages": [
    {"id": "melt/fondue", "name": "MELT/Fondue", "group": "melt",
     "max_step": 55167, "steps": [3000, 17000]}
  ],
  "tasks": {
    "asr": {"label": "Speech recognition", "metric": "corpus_wer_raw",
            "metrics": [{"name": "corpus_wer_raw", "label": "WER", "lower": true}]}
  },
  "panels": [
    {"family": "LibriSpeech", "task": "asr", "languages": ["en"],
     "points": [{"lineage": "melt/fondue", "step": 3000,
                 "values": {"corpus_wer_raw": 0.11},
                 "coverage": {"corpus_wer_raw": {"n": 1, "total": 1, "status": "ok"}}}]}
  ],
  "checkpoints": [
    {"lineage": "melt/fondue", "model": "fondue-step-3000", "step": 3000,
     "values": {"LibriSpeech": 0.11, "VoxPopuli": 0.13},
     "coverage": {"LibriSpeech": 2, "VoxPopuli": 14}}
  ]
}
```

- `panels[].points[].values` carries every configured metric for that task, so
  switching the metric selector costs no request.
- `panels[].languages` lists the distinct language columns seen for that family;
  the Languages tab uses it to hide single-language families without guessing.
- `coverage` is keyed by metric; `n`/`total`/`status` describe that metric only.
- `checkpoints` is the leaderboard feed: one row per lineage+step, headline
  value per family, and the number of slices behind that value. The client
  computes per-family ranks and the mean rank over the **currently visible**
  lineages, so hiding a line updates the ranking. Ties share the average rank;
  families the checkpoint did not run are excluded from its mean, and
  `coverage` in the leaderboard means the count of families included.

### `GET /api/trends/languages?family=<name>&lineage=<id>&metric=<name>`

Lazy-loaded when the Languages tab opens, so the initial payload stays small.
Same `version` 204 behaviour. `threshold` (fraction of `|best|`, default `0.1`)
and `n` (default `5`) are optional. Returns:

```json
{
  "version": "…",
  "family": "FLEURS XtoEN",
  "lineage": "smurf/16n",
  "task": "st",
  "metric": {"name": "corpus_bleu", "label": "BLEU", "lower": false},
  "points": [
    {"step": 102000, "lang": "de", "value": 31.2, "n": 1, "total": 1}
  ],
  "worst_now": [
    {"lang": "mt", "latest": 12.4, "latest_step": 270000,
     "best": 15.1, "best_step": 102000, "gap": 2.7, "onset": 198000}
  ],
  "regressions": [
    {"lang": "mt", "latest": 12.4, "latest_step": 270000,
     "best": 15.1, "best_step": 102000, "gap": 2.7, "onset": 198000}
  ]
}
```

Both lists are computed server-side from the same directional `gap`; `onset` is
`null` when a language never crosses the threshold. Changing `threshold` or `n`
on the page refetches this endpoint (debounced).

Unknown family, lineage or metric: `404` with an error body. The route is
guarded by the existing login shield.

## UI

### Navigation

A shared `.nav` between the brand and the actions on `index.html`,
`inspect.html` and the new `trends.html`:

```
Results   Trends
```

`aria-current="page"` marks the active entry. `Results` keeps the existing
`/`; `Trends` is `/trends`. The nav wraps under the brand on narrow screens.

### Trends page

```
[ Results | Trends ]                         updated 10:42  ☾  Sign out
Lineages (3/3) ▾  Task [All ▾]  Metric per task [WER ▾] [BLEU ▾] [Acc ▾]
x-axis [Step ▾] [Progress %]     Reset zoom   [Pin checkpoint ▾]
┌ Speech recognition ───────────────────────────────────────────────────┐
│ ┌ LibriSpeech ───────────────┐ ┌ VoxPopuli ──────────────────┐        │
│ │ line per lineage           │ │ ● best   ○ worst            │        │
│ └────────────────────────────┘ └─────────────────────────────┘        │
└────────────────────────────────────────────────────────────────────────┘
┌ Checkpoint leaderboard ───────────────────────────────────────────────┐
│ lineage | step | LibriSpeech | VoxPopuli | FLEURS… | mean rank | cov. │
└────────────────────────────────────────────────────────────────────────┘
  [Benchmarks] [Languages] [Leaderboard]
```

- **Panels**: one ECharts line chart per family that has data, under task
  headings. x is a **value axis** on steps (or 0–100 progress), so a missing
  checkpoint is a real gap. y is the selected metric, auto-scaled, with the
  metric label and direction in the panel title.
- **Series**: one line per visible lineage, a fixed colour per lineage across
  all panels, symbols only when a lineage has few points. `markPoint` marks
  best and worst (direction-aware); labels show the step. One point → no
  markers.
- **Interaction**: legend click hides a lineage everywhere; the lineage dropdown
  does the same for users who prefer checkboxes. Hover crosshair and tooltip show
  lineage, step, value, n/total. x zoom/pan is shared across panels through
  `echarts.connect` plus a `dataZoom` slider on the first panel; y zoom is
  inside-zoom only, with **Reset zoom**. `Reset zoom` also restores y.
- **x-axis mode**: `Progress %` is the default, dividing each step by the
  lineage's own `max_step` so curves with different budgets compare in shape.
  `Step` switches to absolute checkpoints for a concrete merge decision.
- **Pin**: choosing a checkpoint draws a dashed vertical `markLine` on every
  panel and highlights its leaderboard row. This is the "compare these two
  candidates" tool.
- **Leaderboard**: top five by default, expanded to the full list by a button;
  sortable columns, best cell per family marked ● and worst ○, mean rank and
  coverage, and a row click pins that checkpoint. A "minimum coverage" control
  (default 1) lets the user ignore checkpoints that only ran one benchmark.
- **Metric default**: the per-task headline unless `trends.default_metrics`
  overrides it; ST defaults to `metricx` while the matrix keeps BLEU.
- **Languages tab**: family selector (only families with >1 language), lineage
  selector, metric selector, one chart with a line per language. Legend is
  scrollable; hovering isolates a language. The side lists show "weakest now"
  and "biggest regressions" with latest (and its step), best (step), gap and
  onset; N and the regression threshold are small inputs that refetch the
  endpoint (debounced). Clicking a language line or list row isolates it.
- **Empty states**: no stepped rows → "No checkpoints to trend yet"; a task
  with no data is omitted; a family with one point still shows the point and a
  note that a trend needs more checkpoints.
- **Theme**: colours come from CSS variables, as `charts.js` does; re-render on
  `worklog:theme`.
- **State**: hash parameters scoped to the page (`lineages`, `task`, `metric-*`,
  `x`, `pinned`, `lang-family`, `lang-metric`), so a view is linkable. The main
  page's existing hash keys are untouched.
- **Refresh**: poll `/api/trends?version=…` every 10 s like the matrix, and
  keep the visible state; the Refresh button re-scans and reloads.

## Edge cases

- A model-name rename lands on the same configured lineage; an unmatched new
  lineage appears automatically. Campaign-folder renames never affect identity.
- Two names for one checkpoint/step never double-plot inside a lineage.
- A checkpoint that ran 1 of 9 families shows a coverage of 1 and cannot hide
  that fact in the leaderboard.
- A metric with no value at a step is a gap, not a zero; coverage is per metric.
- PolySpeech accuracy and unresolved rate render as percentages; WER/CER too.
- A family with an empty direction filter simply has no panels.
- `/api/trends` must not slow the index snapshot; it is computed on demand.

## Files

- New: `worklog/trends.py` — lineage folding, de-duplication, panel/language
  aggregation, leaderboard feed.
- New: `worklog/static/trends.html`, `worklog/static/trends.js`.
- Modify: `worklog/app.py` — `/trends` page route, `/api/trends`,
  `/api/trends/languages`.
- Modify: `worklog/config.py` — parse `trends.lineages` (compiled regexes,
  id, name, first-match).
- Modify: `worklog.yaml` — the three lineage labels above.
- Modify: `worklog/static/index.html`, `inspect.html`, `trends.html` — shared
  nav.
- Modify: `worklog/static/style.css` — nav, trends grid, language list,
  leaderboard, pinned-row styling.
- Modify: `README.md` — page description and config keys.
- Tests: `tests/test_trends.py` (new), `tests/test_api.py`, `tests/test_static.py`,
  `tests/test_ui.py`, `tests/helpers.py` (fixtures for stepped runs).

## Testing

**Python unit (`tests/test_trends.py`)**

- `step_of` parses `fondue-step-3000`, `eurollm-speech-16n-step-0108000`,
  bare `step-108000`; no suffix → `None`.
- Lineage folding puts all 34 bare `step-N` names in one lineage and keeps the
  `eurollm` lineage separate.
- Config entries define identity: two model bases matching one entry merge into
  one lineage; a list of regexes covers a rename; an unmatched lineage gets a
  derived name.
- De-duplication drops a second model name for the same slice/step but keeps
  LibriSpeech's two benchmarks and Europarl's several pairs.
- Panel aggregation respects `macro` and `weighted`, reports per-metric
  `n`/`total`/`status`, and a missing metric is a gap.
- Best/worst flip with `lower`; a single point gets no markers.
- Language column handles `lang`, `enx` (target), `xen` (source); slices sharing
  a column aggregate rather than overwrite.
- `worst_now` is worst in the bad direction (highest WER, lowest BLEU).
- Onset is the start of the final sustained regression, not the first dip;
  `latest` is each language's own last point, including after a partial final
  checkpoint.
- A checkpoint missing a family is excluded from its mean rank; ties average.

**API (`tests/test_api.py`, `tests/test_static.py`)**

- `/trends` serves the page; `/api/trends` shape and auth (anonymous → 401 /
  redirect as the shield dictates); version match → 204.
- `/api/trends/languages` returns the selected family/lineage/metric, honours
  `threshold` and `n`, and returns both lists; unknown params → 404.

**Browser (`tests/test_ui.py`)**

- Nav link reaches `/trends` and is marked current.
- Panels render, one line per visible lineage, task headings present.
- Unchecking a lineage removes its line; legend click does the same.
- Best/worst markers appear at the right step in the right direction.
- Switching x mode changes the axis domain/type.
- Leaderboard mean rank responds to hiding a lineage.
- Pinning draws the mark line on every panel.
- Languages tab lists weakest/regressed languages with an onset step.
- No console errors; theme toggle repaints.

Use the existing `make_log` helper with stepped model names; no corpus or GPU
needed.

## Rollout

Cut `feature/trends-page` from `master`, implement task-by-task with a failing
test first, run the full suite and `ruff check .`, review, merge fast-forward,
restart the worklog server on `127.0.0.1:8765` preserving `WORKLOG_PASSWORD`.

## What is left to the builder

- Exact colour palette and line/marker styling within the existing theme.
- Panel heights, grid breakpoints and whether task headings collapse.
- Number formatting per metric (decimals, percent) beyond the existing helpers.
- Whether the leaderboard default sort is mean rank or coverage, then mean rank.
- The exact regression-threshold default copy and whether N/threshold persist in
  the hash.
- Polling details and whether trends requests are memoised by store version.
- Accessibility polish (chart `aria-label`s, keyboard legend toggles).
