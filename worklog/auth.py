"""Single shared password for the worklog: no accounts, no roles.

The password comes from the environment. This module holds the same-origin
redirect rule, the derived session key and a small in-memory throttle; the
routes and the request guard are added in the next task.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections.abc import Callable

from .store import Store

DEFAULT_NEXT = "/"
SALT_META_KEY = "auth.salt"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def safe_next(value: str | None) -> str:
    """A same-origin path from a query parameter; anything else becomes ``/``."""
    if not value or not value.startswith("/"):
        return DEFAULT_NEXT
    if value.startswith(("//", "/\\")):
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
