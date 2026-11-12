"""Incremental scan: parse only logs whose stat changed since the last pass."""

from __future__ import annotations

from dataclasses import dataclass

from .config import Config
from .scanner import ScannedRun, UnreadableLog, iter_log_paths, scan_run
from .store import Store


@dataclass(frozen=True)
class SyncResult:
    """What one pass did: how many logs it parsed, and how many were unreadable."""

    parsed: int
    warnings: int


def sync_store(config: Config, store: Store) -> SyncResult:
    """Bring *store* in line with the logs on disk; return how many were parsed.

    Unchanged files are recognised by ``(path, mtime_ns, size)`` and reused from
    the cache, so a periodic rescan costs a stat per log, not a JSON parse.
    Deleted logs disappear from the store because the incoming set is built only
    from paths that still exist.
    """
    known = store.stats()
    incoming: list[ScannedRun] = []
    seen: set[str] = set()
    parsed = 0
    warnings = 0
    for path in iter_log_paths(config):
        resolved = str(path.resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            stat = path.stat()
        except OSError:
            if "logs" in path.parts:
                warnings += 1
            continue
        if known.get(resolved) == (stat.st_mtime_ns, stat.st_size):
            continue
        parsed += 1
        try:
            row = scan_run(path)
        except UnreadableLog:
            if "logs" in path.parts:
                warnings += 1
            continue
        if row is not None:
            incoming.append(row)
    incoming.extend(row for row in store.all() if row.path in seen and row.path not in {r.path for r in incoming})
    store.sync(incoming)
    return SyncResult(parsed=parsed, warnings=warnings)
