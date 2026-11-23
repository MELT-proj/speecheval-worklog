"""Flask app: read-only JSON over the SQLite cache, plus the static page."""

from __future__ import annotations

import argparse
import os
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from .auth import install_auth, is_loopback
from .config import Config, load_config
from .samples import Digest, DigestError, load_digest, select
from .scanner import ScannedRun
from .store import Store, open_store
from .sync import sync_store
from .trends import build_languages, build_trends
from .views import build_snapshot

#: Sort keys the samples endpoint accepts; anything else falls back to ``order``.
_SORTS = ("order", "worst", "slowest", "longest")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _as_int(name: str, default: int) -> int:
    """Query arg as an int; an unparsable value quietly means *default*."""
    try:
        return int(request.args.get(name, default))
    except ValueError:
        return default


def _as_float(name: str, default: float) -> float:
    """Query arg as a float; an unparsable value quietly means *default*."""
    try:
        return float(request.args.get(name, default))
    except ValueError:
        return default


def _run_dict(run: ScannedRun) -> dict:
    return {
        "id": run.row_id or 0,
        "path": run.path,
        "campaign": run.campaign,
        "model": run.model,
        "benchmark": run.benchmark,
        "slice": run.slice,
        "task": run.task,
        "src": run.src,
        "tgt": run.tgt,
        "status": run.status,
        "samples": run.total_samples,
        "completed": run.completed_samples,
        "created": run.created,
        "finished": run.finished,
        "metrics": dict(sorted(run.metrics.items())),
        "pending": list(run.pending),
    }


def create_app(
    config: Config,
    store: Store,
    scan: Callable[[], None] | None = None,
    password: str | None = None,
) -> Flask:
    """Build the app; *scan* is the refresh hook and *password* enables the shield."""
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    samples_dir = config.cache.parent / "samples"

    def load(row: ScannedRun) -> Digest:
        """The run's digest from the sidecar cache; raises DigestError typed as HTTP."""
        return load_digest(row.path, row.mtime_ns, row.size, samples_dir, config.roots)

    def snapshot() -> dict:
        return build_snapshot(
            config, store.winning(), store.history(limit=50), store.version(), _now(),
            failed=store.count(status="error"),
            warnings=int(store.get_meta("warnings") or 0),
        )

    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/inspect")
    def inspect_page():
        return send_from_directory(app.static_folder, "inspect.html")

    @app.get("/api/snapshot")
    def api_snapshot():
        version = request.args.get("version")
        if version and version == store.version():
            return "", 204
        return jsonify(snapshot())

    @app.get("/api/trends")
    def api_trends():
        version = request.args.get("version")
        if version and version == store.version():
            return "", 204
        payload = build_trends(config, store.winning())
        payload.update(version=store.version(), generated_at=_now())
        return jsonify(payload)

    @app.get("/api/trends/languages")
    def api_trends_languages():
        version = request.args.get("version")
        if version and version == store.version():
            return "", 204
        payload = build_languages(
            config,
            store.winning(),
            family=request.args.get("family", ""),
            lineage=request.args.get("lineage", ""),
            metric=request.args.get("metric", ""),
            threshold=min(max(_as_float("threshold", 0.1), 0.0), 1.0),
            n=max(_as_int("n", 5), 1),
        )
        if payload is None:
            return jsonify({"error": "unknown family, lineage or metric"}), 404
        payload.update(version=store.version(), generated_at=_now())
        return jsonify(payload)

    @app.get("/api/runs")
    def api_runs():
        rows = store.history(
            campaign=request.args.get("campaign"),
            model=request.args.get("model"),
            benchmark=request.args.get("benchmark"),
            status=request.args.get("status"),
            offset=_as_int("offset", 0),
            limit=_as_int("limit", 50),
        )
        return jsonify({"runs": [_run_dict(row) for row in rows]})

    @app.get("/api/run/<int:run_id>")
    def api_run(run_id: int):
        row = store.get(run_id)
        if row is None:
            return jsonify({"error": f"no run {run_id}"}), 404
        return jsonify(_run_dict(row))

    @app.get("/api/run/<int:run_id>/overview")
    def api_run_overview(run_id: int):
        row = store.get(run_id)
        if row is None:
            return jsonify({"error": f"no run {run_id}"}), 404
        try:
            digest = load(row)
        except DigestError as err:
            return jsonify({"error": err.message, "path": row.path}), err.status
        setup = digest.overview
        return jsonify({
            "run": _run_dict(row),
            "setup": setup,
            "scores": setup["scores"],
            "stats": setup["stats"],
            "digest": {
                "samples": len(digest.samples),
                "failed": digest.failed,
                "flagged": digest.flagged,
                "sorts": digest.sorts,
            },
        })

    @app.get("/api/run/<int:run_id>/samples")
    def api_run_samples(run_id: int):
        row = store.get(run_id)
        if row is None:
            return jsonify({"error": f"no run {run_id}"}), 404
        try:
            digest = load(row)
        except DigestError as err:
            return jsonify({"error": err.message, "path": row.path}), err.status
        sort = request.args.get("sort", "order")
        rows = select(
            digest.samples,
            q=request.args.get("q", ""),
            sort=sort if sort in _SORTS else "order",
            issues_only=request.args.get("filter") == "issues",
        )
        offset = max(_as_int("offset", 0), 0)
        limit = min(max(_as_int("limit", 25), 1), 100)
        return jsonify({
            "total": len(rows),
            "offset": offset,
            "limit": limit,
            "samples": rows[offset:offset + limit],
        })

    @app.post("/api/refresh")
    def api_refresh():
        if scan is not None:
            scan()
        return jsonify({"version": store.version(), "refreshed_at": _now()})

    @app.get("/api/health")
    def api_health():
        return jsonify({"ok": True, "version": store.version()})

    install_auth(app, store, password)
    return app


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only worklog for SpeechEval inspect logs.")
    parser.add_argument("--config", type=Path, help="YAML config (default: worklog.yaml)")
    parser.add_argument("--root", action="append", help="Replace the configured roots (repeatable)")
    parser.add_argument("--cache", type=Path, help="SQLite cache path")
    parser.add_argument("--port", type=int)
    parser.add_argument("--host")
    parser.add_argument("--interval", type=int)
    return parser


def main(argv: list[str] | None = None) -> None:
    """Scan once, keep scanning in a daemon thread, serve."""
    args = _parser().parse_args(argv)
    config = load_config(
        args.config,
        overrides={
            "roots": args.root,
            "cache": args.cache,
            "port": args.port,
            "host": args.host,
            "interval_seconds": args.interval,
        },
    )
    store = open_store(config.cache, fallback_dir=Path.cwd() / ".cache")
    password = os.environ.get("WORKLOG_PASSWORD") or None
    if password is None and not is_loopback(config.host):
        raise SystemExit(
            f"refusing to listen on {config.host} without WORKLOG_PASSWORD; "
            "set it, or use --host 127.0.0.1 for an open local dashboard"
        )
    lock = threading.Lock()

    def scan_once() -> None:
        with lock:
            result = sync_store(config, store)
            store.set_meta("warnings", str(result.warnings))

    app = create_app(config, store, scan=scan_once, password=password)

    def loop() -> None:
        while True:
            try:
                scan_once()
            except Exception:
                app.logger.exception("scan failed")
            time.sleep(config.interval_seconds)

    threading.Thread(target=loop, daemon=True, name="worklog-scanner").start()
    app.run(host=config.host, port=config.port, threaded=True)
