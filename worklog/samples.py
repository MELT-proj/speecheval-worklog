"""Turn one inspect_ai log into the per-sample digest the ``/inspect`` page renders.

Pure extraction: everything here takes already-parsed log content and returns
plain dicts — no file I/O. The sidecar cache that turns these functions into a
persisted digest joins this module later; this file defines the shape of one
digest, so it is the contract the API and the page build on.

Two things worth knowing before changing anything here:

- A log's ``samples[].scores`` is ``{scorer: {"value", "answer", "metadata",
  "history"}}``, and ``value`` is itself a dict for ASR (error counts) and ST
  (token counts). The WER is discovered by shape — integer ``wer_errors`` and
  ``ref_words`` — not by scorer name, because the scorer differs per task and
  per rescoring pass.
- ``context_file`` must never read outside the configured ``roots``: the app
  can be shared with ``--host 0.0.0.0`` and the log is writable input, so the
  page must not become a general file reader.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

#: Bumped whenever the digest layout changes; part of the sidecar name so old
#: caches invalidate themselves instead of being parsed into wrong digests.
SCHEMA_VERSION: int = 1

#: A log above this size is refused rather than read into memory.
MAX_LOG_BYTES: int = 256 * 1024 * 1024

#: Cap on a context file's text in the digest; the setup panel is a preview.
MAX_CONTEXT_BYTES: int = 20 * 1024

#: Stop reasons that mean the model ran out of room rather than chose to stop.
_TRUNCATED_STOP_REASONS = ("max_tokens", "length")

#: The flags a sample can carry, in the order they are reported.
_FLAG_ORDER = ("error", "empty", "truncated", "preamble")


def _is_int(value: object) -> bool:
    """``True`` for a real ``int`` — ``bool`` is an ``int`` subclass but never a count."""
    return isinstance(value, int) and not isinstance(value, bool)


def _score_value(entry: object) -> dict | None:
    """The ``value`` dict of one scorer entry, or ``None`` if there is not one."""
    if isinstance(entry, dict) and isinstance(entry.get("value"), dict):
        return entry["value"]
    return None


def per_sample_wer(scores: dict) -> float | None:
    """Per-sample WER from the first scorer whose ``value`` carries ASR counts.

    Found by shape, not by scorer name: a slice may be scored by ``asr_scorer``,
    by ``chunked_asr_scorer`` (token counts only, so no WER) or by a rescoring
    pass. Returns ``wer_errors / ref_words``, or ``None`` when no scorer has
    the pair or ``ref_words`` is 0 (the scorer's empty-reference skip path).
    """
    for entry in scores.values():
        value = _score_value(entry)
        if value is None:
            continue
        errors, ref_words = value.get("wer_errors"), value.get("ref_words")
        if _is_int(errors) and _is_int(ref_words) and ref_words > 0:
            return errors / ref_words
    return None


def _stop_reason(output: dict) -> str | None:
    """``output.choices[0].stop_reason``, falling back to ``output.stop_reason``."""
    choices = output.get("choices") or []
    if choices and isinstance(choices[0], dict):
        reason = choices[0].get("stop_reason")
        if reason is not None:
            return reason
    return output.get("stop_reason")


def _has_preamble(scores: dict) -> bool:
    """``True`` when any scorer counted non-zero preamble tokens."""
    for entry in scores.values():
        value = _score_value(entry)
        if value is not None and _is_int(value.get("preamble")) and value["preamble"] > 0:
            return True
    return False


def sample_record(sample: dict) -> dict:
    """One log ``samples[]`` entry, reduced to what a sample card renders.

    Keeps the transcript-side fields verbatim — ``messages``, ``metadata``, the
    scorer ``value``/``answer`` — and adds the derived ``flags`` and ``wer``.
    Drops ``events`` and the raw ``history`` (only its length survives).
    """
    output = sample.get("output") or {}
    store = sample.get("store") or {}
    scores: dict[str, dict] = {}
    for scorer, entry in (sample.get("scores") or {}).items():
        entry = entry if isinstance(entry, dict) else {}
        scores[scorer] = {
            "value": entry.get("value"),
            "answer": entry.get("answer"),
            "metadata": entry.get("metadata"),
            "history": len(entry.get("history") or []),
        }

    completion = output.get("completion")
    stop_reason = _stop_reason(output)
    error = sample.get("error")
    raised = {
        "error": bool(error),
        "empty": completion is None or (isinstance(completion, str) and not completion.strip()),
        "truncated": stop_reason in _TRUNCATED_STOP_REASONS,
        "preamble": _has_preamble(scores),
    }
    return {
        "id": sample.get("id"),
        "epoch": sample.get("epoch"),
        "input": sample.get("input"),
        "target": sample.get("target"),
        "messages": sample.get("messages"),
        "prompt": store.get("melteval:prompt"),
        "format_spec": store.get("melteval:format_spec"),
        "completion": completion,
        "stop_reason": stop_reason,
        "model": output.get("model"),
        "scores": scores,
        "metadata": sample.get("metadata"),
        "timing": {
            "total_time": sample.get("total_time"),
            "working_time": sample.get("working_time"),
            "started_at": sample.get("started_at"),
            "completed_at": sample.get("completed_at"),
            "turn_count": sample.get("turn_count"),
            "retries": len(sample.get("error_retries") or []),
        },
        "error": error,
        "flags": [name for name in _FLAG_ORDER if raised[name]],
        "wer": per_sample_wer(scores),
    }


def sorts_for(samples: list[dict]) -> list[str]:
    """Sort keys the page offers for this log; ``worst`` only when some sample has a WER."""
    sorts = ["order"]
    if any(record.get("wer") is not None for record in samples):
        sorts.append("worst")
    return sorts + ["slowest", "longest"]


def _haystack(record: dict) -> str:
    """Everything the free-text search looks at, lowercased and newline-joined."""
    parts = [
        str(record.get("id") or ""),
        str(record.get("target") or ""),
        str(record.get("completion") or ""),
    ]
    for entry in (record.get("scores") or {}).values():
        answer = entry.get("answer") if isinstance(entry, dict) else None
        if answer is not None:
            parts.append(str(answer))
    return "\n".join(parts).lower()


def _sorted(rows: list[dict], sort: str) -> list[dict]:
    """Apply one sort; ties keep digest order, an unknown sort means digest order."""
    if sort == "worst":
        return sorted(rows, key=lambda row: (row.get("wer") is None, -(row.get("wer") or 0.0)))
    if sort == "slowest":
        return sorted(rows, key=lambda row: -((row.get("timing") or {}).get("total_time") or 0.0))
    if sort == "longest":
        return sorted(rows, key=lambda row: -((row.get("metadata") or {}).get("duration") or 0.0))
    return rows


def select(samples: list[dict], q: str = "", sort: str = "order", issues_only: bool = False) -> list[dict]:
    """Filter and order digests for one page; sorting is server-side so paging stays stable.

    ``q`` is a case-insensitive substring over id, reference, completion and the
    scorers' answers; ``issues_only`` keeps flagged samples; ``sort`` is one of
    :func:`sorts_for`'s keys (anything else falls back to digest order).
    """
    rows = list(samples)
    if issues_only:
        rows = [row for row in rows if row.get("flags")]
    if q:
        needle = q.lower()
        rows = [row for row in rows if needle in _haystack(row)]
    return _sorted(rows, sort)


def context_file(recorded: str | None, roots: Sequence[Path]) -> dict | None:
    """Describe one file the eval config pointed at — without reading arbitrary disk.

    ``None`` in, ``None`` out (the arg was not set). A relative path is reported
    as recorded, never resolved: silently guessing a base directory would turn a
    moved log into a confident wrong answer. An absolute path is statted, but
    its text is only read when it resolves under one of ``roots``; the returned
    dict is ``{path, exists, text, note}`` with the text capped at
    ``MAX_CONTEXT_BYTES``.
    """
    if recorded is None:
        return None
    info: dict = {"path": recorded, "exists": None, "text": None, "note": ""}
    path = Path(recorded)
    if not path.is_absolute():
        info["note"] = "relative path, not resolved"
        return info
    resolved = path.resolve()
    allowed = any(resolved.is_relative_to(Path(root).resolve()) for root in roots)
    info["exists"] = path.exists()
    if not allowed:
        info["note"] = "outside the configured roots"
        return info
    if not info["exists"]:
        return info
    try:
        data = path.read_bytes()
    except OSError:
        info["note"] = "unreadable"
        return info
    if len(data) > MAX_CONTEXT_BYTES:
        data = data[:MAX_CONTEXT_BYTES]
        info["note"] = "truncated"
    info["text"] = data.decode("utf-8", errors="replace")
    return info


def _first_model_config(samples: list[dict]) -> dict | None:
    """The effective generate config: the ``config`` of the first ``model`` event."""
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        for event in sample.get("events") or []:
            if isinstance(event, dict) and event.get("event") == "model":
                config = event.get("config")
                return config if isinstance(config, dict) else None
    return None


def overview_of(body: dict, roots: Sequence[Path]) -> dict:
    """The setup half of the digest: everything about the run except its samples.

    ``generate_config`` is the ``config`` of the first ``model`` event (e.g. the
    effective ``max_tokens``), which is where inspect records what generation
    actually used; ``scores`` stays raw because the results table renders it.
    """
    ev = body.get("eval") or {}
    task_args = ev.get("task_args") or {}
    model_args = ev.get("model_args") or {}
    dataset = ev.get("dataset") or {}
    scorers = []
    for scorer in ev.get("scorers") or []:
        if not isinstance(scorer, dict):
            continue
        scorers.append({
            "name": scorer.get("name"),
            "metrics": [
                metric.get("name") if isinstance(metric, dict) else metric
                for metric in scorer.get("metrics") or []
            ],
        })
    return {
        "task": ev.get("task"),
        "model": ev.get("model"),
        "task_args": task_args,
        "model_args": model_args,
        "model_generate_config": ev.get("model_generate_config"),
        "eval_config": ev.get("config"),
        "packages": ev.get("packages"),
        "dataset": {
            "name": dataset.get("name"),
            "location": dataset.get("location"),
            "samples": dataset.get("samples") or 0,
        },
        "scorers": scorers,
        "generate_config": _first_model_config(body.get("samples") or []),
        "scores": (body.get("results") or {}).get("scores"),
        "stats": body.get("stats"),
        "context_files": {
            "instruction_file": context_file(task_args.get("instruction_file"), roots),
            "system_prompt_file": context_file(model_args.get("system_prompt_file"), roots),
            "model_config_override": context_file(model_args.get("model_config_override"), roots),
        },
    }
