"""Aggregation from winning log rows to the payload the page reads.

The matrix is one column per configured family and one cell per
``(model, family)``: the aggregate of the per-slice values, labelled with how
many slices went into it. A cell knows the difference between "not run",
"scored but the post-scorer has not run" and "partially scored".
"""

from __future__ import annotations

from collections.abc import Sequence

from .config import Config, Family
from .scanner import ScannedRun, direction_of, model_family


def _task_key(task: str) -> str:
    return task.removeprefix("chunked_")


def match_family(
    families: Sequence[Family], benchmark: str, task: str, direction: str | None
) -> Family | None:
    """The configured column a slice belongs to, or ``None`` if unclassified."""
    for family in families:
        if family.task != _task_key(task) or benchmark not in family.benchmarks:
            continue
        if family.direction is None or family.direction == direction:
            return family
    return None


def family_of(config: Config, run: ScannedRun) -> Family | None:
    return match_family(config.families, run.benchmark, run.task, direction_of(run.src, run.tgt))


def aggregate(values: Sequence[tuple[float, int]], kind: str) -> float | None:
    """One number for a list of ``(value, samples)``: macro mean, or sample-weighted."""
    if not values:
        return None
    if kind == "weighted":
        weights = [(value, samples) for value, samples in values if samples > 0]
        total = sum(samples for _, samples in weights)
        return sum(value * samples for value, samples in weights) / total if total else None
    return sum(value for value, _ in values) / len(values)


def _slice_row(config: Config, run: ScannedRun) -> dict:
    family = family_of(config, run)
    return {
        "id": f"{run.model}|{run.benchmark}|{run.slice}",
        "campaign": run.campaign,
        "model": run.model,
        "benchmark": run.benchmark,
        "slice": run.slice,
        "family": family.name if family else "unclassified",
        "task": _task_key(run.task),
        "src": run.src,
        "tgt": run.tgt,
        "direction": direction_of(run.src, run.tgt),
        "lang": run.lang,
        "status": run.status,
        "samples": run.total_samples,
        "completed": run.completed_samples,
        "created": run.created,
        "finished": run.finished,
        "metrics": dict(sorted(run.metrics.items())),
        "pending": list(run.pending),
        "path": run.path,
    }


def _cell(
    model: str, family: Family, rows: Sequence[ScannedRun], metric: str, kind: str
) -> dict:
    values = [
        (row.metrics[metric], row.total_samples)
        for row in rows
        if isinstance(row.metrics.get(metric), (int, float))
    ]
    pending = sum(1 for row in rows if row.pending)
    total = len(rows)
    if not values:
        status = "pending" if pending and total else "empty"
    elif len(values) < total:
        status = "partial"
    else:
        status = "ok"
    return {
        "model": model,
        "family": family.name,
        "value": aggregate(values, kind) if values else None,
        "n": len(values),
        "total": total,
        "status": status,
    }


def build_snapshot(
    config: Config,
    runs: Sequence[ScannedRun],
    feed: Sequence[ScannedRun],
    version: str,
    now: str,
) -> dict:
    """The whole view payload: slices, server-side matrix, filters, KPIs and feed."""
    slices = [_slice_row(config, run) for run in runs]
    models = sorted({run.model for run in runs})
    providers = {run.model: (run.provider or run.model) for run in runs}
    slices_by_family: dict[str, list[ScannedRun]] = {}
    for run in runs:
        family = family_of(config, run)
        if family is not None:
            slices_by_family.setdefault(family.name, []).append(run)

    matrix: dict[str, dict] = {}
    for task in sorted({family.task for family in config.families}):
        rule_name = config.headline.get(task)
        rule = config.metrics.get(rule_name) if rule_name else None
        if not rule or not rule_name:
            continue
        cells = [
            _cell(model, family, [r for r in slices_by_family.get(family.name, []) if r.model == model], rule_name, config.aggregate)
            for family in config.families
            if family.task == task
            for model in models
        ]
        matrix[task] = {"metric": rule_name, "label": rule.label, "lower": rule.lower, "cells": cells}

    languages = sorted({lang for row in slices for lang in (row["lang"], row["src"], row["tgt"]) if lang})
    return {
        "version": version,
        "generated_at": now,
        "kpis": {
            "models": len(models),
            "families": len(config.families),
            "slices": len(slices),
            "pending": sum(1 for row in slices if row["pending"]),
            "failed": sum(1 for row in feed if row.status == "error"),
            "warnings": 0,
        },
        "filters": {
            "campaigns": sorted({row["campaign"] for row in slices}),
            "models": models,
            "families": [family.name for family in config.families],
            "languages": languages,
        },
        "model_families": {model: model_family(provider) for model, provider in providers.items()},
        "slices": slices,
        "matrix": matrix,
        "runs": [
            {
                "id": row.row_id or 0,
                "campaign": row.campaign,
                "model": row.model,
                "benchmark": row.benchmark,
                "slice": row.slice,
                "status": row.status,
                "created": row.created,
                "finished": row.finished,
                "path": row.path,
            }
            for row in feed
        ],
    }
