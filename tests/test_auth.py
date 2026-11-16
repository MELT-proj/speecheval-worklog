from __future__ import annotations

from worklog.auth import Throttle, is_loopback, safe_next, session_secret
from worklog.store import Store


def test_safe_next_only_allows_same_origin_paths():
    assert safe_next("/inspect?run=3") == "/inspect?run=3"
    assert safe_next("/") == "/"
    assert safe_next(None) == "/"
    assert safe_next("") == "/"
    assert safe_next("inspect") == "/"
    assert safe_next("https://example.org/") == "/"
    assert safe_next("//example.org/") == "/"
    assert safe_next("/\\example.org") == "/"


def test_session_secret_is_stable_and_password_bound(tmp_path):
    store = Store(tmp_path / "w.sqlite")
    first = session_secret(store, "hunter2")
    assert first == session_secret(store, "hunter2")
    assert first != session_secret(store, "different")
    assert store.get_meta("auth.salt")
    other = Store(tmp_path / "other.sqlite")
    assert session_secret(other, "hunter2") != first


def test_throttle_blocks_after_the_configured_failures():
    now = [1000.0]
    throttle = Throttle(max_failures=3, window_seconds=60, clock=lambda: now[0])
    assert throttle.blocked_for("ip") == 0
    for _ in range(3):
        throttle.fail("ip")
    assert throttle.blocked_for("ip") > 0
    now[0] += 61
    assert throttle.blocked_for("ip") == 0
    throttle.fail("ip")
    throttle.clear("ip")
    assert throttle.blocked_for("ip") == 0


def test_only_loopback_hosts_are_safe_without_a_password():
    assert is_loopback("127.0.0.1")
    assert is_loopback("::1")
    assert is_loopback("localhost")
    assert not is_loopback("0.0.0.0")
    assert not is_loopback("10.0.0.5")
