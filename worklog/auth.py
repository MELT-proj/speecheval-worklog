"""Single shared password for the worklog: no accounts, no roles.

The password comes from the environment. This module holds the same-origin
redirect rule, the derived session key, a small in-memory throttle, the
``/login`` and ``/logout`` routes and the request guard.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections.abc import Callable
from datetime import timedelta
from urllib.parse import urlencode

from flask import Flask, jsonify, redirect, render_template_string, request, send_from_directory, session

from .store import Store

DEFAULT_NEXT = "/"
SALT_META_KEY = "auth.salt"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
SESSION_DAYS = 7

_THROTTLED = """<!doctype html>
<meta charset="utf-8">
<title>Too many attempts</title>
<body style="font-family: system-ui, sans-serif; max-width: 26rem; margin: 4rem auto;">
<p>Too many failed sign-in attempts. Try again in {{ wait }} seconds.</p>
<p><a href="{{ back }}">Back to sign in</a></p>
</body>
"""


def safe_next(value: str | None) -> str:
    """A same-origin path from a query parameter; anything else becomes ``/``."""
    if not value or not value.startswith("/"):
        return DEFAULT_NEXT
    if value.startswith(("//", "/\\")) or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        return DEFAULT_NEXT
    return value


def session_secret(store: Store, password: str) -> str:
    """Derive a stable signing key from *password* and a per-cache random salt."""
    salt = store.get_meta(SALT_META_KEY)
    if not salt:
        salt = secrets.token_hex(32)
        store.set_meta(SALT_META_KEY, salt)
    return hmac.new(salt.encode(), password.encode(), hashlib.sha256).hexdigest()


def is_loopback(host: str) -> bool:
    """Whether *host* is a loopback name or address, safe to serve without a password."""
    return host in LOOPBACK_HOSTS


class Throttle:
    """In-memory failed-login counter per client address."""

    def __init__(
        self,
        max_failures: int = 5,
        window_seconds: float = 600,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self.clock = clock
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> list[float]:
        recent = [stamp for stamp in self._failures.get(key, ()) if now - stamp < self.window_seconds]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def blocked_for(self, key: str) -> int:
        """Seconds until *key* may try again, or 0 when it is allowed now."""
        with self._lock:
            now = self.clock()
            recent = self._recent(key, now)
            if len(recent) < self.max_failures:
                return 0
            return max(1, int(self.window_seconds - (now - recent[0])) + 1)

    def fail(self, key: str) -> None:
        with self._lock:
            now = self.clock()
            recent = self._recent(key, now)
            recent.append(now)
            self._failures[key] = recent

    def clear(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


def install_auth(app: Flask, store: Store, password: str | None) -> None:
    """Add the login routes, and guard every other route when *password* is set.

    With no password the app stays open (a loopback development run); ``/login``
    and ``/logout`` still exist so the header's sign-out control never 404s.
    """
    enabled = bool(password)
    throttle = Throttle()

    if enabled:
        app.secret_key = session_secret(store, password)
        app.config.update(
            SESSION_COOKIE_NAME="worklog_session",
            SESSION_COOKIE_HTTPONLY=True,
            SESSION_COOKIE_SAMESITE="Lax",
            PERMANENT_SESSION_LIFETIME=timedelta(days=SESSION_DAYS),
        )

    def client_key() -> str:
        return request.remote_addr or "unknown"

    def login_url(**params: str) -> str:
        return "/login?" + urlencode(params)

    @app.get("/login", strict_slashes=False)
    def login_page():
        if not enabled or session.get("auth") is True:
            return redirect(DEFAULT_NEXT)
        return send_from_directory(app.static_folder, "login.html")

    @app.post("/login")
    def login_submit():
        if not enabled:
            return redirect(DEFAULT_NEXT)
        next_path = safe_next(request.form.get("next"))
        key = client_key()
        wait = throttle.blocked_for(key)
        if wait:
            back = login_url(next=next_path)
            return (
                render_template_string(_THROTTLED, wait=wait, back=back),
                429,
                {"Retry-After": str(wait)},
            )
        supplied = request.form.get("password", "")
        if not hmac.compare_digest(supplied.encode(), password.encode()):
            throttle.fail(key)
            return redirect(login_url(error="1", next=next_path), code=303)
        throttle.clear(key)
        session.clear()
        session["auth"] = True
        session.permanent = True
        return redirect(next_path, code=303)

    @app.post("/logout")
    def logout():
        if enabled:
            session.clear()
        return redirect("/login" if enabled else DEFAULT_NEXT, code=303)

    if not enabled:
        return

    @app.before_request
    def require_login():
        path = request.path
        if path in ("/login", "/login/", "/logout") or path.startswith("/static/"):
            return None
        if session.get("auth") is True:
            return None
        if path.startswith("/api/"):
            return jsonify({"error": "authentication required"}), 401
        target = path
        if request.query_string:
            target = f"{target}?{request.query_string.decode('utf-8', 'replace')}"
        return redirect(login_url(next=target))
