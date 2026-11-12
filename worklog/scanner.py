"""Turn inspect_ai log files into one flat row each.

The worklog reads logs, not the evaluation harness: a log is interesting when it
carries ``metadata.melteval_*`` (written by the speech task), and every field here
comes from the log itself. A log that is unparseable or half-written returns
``None`` and is retried on the next scan.
"""

from __future__ import annotations

import fnmatch
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config


#: ``dataset_id`` / slice ids end in ``-<src>_<tgt>``, optionally with a region
#: code on either side (``zh-CN``) and a ``-dev``/``-test`` suffix.
_PAIR_RE = re.compile(r"-([a-z]{2}(?:-[A-Z]{2})?)_([a-z]{2}(?:-[A-Z]{2})?)(?:-(?:dev|test))?$")


@dataclass(frozen=True)
class ScannedRun:
    """One inspect log, flattened to what the matrix and the feed need."""

    path: str
    mtime_ns: int
    size: int
    campaign: str
    model: str
    benchmark: str
    slice: str
    task: str
    src: str | None
    tgt: str | None
    status: str
    total_samples: int
    completed_samples: int
    created: str | None
    finished: str | None
    log_id: str | None
    provider: str = ""
    lang: str | None = None
    metrics: dict[str, float] = field(default_factory=dict)
    pending: tuple[str, ...] = ()
    row_id: int | None = None


def model_family(model: str) -> str:
    """Row group in the matrix: a Hub baseline, a MELT checkpoint, or a SMURF one."""
    if model.startswith("smurf/"):
        return "smurf"
    if model.startswith("melt/hf/"):
        return "baseline"
    if model.startswith("melt/"):
        return "melt"
    return "other"


def direction_of(src: str | None, tgt: str | None) -> str | None:
    """``xen`` for X→en, ``enx`` for en→X, ``None`` otherwise (including ASR)."""
    if src and tgt:
        if tgt == "en" and src != "en":
            return "xen"
        if src == "en" and tgt != "en":
            return "enx"
    return None


def parse_pair(
    dataset_id: str | None,
    slice_id: str | None,
    task: str,
    sample_meta: dict | None,
) -> tuple[str | None, str | None]:
    """Source and target language codes for one slice.

    The per-sample metadata is the most reliable source; older logs fall back to
    the ``dataset_id`` and then the slice id. ASR has no direction.
    """
    if task in ("asr", "chunked_asr"):
        return (None, None)
    if sample_meta:
        src, tgt = sample_meta.get("src_lang"), sample_meta.get("tgt_lang")
        if src and tgt:
            return (src, tgt)
    for candidate in (dataset_id, slice_id):
        if not candidate:
            continue
        match = _PAIR_RE.search(candidate)
        if match:
            return (match.group(1), match.group(2))
    return (None, None)


def _flatten_metrics(scores: list[dict]) -> dict[str, float]:
    """Every scorer's numeric metrics; namespaced only when two scorers collide."""
    seen: dict[str, int] = {}
    for score in scores:
        for name in (score.get("metrics") or {}):
            seen[name] = seen.get(name, 0) + 1
    out: dict[str, float] = {}
    for score in scores:
        scorer = score.get("name") or "scorer"
        for name, value in (score.get("metrics") or {}).items():
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            key = f"{scorer}.{name}" if seen.get(name, 0) > 1 else name
            out[key] = float(value)
    return out


def scan_run(path: Path) -> ScannedRun | None:
    """Read one log; ``None`` when it is not a speech log, or not readable yet."""
    try:
        body = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ev = body.get("eval")
    if not isinstance(ev, dict):
        return None
    meta = ev.get("metadata") or {}
    benchmark = meta.get("melteval_benchmark")
    if not benchmark:
        return None

    task = str(meta.get("melteval_task") or "")
    slice_id = str(meta.get("melteval_slice") or "")
    args = ev.get("task_args") or {}
    sample_meta = (body.get("samples") or [{}])[0].get("metadata") if body.get("samples") else None
    src, tgt = parse_pair(args.get("dataset_id"), slice_id, task, sample_meta)
    # A language only for ASR: an ST sample's `lang` is its target, which would
    # make a X->en heatmap collapse into one "en" column.
    lang = None
    if src is None and tgt is None:
        lang = str(args.get("lang") or (sample_meta or {}).get("lang") or "") or None

    results = body.get("results") or {}
    scores = results.get("scores") or []
    metrics = _flatten_metrics(scores)
    got_scorers = {s.get("name") for s in scores}
    declared = [part.split("@")[-1] for part in str(meta.get("melteval_post_scorers") or "").split(",") if part]
    pending = tuple(name for name in declared if name not in got_scorers)

    stat = Path(path).stat()
    return ScannedRun(
        path=str(Path(path).resolve()),
        mtime_ns=stat.st_mtime_ns,
        size=stat.st_size,
        campaign=str(meta.get("melteval_campaign") or ""),
        model=str(meta.get("melteval_model") or ev.get("model") or "unknown"),
        benchmark=str(benchmark),
        slice=slice_id,
        task=task,
        src=src,
        tgt=tgt,
        status=str(body.get("status") or "unknown"),
        total_samples=int((ev.get("dataset") or {}).get("samples") or results.get("total_samples") or 0),
        completed_samples=int(results.get("completed_samples") or 0),
        created=ev.get("created"),
        finished=(body.get("stats") or {}).get("completed_at"),
        log_id=ev.get("eval_id"),
        provider=str(ev.get("model") or ""),
        lang=lang,
        metrics=metrics,
        pending=pending,
    )


def _ignored(path: Path, patterns: tuple[str, ...]) -> bool:
    for pattern in patterns:
        bare = pattern.removeprefix("**/").removesuffix("/**")
        if any(fnmatch.fnmatch(part, bare) for part in path.parts):
            return True
    return False


def iter_log_paths(config: Config) -> Iterator[Path]:
    """Every candidate log under the configured campaign folders, once."""
    seen: set[Path] = set()
    for root in config.roots:
        if not root.is_dir():
            continue
        for campaign_dir in sorted(root.iterdir()):
            if not campaign_dir.is_dir():
                continue
            if not any(fnmatch.fnmatch(campaign_dir.name, pattern) for pattern in config.include):
                continue
            for path in sorted(campaign_dir.rglob("*.json")):
                if _ignored(path, config.ignore) or path in seen:
                    continue
                seen.add(path)
                yield path
