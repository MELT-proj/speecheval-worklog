# Test results page — design

Date: 2026-11-24
Status: approach, families, trends scope and data provenance confirmed in
conversation; builder-check answers folded in; builder check of the updated
doc pending

Companion design: melt-eval `docs/superpowers/specs/2026-11-24-speechval-test-suite-design.md`
defines the `speechval-test` suite whose benchmark ids this page classifies.
The suite lands first.

## Intent

Everything the worklog shows today is validation: the matrix, the feed, the
KPIs and the trends all read the `speechval-*` campaigns, whose benchmark ids
name validation splits (`librispeech-validation-clean`, `covost2-dev`,
`fleurs24-st-xen-dev`, …). The upcoming `speechval-test*` campaign (melt-eval
suite `speechval-test`) will produce the same kind of inspect logs under
test-split benchmark ids, and the worklog needs a second place to visualise
them, mirroring the Results page, without disturbing anything that exists.

Why now: the test campaign's numbers are the ones the final report quotes;
they should be visible the way the validation sweep is, not by grepping logs.

## Goals

- A **🚀 Test** nav page with the same structure as Results: KPIs, family
  matrix, filters, feed — reading test-split logs only.
- The existing Results page stays byte-identical: after the test campaign
  runs, no test log may appear on it.
- Adding or reclassifying a test benchmark later is a `worklog.yaml` edit,
  not a code change.

## Non-goals

- No test variant of Trends, its leaderboard or the language explorer: test
  runs will cover final checkpoints, not the sweep. Trends stays
  validation-only (user decision).
- No merged validation+test columns in one matrix.
- No changes to the run inspector (`/inspect`); it is per-run and
  split-agnostic.

## Approaches

- **A — a 🚀 Test nav page reusing the Results view** *(chosen)*. One
  template, one JS bundle, one endpoint with a `split` parameter; a second
  nav entry. The URL (`/test`) says what the numbers are; the validation page
  is untouched; each page keeps its own KPIs/feed.
- **B — a Validation/Test toggle inside Results.** Same code path, but the
  toggle lets KPIs and the feed silently change meaning, and the Results
  header is already busy. Rejected.
- **C — extra test columns inside the one matrix.** Doubles the width of an
  already-wide table and mixes two meanings into one glance. Rejected.

Efficiency was a wash between A and B — both read the same SQLite cache and
hit the same endpoint — so the choice was purely UX, and the user picked A.

## Design

### Split identity: derived from config, never from names

A log's split is a pure function of its **benchmark id** against
`worklog.yaml` — nothing changes in the scanner, `ScannedRun` or the store
schema, and there is no cache migration. The rule, applied at view time:

- `test` iff the benchmark id appears in some family's `test_benchmarks`
  (new optional family key) **or** in the new top-level `test_benchmarks`
  feed-only list;
- `validation` otherwise — which is what everything shown today already is.

The name-marker fallback from the first draft is dropped: a split that
depended on substring matching would make the feed's SQL filter and the
matrix's Python filter drift apart, and a benchmark id listed in both a
validation list and a test list (the `covost2`/`covost2-dev` typo class)
becomes a **config-load `ValueError`** instead of a silent
misclassification. The ids are exactly the suite's `name:` entries, so the
config is checked once and stays honest.

`speechval-dev-test` (the 5-sample smoke campaign) is not a trap: its logs
reuse the dev suite's `-dev` benchmark ids, and split never looks at the
campaign name.

### Config

`Family` gains `test_benchmarks: tuple[str, ...] = ()`; the config gains a
top-level `test_benchmarks: [...]` for test benchmarks that have **no
validation matrix column** — they appear in the Test feed and inspector
only. Seeded from the `speechval-test` suite (mirror-the-current-columns
decision — every matrix family gets a test list, empty ones included, so the
column exists and shows "not run"):

