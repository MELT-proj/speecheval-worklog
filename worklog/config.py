"""Configuration for the SpeechEval worklog.

One YAML file describes where logs are read from, how campaign folders are
recognised, how benchmark names map to matrix columns, and which metric is the
headline for each task. Nothing here knows about inspect_ai or melteval.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = REPO_ROOT / "worklog.yaml"
#: Scratch, never $HOME: the scanner rewrites the cache version row every interval.
DEFAULT_CACHE = Path("/mnt/scratch-artemis/giuseppe/worklog/worklog.sqlite")

_DIRECTIONS = frozenset({"xen", "enx"})
_AGGREGATES = frozenset({"macro", "weighted"})


@dataclass(frozen=True)
class Family:
    """One matrix column: a set of benchmark ids, optionally one direction of travel."""

    name: str
    task: str
    benchmarks: tuple[str, ...]
    direction: str | None = None
    test_benchmarks: tuple[str, ...] = ()


@dataclass(frozen=True)
class Metric:
    """Display name and preferred direction for one metric key."""

    label: str
    lower: bool = False


@dataclass(frozen=True)
class Lineage:
    """One training line in the trends page: several model prefixes, one label."""

    id: str
    name: str
    patterns: tuple[str, ...]

    def matches(self, model: str) -> bool:
        """Whether *model* belongs to this configured lineage."""
        return any(re.search(pattern, model) for pattern in self.patterns)


@dataclass(frozen=True)
class Config:
    """Everything the scanner, the views and the API read."""

    roots: tuple[Path, ...]
    include: tuple[str, ...] = ("*",)
    ignore: tuple[str, ...] = ()
    cache: Path = DEFAULT_CACHE
    port: int = 8765
    host: str = "127.0.0.1"
    interval_seconds: int = 30
    aggregate: str = "macro"
    families: tuple[Family, ...] = ()
    metrics: dict[str, Metric] = field(default_factory=dict)
    headline: dict[str, str] = field(default_factory=dict)
    lineages: tuple[Lineage, ...] = ()
    trend_metrics: dict[str, str] = field(default_factory=dict)
    test_benchmarks: frozenset[str] = frozenset()
    columnless_test_benchmarks: frozenset[str] = frozenset()

    def lineage_for(self, model: str) -> Lineage | None:
        """The first configured lineage whose patterns match *model*."""
        for lineage in self.lineages:
            if lineage.matches(model):
                return lineage
        return None

    def _task_key(self, task: str) -> str:
        """``chunked_asr`` and ``asr`` share a headline; the suite uses both prefixes."""
        return task.removeprefix("chunked_")

    def headline_metric(self, task: str) -> Metric | None:
        """The metric a matrix cell shows for *task*, or ``None`` when unconfigured."""
        name = self.headline.get(self._task_key(task))
        return self.metrics.get(name) if name else None

    def metric_names(self, task: str) -> tuple[str, ...]:
        """Every metric known for *task*; empty when the task has no headline."""
        return tuple(self.metrics) if self._task_key(task) in self.headline else ()


def _expand(value: str | Path) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(str(value))))


def _slug(name: str) -> str:
    """A stable id from a display label: ``SMURF · 16n`` -> ``smurf-16n``."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _ids(value: object) -> tuple[str, ...]:
    """A benchmark-id list, or the one id a YAML scalar spells."""
    if not value:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def load_config(path: Path | None = None, overrides: dict[str, object] | None = None) -> Config:
    """Read *path* (default: the repo's ``worklog.yaml``) and merge *overrides*.

    Environment variables in ``roots`` and ``cache`` are expanded, so a path can
    be written as ``${EVAL_ROOT}/speechval-canary``.

    Raises:
        ValueError: On an unknown ``aggregate``, a family direction other than
            ``xen``/``enx``, a headline naming an undefined metric, or a
            benchmark id listed as both a validation and a test benchmark.
    """
    if path is not None and not Path(path).exists():
        raise FileNotFoundError(f"config not found: {path}")
    raw: dict[str, object] = {}
    if DEFAULT_PATH.exists():
        raw = yaml.safe_load(DEFAULT_PATH.read_text(encoding="utf-8")) or {}
    if path is not None and Path(path).exists() and Path(path).resolve() != DEFAULT_PATH.resolve():
        raw.update(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})
    overrides_cache = (overrides or {}).get("cache")
    if overrides:
        raw.update({k: v for k, v in overrides.items() if v is not None})

    roots = tuple(_expand(p) for p in raw.get("roots") or ())
    include = tuple(raw.get("include") or ("*",))
    ignore = tuple(raw.get("ignore") or ())
    cache = _expand(overrides_cache or os.environ.get("WORKLOG_CACHE") or raw.get("cache") or DEFAULT_CACHE)
    aggregate = str(raw.get("aggregate") or "macro")
    if aggregate not in _AGGREGATES:
        raise ValueError(f"aggregate must be one of {sorted(_AGGREGATES)}, got {aggregate!r}")

    families: list[Family] = []
    for item in raw.get("families") or ():
        direction = item.get("direction")
        if direction is not None and direction not in _DIRECTIONS:
            raise ValueError(f"family {item.get('name')!r}: direction must be xen/enx, got {direction!r}")
        families.append(
            Family(
                name=item["name"],
                task=item["task"],
                benchmarks=tuple(item["benchmarks"]),
                direction=direction,
                test_benchmarks=_ids(item.get("test_benchmarks")),
            )
        )

    columnless = frozenset(_ids(raw.get("test_benchmarks")))
    test_benchmarks = frozenset(
        benchmark for family in families for benchmark in family.test_benchmarks
    ) | columnless
    validation_benchmarks = {benchmark for family in families for benchmark in family.benchmarks}
    both = validation_benchmarks & test_benchmarks
    if both:
        raise ValueError(f"benchmark {min(both)!r} is both a validation and a test benchmark")

    metrics = {
        name: Metric(label=str(spec.get("label", name)), lower=bool(spec.get("lower", False)))
        for name, spec in (raw.get("metrics") or {}).items()
    }
    headline = dict(raw.get("headline") or {})
    for task, metric in headline.items():
        if metric not in metrics:
            raise ValueError(f"headline {task!r} names undefined metric {metric!r}")

    lineages: list[Lineage] = []
    for item in (raw.get("trends") or {}).get("lineages") or ():
        name = str(item["name"])
        match = item.get("match") or ()
        if isinstance(match, str):
            match = (match,)
        patterns = tuple(str(pattern) for pattern in match)
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error as error:
                raise ValueError(f"lineage {name!r}: invalid match pattern {pattern!r}") from error
        lineages.append(Lineage(id=str(item.get("id") or _slug(name)), name=name, patterns=patterns))

    trend_metrics = {
        str(task): str(metric)
        for task, metric in ((raw.get("trends") or {}).get("default_metrics") or {}).items()
    }

    return Config(
        roots=roots,
        include=include,
        ignore=ignore,
        cache=cache,
        port=int(raw.get("port") or 8765),
        host=str(raw.get("host") or "127.0.0.1"),
        interval_seconds=int(raw.get("interval_seconds") or 30),
        aggregate=aggregate,
        families=tuple(families),
        metrics=metrics,
        headline=headline,
        lineages=tuple(lineages),
        trend_metrics=trend_metrics,
        test_benchmarks=test_benchmarks,
        columnless_test_benchmarks=columnless,
    )
