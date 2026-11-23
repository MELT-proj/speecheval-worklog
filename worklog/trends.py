"""Trend lines: fold cached runs into training lineages and their checkpoints."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from .config import Config, Family
from .scanner import ScannedRun, model_family
from .views import checkpoint_of, family_of

_GROUP_RANK = {"baseline": 0, "melt": 1, "smurf": 2, "other": 3}
_GROUP_LABELS = {"baseline": "Baselines", "melt": "MELT", "smurf": "SMURF", "other": "Other"}
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
    """The trends payload: lineages now; tasks, panels and checkpoints in Task 3."""
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
    payload = [
        {
            "id": entry["id"],
            "name": entry["name"],
            "group": entry["group"],
            "steps": sorted(entry["steps"]),
            "max_step": max(entry["steps"]),
        }
        for entry in ordered
    ]
    return {"aggregate": config.aggregate, "lineages": payload, "tasks": {}, "panels": [], "checkpoints": []}