| family | `test_benchmarks` |
|---|---|
| LibriSpeech | `librispeech-test-clean`, `librispeech-test-other` |
| VoxPopuli | `voxpopuli-test` |
| FLEURS XtoEN | `fleurs24-st-xen-test` |
| FLEURS ENtoX | `fleurs24-st-enx-test` |
| CoVoST2 XtoEN | `covost2-test` |
| CoVoST2 ENtoX | `covost2-enx-test` |
| Europarl XtoEN | `europarl-st-test` |
| Europarl ENtoX | `europarl-st-test` |
| Europarl other pairs | `europarl-st-test` |
| PolySpeech | *(empty — not on the test list)* |

Top-level feed-only test ids: `mls-test`, `fleurs24-asr-test`,
`mcif-chunked-asr`, `mcif-chunked-st`, `mcif-sfp`, `air-bench-chat-speech`,
and later `voicebench`, `advbench`, `redvox` when their sources exist. These
have no validation counterpart, so they get no column; on the Results page
they never appear at all (user decision: feed-only on Test).

**Campaign glob.** The test campaign will be named `speechval-test*`, which
`include: ["speechval-*"]` already matches — no change. The older non-speechval
test backfills (`baselines-fill-*`, `comet-backfill`, `mcif-*`, v1's
`baselines-v1`/`smurf`) stay out on purpose (user decision: only the new
campaign counts).

### Views

`match_family` takes the split: a validation matrix matches only
`family.benchmarks`, a test matrix only `family.test_benchmarks`. The
payload builder (`views.py`) takes a `split` argument. **The filter sits on
the input runs, not on the payload's outputs**: `app.py`'s snapshot passes
`build_snapshot` only the winning runs of the requested split, so the
derived models list, campaign/model/language filters, model families and
checkpoint flags all see just that split too — a test-only model or the
`speechval-test` campaign can never leak into the validation page's model
list or KPIs. KPIs, slices, matrix cells and feed rows follow from the same
filtered input. A family with no test logs renders a column of "not run"
cells — the mirror decision wants the column present.

### Split-aware feed and KPI (builder-check finding)

The snapshot currently feeds `build_snapshot` with `store.history(limit=50)`
— the 50 newest runs of any split — and `failed=store.count(status="error")`
split-agnostically; filtering those after the fact would leave the Test feed
permanently stale and its error count wrong. Instead `Store.history` and
`Store.count` grow an optional benchmark-set filter (`IN`/`NOT IN` built from
the config's test ids), and `app.py`'s snapshot passes the right set per
split: the Test page shows the newest 50 *test* runs and its own failed-run
count, and the validation page's numbers are bit-for-bit what they are today
(its exclusion set contains no id any current campaign produces).

### Routes and page

- `/test` serves the same static `index.html` as `/`; the page detects its
  split from the URL path (`/test` vs `/`) in JS — no server-injected flag,
  no template change. `/` keeps serving validation.
- `/api/snapshot` gains `?split=test` (default `validation`, so nothing
  existing changes behaviour; an unknown value is a 400, not a silent
  fallback).
- Nav gains **🚀 Test** between Results and Trends; `aria-current` is set
  by JS from the path (the page is fully JS-rendered already).
- `app.js` computes the split from `location.pathname`, passes it to every
  poll/refresh call, and shows a "Test splits" badge in the page header so
  a screenshot is self-identifying.

### Trends

`build_trends` keeps reading only validation runs: test benchmark ids are in
no family's `benchmarks` list, so they match no panel. A regression test pins
this so a future family edit cannot leak test numbers into trend lines by
accident.

## Constraints

- Read-only app, config-driven, no new runtime dependencies, no JS build
  step; password auth and loopback bind unchanged.
- The benchmark ids in `worklog.yaml` must match the `speechval-test` suite
  exactly; unit tests assert the seeded ids against the config as shipped.
- The worklog config edit happens **after** the suite file lands in
  melt-eval, so the two files cannot drift.

## Testing

- Unit: split inference (family list, feed-only list, default), config
  parse + the both-lists `ValueError`, `match_family` per split, payload
  filtering per split, split-aware `history`/`count` queries.
- Unit: `build_trends` ignores a run whose benchmark is only in a test list.
- Playwright: `/test` renders the same structure (KPIs, matrix, feed) with
  `aria-current` on 🚀 Test; a test-split log appears only on `/test` and a
  validation log never on it; the validation page is unchanged.
