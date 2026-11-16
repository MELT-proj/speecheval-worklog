from __future__ import annotations

import dataclasses
from urllib.parse import parse_qs, urlsplit

import pytest

from worklog.app import create_app, main
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
    assert safe_next("/a\r\nb") == "/"


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


def locked_app(cfg, store, tmp_path, password="s3cret"):
    return create_app(dataclasses.replace(cfg, cache=tmp_path / "w.sqlite"), store, password=password)


def test_pages_redirect_and_apis_answer_401(cfg, store, tmp_path):
    client = locked_app(cfg, store, tmp_path).test_client()
    page = client.get("/")
    assert page.status_code == 302
    assert urlsplit(page.headers["Location"]).path == "/login"
    assert parse_qs(urlsplit(page.headers["Location"]).query)["next"] == ["/"]
    assert client.get("/inspect").status_code == 302
    for path in ("/api/snapshot", "/api/runs", "/api/health"):
        response = client.get(path)
        assert response.status_code == 401
        assert response.get_json() == {"error": "authentication required"}
    assert client.get("/login").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_login_logout_and_the_session_cookie(cfg, store, tmp_path):
    client = locked_app(cfg, store, tmp_path).test_client()
    bad = client.post("/login", data={"password": "wrong", "next": "/inspect?run=3"})
    assert bad.status_code == 303
    assert parse_qs(urlsplit(bad.headers["Location"]).query)["error"] == ["1"]
    good = client.post("/login", data={"password": "s3cret", "next": "/inspect?run=3"})
    assert good.status_code == 303
    assert good.headers["Location"] == "/inspect?run=3"
    assert client.get_cookie("worklog_session") is not None
    assert client.get("/api/snapshot").status_code == 200
    out = client.post("/logout")
    assert out.status_code == 303 and out.headers["Location"] == "/login"
    assert client.get("/api/snapshot").status_code == 401


def test_login_rejects_an_off_site_next(cfg, store, tmp_path):
    client = locked_app(cfg, store, tmp_path).test_client()
    for target in ("//evil.example/x", "https://evil.example/x", "/\\evil.example"):
        response = client.post("/login", data={"password": "s3cret", "next": target})
        assert response.headers["Location"] == "/"


def test_session_survives_a_new_app_on_the_same_store(cfg, store, tmp_path):
    first = locked_app(cfg, store, tmp_path).test_client()
    first.post("/login", data={"password": "s3cret"})
    cookie = first.get_cookie("worklog_session").value
    second = locked_app(cfg, store, tmp_path).test_client()
    second.set_cookie("worklog_session", cookie)
    assert second.get("/api/snapshot").status_code == 200


def test_changing_the_password_invalidates_existing_cookies(cfg, store, tmp_path):
    first = locked_app(cfg, store, tmp_path).test_client()
    first.post("/login", data={"password": "s3cret"})
    cookie = first.get_cookie("worklog_session").value
    second = locked_app(cfg, store, tmp_path, password="rotated").test_client()
    second.set_cookie("worklog_session", cookie)
    assert second.get("/api/snapshot").status_code == 401


def test_login_is_throttled_after_five_failures(cfg, store, tmp_path):
    client = locked_app(cfg, store, tmp_path).test_client()
    for _ in range(5):
        client.post("/login", data={"password": "wrong"})
    blocked = client.post("/login", data={"password": "s3cret"})
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0


def test_without_a_password_the_app_stays_open(client):
    assert client.get("/api/snapshot").status_code == 200
    assert client.get("/login").status_code == 302
    assert client.post("/logout").status_code == 303


def test_non_loopback_without_a_password_refuses_to_start(monkeypatch, tmp_path):
    monkeypatch.delenv("WORKLOG_PASSWORD", raising=False)
    with pytest.raises(SystemExit) as err:
        main(["--host", "0.0.0.0", "--cache", str(tmp_path / "w.sqlite")])
    assert "WORKLOG_PASSWORD" in str(err.value)


def test_login_page_has_the_form(cfg, store, tmp_path):
    page = locked_app(cfg, store, tmp_path).test_client().get("/login")
    assert page.status_code == 200
    assert b'name="password"' in page.data
    assert b'id="login-next"' in page.data
