"""Trend lines: fold cached runs into training lineages and their checkpoints."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from .config import Config, Family
from .scanner import ScannedRun, direction_of, model_family
from .views import aggregate, checkpoint_of, family_of

_GROUP_RANK = {"baseline": 0, "melt": 1, "smurf": 2, "other": 3}
_GROUP_LABELS = {"baseline": "Baselines", "melt": "MELT", "smurf": "SMURF", "other": "Other"}
_TASK_LABELS = {
    "asr": "Speech recognition",
    "st": "Speech translation",
    "audio_mcq": "Multiple-choice QA",
}
_BARE_STEP_RE = re.compile(r"^step-(?P<step>\d+)$")


@dataclass(frozen=True)
class LineageRef:
    """A trend line's stable id, display name and matrix group."""

    id: str
    name: str
    group: str


@dataclass(frozen=True)
class TrendRecord:
    """One de-duplicated winning run, placed on a lineage at a numeric step."""

    run: ScannedRun
    lineage: LineageRef
    family: Family
    step: int


def step_of(model: str) -> tuple[int, str] | tuple[None, str]:
    """The numeric step and base name; bare ``step-108000`` belongs to ``step``."""
    bare = _BARE_STEP_RE.match(model)
    if bare is not None:
        return int(bare.group("step")), "step"
    base, step = checkpoint_of(model)
    if step is None:
        return None, model
    return step, base


def _lineage_of(config: Config, model: str, group: str, base: str) -> LineageRef:
    configured = config.lineage_for(model)
    if configured is not None:
        return LineageRef(configured.id, configured.name, group)
    label = _GROUP_LABELS.get(group, group.title())
    return LineageRef(f"{group}/{base}", f"{label} · {base}", group)


def fold_runs(config: Config, runs: Sequence[ScannedRun]) -> list[TrendRecord]:
    """Winning runs that belong to a configured family and a numeric checkpoint step."""
    winners: dict[tuple[str, str, str, int], TrendRecord] = {}
    for run in runs:
        if run.status != "success":
            continue
        family = family_of(config, run)
        step, base = step_of(run.model)
        if family is None or step is None:
            continue
        group = model_family(run.provider or run.model)
        record = TrendRecord(run, _lineage_of(config, run.model, group, base), family, step)
        key = (record.lineage.id, run.benchmark, run.slice, step)
        current = winners.get(key)
        if current is None or ((run.created or ""), run.mtime_ns) > (
            (current.run.created or ""),
            current.run.mtime_ns,
        ):
            winners[key] = record
    return sorted(winners.values(), key=lambda item: (item.lineage.id, item.step, item.run.benchmark, item.run.slice))


def build_trends(config: Config, runs: Sequence[ScannedRun]) -> dict:
    """The whole trends payload: lineages, task metrics, panels and checkpoints."""
    records = fold_runs(config, runs)
    lineages: dict[str, dict] = {}
    for record in records:
        entry = lineages.setdefault(
            record.lineage.id,
            {
                "id": record.lineage.id,
                "name": record.lineage.name,
                "group": record.lineage.group,
                "steps": set(),
            },
        )
        entry["steps"].add(record.step)

    ordered = sorted(lineages.values(), key=lambda item: (_GROUP_RANK.get(item["group"], 3), item["name"]))
    lineage_payload = [
        {
            "id": entry["id"],
            "name": entry["name"],
            "group": entry["group"],
            "steps": sorted(entry["steps"]),
            "max_step": max(entry["steps"]),
        }
        for entry in ordered
    ]

    tasks = {}
    for task, metric_name in config.headline.items():
        rule = config.metrics.get(metric_name)
        if rule is None:
            continue
        tasks[task] = {
            "label": _TASK_LABELS.get(task, task),
            "metric": metric_name,
            "metrics": [
                {"name": name, "label": metric.label, "lower": metric.lower}
                for name, metric in config.metrics.items()
            ],
        }

    by_family: dict[str, list[TrendRecord]] = {}
    for record in records:
        by_family.setdefault(record.family.name, []).append(record)

    panels = []
    for family in config.families:
        family_records = by_family.get(family.name)
        if not family_records:
            continue
        task = family.task.removeprefix("chunked_")
        if task not in tasks:
            continue
        cells: dict[tuple[str, int], list[TrendRecord]] = {}
        for record in family_records:
            cells.setdefault((record.lineage.id, record.step), []).append(record)
        points = []
        for (lineage_id, step), cell in sorted(cells.items()):
            values = {}
            coverage = {}
            for name in config.metrics:
                pairs = [
                    (record.run.metrics[name], record.run.total_samples)
                    for record in cell
                    if isinstance(record.run.metrics.get(name), (int, float))
                ]
                if pairs:
                    values[name] = aggregate(pairs, config.aggregate)
                pending = any(record.run.pending for record in cell)
                if not pairs:
                    status = "pending" if pending else "empty"
                elif len(pairs) < len(cell):
                    status = "partial"
                else:
                    status = "ok"
                coverage[name] = {"n": len(pairs), "total": len(cell), "status": status}
            points.append(
                {"lineage": lineage_id, "step": step, "values": values, "coverage": coverage}
            )
        languages = sorted(
            {language for record in family_records if (language := language_column(record.run))}
        )
        panels.append(
            {"family": family.name, "task": task, "languages": languages, "points": points}
        )

    checkpoints: list[dict] = []
    by_checkpoint: dict[tuple[str, int], list[TrendRecord]] = {}
    for record in records:
        by_checkpoint.setdefault((record.lineage.id, record.step), []).append(record)
    for (lineage_id, step), checkpoint_records in sorted(by_checkpoint.items()):
        model = max(
            checkpoint_records,
            key=lambda item: ((item.run.created or ""), item.run.mtime_ns),
        ).run.model
        values = {}
        coverage = {}
        for family in config.families:
            family_records = [
                record for record in checkpoint_records if record.family.name == family.name
            ]
            if not family_records:
                continue
            task = family.task.removeprefix("chunked_")
            metric = config.headline.get(task)
            if metric is None:
                continue
            pairs = [
                (record.run.metrics[metric], record.run.total_samples)
                for record in family_records
                if isinstance(record.run.metrics.get(metric), (int, float))
            ]
            if not pairs:
                continue
            values[family.name] = aggregate(pairs, config.aggregate)
            coverage[family.name] = len(pairs)
        if values:
            checkpoints.append(
                {
                    "lineage": lineage_id,
                    "model": model,
                    "step": step,
                    "values": values,
                    "coverage": coverage,
                }
            )

    return {
        "aggregate": config.aggregate,
        "lineages": lineage_payload,
        "tasks": tasks,
        "panels": panels,
        "checkpoints": checkpoints,
    }


def language_column(run: ScannedRun) -> str | None:
    """The slice's language column, mirroring ``util.js``'s ``languageOf``."""
    if run.lang:
        return run.lang
    direction = direction_of(run.src, run.tgt)
    if direction == "enx":
        return run.tgt or run.src
    if direction == "xen":
        return run.src or run.tgt
    return run.tgt or run.src
