# SpeechEval worklog

A read-only dashboard over inspect_ai logs from the speechval campaigns. It
answers "how did each model do on each benchmark, and where is the log?" without
re-scoring anything, re-reading audio, or needing the evaluation harness.

```
 models × benchmark families        per-language heatmap        run feed
 ───────────────────────────       ─────────────────────        ────────
 canary-1b-v2  82.1  24.5  …       model × language, WER/BLEU   success / error,
 melt/…        …     …            or COMET, colour by value    newest first
```

## What it is, and is not

- Reads `*.json` inspect logs under one or more **campaign roots**. A log is
  recognised by its `eval.metadata.melteval_*` block (or a `speech-*` task, for
  older logs); anything else is ignored. `results/*.csv` is never read.
- Caches the extraction in SQLite, keyed by path + mtime + size, so a rescan is
  cheap and a new log appears without restarting.
- **Read-only.** The only writes are that cache and the inspector's
  rebuildable digest sidecars. There is no editing, no notes, no re-scoring;
  the sample browser is `/inspect?run=<id>`, served from per-log digests
  cached under `<cache parent>/samples`.
- Light and dark themes, remembered per browser (the toggle sits in the header;
  the first visit follows the OS preference).
- **No authentication.** Bind loopback by default; share deliberately (below).

## Install and run

```bash
cd /home/giuseppe/melt-proj/speecheval-worklog
uv venv .venv && source .venv/bin/activate      # or any Python ≥ 3.10
uv pip install -r requirements.txt

python app.py                       # uses worklog.yaml, 127.0.0.1:8765
python app.py --root /mnt/scratch-artemis/giuseppe/melt-data/eval-runs \
              --cache /mnt/scratch-artemis/giuseppe/worklog/worklog.sqlite \
              --port 8765
```

The cache defaults to `/mnt/scratch-artemis/giuseppe/worklog/worklog.sqlite`
(`$WORKLOG_CACHE` overrides), never `$HOME`: the scanner rewrites it every
interval and the repo checkout lives on GlusterFS.

Sharing: `--host 0.0.0.0` plus an SSH tunnel or a reverse proxy. There is no
login, so only expose it to people you would give the numbers to anyway.

## Configuration

`worklog.yaml`:

| key | meaning |
| --- | --- |
| `roots` | directories whose direct children are campaign folders (`<root>/<campaign>/logs/...`) |
| `include` | campaign-folder globs, e.g. `["speechval-*"]` |
| `ignore` | path-part globs to skip (`**/smoke-limit*/**`, packs, …) |
| `cache` | SQLite path (`${VAR}` and `~` expand) |
| `port`, `host` | bind address; loopback by default |
| `interval_seconds` | background rescan period (the page polls every 10 s) |
| `aggregate` | `macro` (equal weight per language) or `weighted` (by sample count) |
| `families` | matrix columns: `{name, task, benchmarks, direction?}` |
| `metrics` | display label and `lower` direction per metric key |
| `headline` | the metric a matrix cell shows per task |

Add a new campaign: drop its folder under a root and let `include` match it. Add
a new benchmark: add its `melteval_benchmark` id to a family's `benchmarks` (and
set `direction: xen|enx` if one benchmark carries both directions, as
EuroparlST does). No code change.

Each table has its own **metric selector** (BLEU, chrF, spBLEU, MetricX, …),
and slices whose benchmark is not in `worklog.yaml` are listed under
**Unclassified** with their log path instead of vanishing.

The matrix cell is the **macro average of the per-slice values** for that
model and family (or sample-weighted with `aggregate: weighted`), labelled with
`scored/present`. The drill-down shows the per-slice corpus figures, which are
the numbers to quote. A cell is `–` when nothing ran, `…` when the slices are
scored but their post-scorer has not run yet, and `*` when only part of the
family is scored.

### Inspecting a run

`/inspect?run=<id>` opens one log in the browser: the slice's setup (prompt
style, instruction text and the instruction file it came from, format spec,
effective generate config, model args, declared scorers, package versions),
the aggregate scores, and paged per-sample cards — id, WER, time, issue flags
(`error`, `empty`, `truncated`, `preamble`), reference, the normalized answer
the metric used, the raw completion, the rendered prompt, and collapsible
messages/scores/metadata. Reach it from the **Inspect** column of the matrix
drill-down or from any run-feed row; the back link returns to the filters the
link came from.

The limits are the honest ones. Audio is a pointer, not playback — playing it
would mean depending on lhotse and the corpora, which this app does not. When
a checkpoint's chat template is applied inside the provider, the templated
prompt is not in the log, and the page says so rather than pretending the
bare messages are what the model saw. A log over 256 MB is refused with a
clear error, not parsed into memory.

What the page shows comes from a JSONL digest per log cached under
`<cache parent>/samples`: pure cache, rebuildable from the log, safe to delete
entirely. A rebuild names the sidecar by the log's `(mtime, size)` and schema
version, so a changed log never serves a stale digest and the superseded files
for that log are dropped; `worklog.samples.prune` removes the oldest sidecars
across the directory (1 GB budget). `run`, `q`, `sort`, `filter` and `offset`
all live in the URL, so any page of samples is linkable as-is.

## Development

```bash
uv pip install -r requirements-dev.txt
pytest tests -q
```

The browser tests (`tests/test_ui.py`) skip without Playwright. Chromium needs
the system libraries `playwright install-deps` installs; on artemis they are
staged without root:

```bash
mkdir -p /tmp/opencode/pwlibs /tmp/opencode/debs && cd /tmp/opencode/debs
apt-get download libasound2 libatk1.0-0 libatk-bridge2.0-0 libatspi2.0-0 \
    libgbm1 libxcomposite1 libxdamage1 libxfixes3 libxkbcommon0 libxrandr2 \
    libxrender1 libxext6 libxi6 libxtst6 libcairo2 libpango-1.0-0 \
    libpangocairo-1.0-0 libfontconfig1 libgdk-pixbuf-2.0-0 \
    libwayland-server0 libxcb-randr0
for d in *.deb; do dpkg-deb -x "$d" /tmp/opencode/pwlibs; done
export LD_LIBRARY_PATH=$(find /tmp/opencode/pwlibs -type d -name '*-linux-gnu' | tr '\n' ':')
pytest tests -q
```

ECharts is vendored under `worklog/static/vendor/` (no CDN at runtime);
`scripts/vendor.sh` re-downloads the pinned version.

## Layout

```
app.py                  entry point
worklog/config.py       YAML config, defaults, metric/family lookups
worklog/scanner.py      inspect log -> one flat row
worklog/store.py        SQLite cache, duplicate policy
worklog/views.py        family matching, aggregation, snapshot payload
worklog/samples.py      one log -> per-sample digest, sidecar cache
worklog/app.py          Flask routes + scanner thread
worklog/static/         page, style, matrix.js, charts.js, feed.js, app.js
tests/                  unit + API + headless-browser tests
```
