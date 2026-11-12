"""SQLite cache of scanned logs.

Keyed by path plus ``(mtime_ns, size)``: a rescan rewrites only what changed and
deletes rows whose log disappeared. The duplicate policy mirrors
``scripts/evaluate.py``: the newest successful log wins a slice, and errors stay
in history so the feed can show them.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterable, Sequence
from pathlib import Path

from .scanner import ScannedRun

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    mtime_ns INTEGER NOT NULL,
    size INTEGER NOT NULL,
    campaign TEXT, model TEXT, benchmark TEXT, slice TEXT, task TEXT,
    src TEXT, tgt TEXT, status TEXT,
    total_samples INTEGER, completed_samples INTEGER,
    created TEXT, finished TEXT, log_id TEXT, provider TEXT, lang TEXT
);
CREATE TABLE IF NOT EXISTS metrics (
    run_id INTEGER NOT NULL,
    metric TEXT NOT NULL,
    value REAL,
    is_pending INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, metric, is_pending)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

_RUN_COLUMNS = (
    "id, path, mtime_ns, size, campaign, model, benchmark, slice, task, src, tgt, status,"
    " total_samples, completed_samples, created, finished, log_id, provider, lang"
)


def open_store(path: Path, *, fallback_dir: Path) -> Store:
    """Open the cache, falling back to ``<fallback_dir>/worklog.sqlite`` when *path* is unusable."""
    try:
        return Store(path)
    except OSError:
        fallback = Path(fallback_dir) / "worklog.sqlite"
        fallback.parent.mkdir(parents=True, exist_ok=True)
        return Store(fallback)


class Store:
    """A single-file cache; every method opens its own connection, so it is safe to call from a thread."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(runs)")}
            if not {"provider", "lang"} <= columns:
                # Old cache from before the provider column: it is disposable.
                conn.executescript("DROP TABLE runs; DROP TABLE metrics;")
                conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def sync(self, runs: Iterable[ScannedRun]) -> str:
        """Upsert *runs*, drop vanished paths, and return the new content version."""
        incoming = list(runs)
        with self._connect() as conn:
            existing = {
                row["path"]: (row["id"], row["mtime_ns"], row["size"])
                for row in conn.execute("SELECT id, path, mtime_ns, size FROM runs")
            }
            keep: set[str] = set()
            for item in incoming:
                keep.add(item.path)
                old = existing.get(item.path)
                if old and old[1] == item.mtime_ns and old[2] == item.size:
                    continue
                conn.execute(
                    "INSERT INTO runs (path, mtime_ns, size, campaign, model, benchmark, slice, task, src, tgt,"
                    " status, total_samples, completed_samples, created, finished, log_id, provider, lang)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(path) DO UPDATE SET mtime_ns=excluded.mtime_ns, size=excluded.size,"
                    " campaign=excluded.campaign, model=excluded.model, benchmark=excluded.benchmark,"
                    " slice=excluded.slice, task=excluded.task, src=excluded.src, tgt=excluded.tgt,"
                    " status=excluded.status, total_samples=excluded.total_samples,"
                    " completed_samples=excluded.completed_samples, created=excluded.created,"
                    " finished=excluded.finished, log_id=excluded.log_id, provider=excluded.provider, lang=excluded.lang",
                    (
                        item.path, item.mtime_ns, item.size, item.campaign, item.model, item.benchmark,
                        item.slice, item.task, item.src, item.tgt, item.status, item.total_samples,
                        item.completed_samples, item.created, item.finished, item.log_id, item.provider, item.lang,
                    ),
                )
                run_id = conn.execute("SELECT id FROM runs WHERE path=?", (item.path,)).fetchone()["id"]
                conn.execute("DELETE FROM metrics WHERE run_id=?", (run_id,))
                conn.executemany(
                    "INSERT INTO metrics (run_id, metric, value, is_pending) VALUES (?,?,?,0)",
                    [(run_id, name, value) for name, value in item.metrics.items()],
                )
                conn.executemany(
                    "INSERT INTO metrics (run_id, metric, value, is_pending) VALUES (?,?,NULL,1)",
                    [(run_id, name) for name in item.pending],
                )
            gone = [path for path in existing if path not in keep]
            for path in gone:
                conn.execute("DELETE FROM metrics WHERE run_id=?", (existing[path][0],))
                conn.execute("DELETE FROM runs WHERE path=?", (path,))

            version = self._version_of(incoming)
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('version', ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (version,),
            )
            conn.commit()
        return version

    @staticmethod
    def _version_of(runs: Sequence[ScannedRun]) -> str:
        payload = "|".join(sorted(f"{r.path}:{r.mtime_ns}:{r.size}" for r in runs))
        return hashlib.sha1(payload.encode()).hexdigest()[:12] if payload else "empty"

    def version(self) -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key='version'").fetchone()
        return row["value"] if row else "empty"

    def set_meta(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            conn.commit()

    def get_meta(self, key: str, default: str = "") -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def count(self, *, status: str | None = None) -> int:
        """Number of cached runs, optionally only one status (the KPI uses ``error``)."""
        query = "SELECT COUNT(*) AS n FROM runs" + (" WHERE status=?" if status else "")
        with self._connect() as conn:
            row = conn.execute(query, (status,) if status else ()).fetchone()
        return int(row["n"])

    def stats(self) -> dict[str, tuple[int, int]]:
        """``path -> (mtime_ns, size)`` for every cached log, for the incremental scan."""
        with self._connect() as conn:
            return {
                row["path"]: (row["mtime_ns"], row["size"])
                for row in conn.execute("SELECT path, mtime_ns, size FROM runs")
            }

    def _load(self, where: str = "", params: Sequence = (), order: str = "created DESC, path") -> list[ScannedRun]:
        with self._connect() as conn:
            rows = conn.execute(f"SELECT {_RUN_COLUMNS} FROM runs {where} ORDER BY {order}", params).fetchall()
            metrics = self._metrics(conn, [row["id"] for row in rows])
        return [self._to_run(row, metrics.get(row["id"], ({}, ()))) for row in rows]

    @staticmethod
    def _metrics(conn: sqlite3.Connection, ids: Sequence[int]) -> dict[int, tuple[dict[str, float], tuple[str, ...]]]:
        if not ids:
            return {}
        marks = ",".join("?" for _ in ids)
        raw: dict[int, tuple[dict[str, float], list[str]]] = {i: ({}, []) for i in ids}
        for row in conn.execute(
            f"SELECT run_id, metric, value, is_pending FROM metrics WHERE run_id IN ({marks})", tuple(ids)
        ):
            values, pending = raw[row["run_id"]]
            if row["is_pending"]:
                pending.append(row["metric"])
            else:
                values[row["metric"]] = row["value"]
        return {i: (values, tuple(sorted(pending))) for i, (values, pending) in raw.items()}

    @staticmethod
    def _to_run(row: sqlite3.Row, loaded: tuple[dict[str, float], tuple[str, ...]]) -> ScannedRun:
        values, pending = loaded
        return ScannedRun(
            path=row["path"],
            mtime_ns=row["mtime_ns"],
            size=row["size"],
            campaign=row["campaign"] or "",
            model=row["model"] or "unknown",
            benchmark=row["benchmark"] or "",
            slice=row["slice"] or "",
            task=row["task"] or "",
            src=row["src"],
            tgt=row["tgt"],
            status=row["status"] or "unknown",
            total_samples=row["total_samples"] or 0,
            completed_samples=row["completed_samples"] or 0,
            created=row["created"],
            finished=row["finished"],
            log_id=row["log_id"],
            provider=row["provider"] or "",
            lang=row["lang"],
            metrics=values,
            pending=pending,
            row_id=row["id"],
        )

    def all(self) -> list[ScannedRun]:
        return self._load(order="path")

    def winning(self) -> list[ScannedRun]:
        """Newest successful log per (model, benchmark, slice), mirroring evaluate.py."""
        winners: dict[tuple[str, str, str], ScannedRun] = {}
        for item in self._load(where="WHERE status='success'"):
            key = (item.model, item.benchmark, item.slice)
            current = winners.get(key)
            if current is None or ((item.created or ""), item.mtime_ns) > (
                (current.created or ""),
                current.mtime_ns,
            ):
                winners[key] = item
        return sorted(winners.values(), key=lambda r: (r.model, r.benchmark, r.slice))

    def history(
        self,
        *,
        campaign: str | None = None,
        model: str | None = None,
        benchmark: str | None = None,
        status: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[ScannedRun]:
        clauses, params = [], []
        for column, value in (
            ("campaign", campaign),
            ("model", model),
            ("benchmark", benchmark),
            ("status", status),
        ):
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._load(where=where, params=params)
        return rows[offset : offset + limit]

    def get(self, run_id: int) -> ScannedRun | None:
        rows = self._load(where="WHERE id=?", params=(run_id,))
        return rows[0] if rows else None
