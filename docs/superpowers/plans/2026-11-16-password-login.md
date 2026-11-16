# Password Login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put one shared password in front of every worklog page and JSON endpoint, with a login form, a signed session cookie, a sign-out button, and a CLI refusal to serve beyond loopback without a password.

**Architecture:** A new `worklog/auth.py` owns password comparison, same-origin `next` sanitization, the session signing key, the failed-login throttle, the `/login` and `/logout` routes, and the `before_request` guard. `create_app` installs it when given a password; `main()` reads `WORKLOG_PASSWORD` and refuses a non-loopback bind without it. The login page is plain static HTML/JS like the rest of the app, and the two existing fetch modules learn to send an expired session to `/login`.

**Tech Stack:** Python ≥ 3.10, Flask 3 (signed-cookie sessions via its existing `itsdangerous`), PyYAML, vanilla JS with no build step, pytest, Playwright for the browser test.

**Spec:** `docs/superpowers/specs/2026-11-16-password-login-design.md`

## Global Constraints

- Python ≥ 3.10; `from __future__ import annotations`; no imports from `typing`
  (`X | None`, `list[str]`, `dict[str, int]`); ruff line length 119.
- No new runtime dependency. The password check uses `hmac`, `hashlib`,
  `secrets` and Flask's own session.
- Read-only app: the only writes remain the SQLite cache and digest sidecars.
  Auth adds exactly one one-time `auth.salt` row in the existing `meta` table.
- The password must never appear in `worklog.yaml`, a command-line argument, the
  SQLite cache, a log line or a response body.
- API requests get `401` JSON when unauthenticated, never a redirect. Page
  requests redirect to `/login?next=…`.
- `next` accepts only a same-origin path: it must start with one `/` and must not
  start with `//` or `/\`; anything else becomes `/`.
- Static assets and `/login`/`/logout` are reachable without a session, so the
  login page can load the stylesheet and no redirect loop exists.
- Auth is opt-in through `create_app(..., password=None)`, so every existing
  test and a loopback `python app.py` keep working unchanged.
- Unit/API run: `pytest tests -q`. Browser tests additionally need
  `LD_LIBRARY_PATH=$(cat /tmp/opencode/pwlibs.path)`.
- All paths are relative to `/home/giuseppe/melt-proj/speecheval-worklog`.

## Review Focus

- **API fetch parsing HTML.** If an unauthenticated `/api/snapshot` is answered
  with a 302 to the login page, `fetch` follows it and `response.json()` fails;
  the guard must answer 401 JSON. Pinned in Task 2.
- **Open redirect through `next`.** `/login?next=//evil.example/x` must land on
  `/`, not another origin, and the same rule must hold for a direct POST. Pinned
  in Tasks 1 and 2.
- **A secret that moves or leaks.** Sessions must survive a restart on the same
  cache but die when the password changes; the cache file alone must not contain
  a key that mints sessions. Pinned in Tasks 1 and 2.
- **A guard that locks out the login page itself.** `/login`, `/logout` and
  `/static/*` must stay reachable while everything else is protected, or the
  login page renders unstyled or loops. Pinned in Task 2.
- **An accidentally open share.** `--host 0.0.0.0` with no password must exit
  with a message, not start. Pinned in Task 2.
- **An expired cookie mid-session.** The running SPA's poll or Refresh must
  navigate to `/login`, not replace the matrix with a parse error. Pinned in
  Task 3.

---

### Task 1: Auth primitives

**Files:**
- Create: `worklog/auth.py`
- Create: `tests/test_auth.py`

**Interfaces:**
- Consumes: `Store.get_meta(key, default="")` and `Store.set_meta(key, value)`.
- Produces:
  - `DEFAULT_NEXT: str = "/"`, `SALT_META_KEY: str = "auth.salt"`
  - `safe_next(value: str | None) -> str`
  - `session_secret(store: Store, password: str) -> str`
  - `class Throttle` with
    `__init__(self, max_failures: int = 5, window_seconds: float = 600, clock: Callable[[], float] = time.monotonic)`,
    `blocked_for(self, key: str) -> int` (seconds remaining, 0 when allowed),
    `fail(self, key: str) -> None`, `clear(self, key: str) -> None`
  - `LOOPBACK_HOSTS: frozenset[str]` and `is_loopback(host: str) -> bool`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_auth.py` with:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_auth.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'worklog.auth'`.

- [ ] **Step 3: Implement the primitives in `worklog/auth.py`**

Module docstring: one shared password, no accounts. Implementation notes:

- `safe_next` returns `DEFAULT_NEXT` unless the value starts with `/` and not
  with `//` or `/\`.
- `session_secret` reads `auth.salt` from the store; when absent it generates
  `secrets.token_hex(32)` and writes it once, then returns
  `hmac.new(salt.encode(), password.encode(), hashlib.sha256).hexdigest()`.
- `Throttle` keeps `dict[str, list[float]]` of failure timestamps under a
  `threading.Lock`, prunes entries older than `window_seconds` on every read and
  write, and returns a positive whole number of seconds (at least 1) when the
  window holds `max_failures` or more.
- `LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})`;
  `is_loopback` is a membership test.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_auth.py -q && ruff check worklog/auth.py tests/test_auth.py`
Expected: PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add worklog/auth.py tests/test_auth.py
git commit -m "worklog: add password-check primitives"
```

---

### Task 2: Guard, login/logout routes and CLI wiring

**Files:**
- Modify: `worklog/auth.py` (add `install_auth`)
- Modify: `worklog/app.py` (`create_app` keyword, `main()` environment and host check)
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: Task 1 primitives; `Store`.
- Produces:
  - `install_auth(app: Flask, store: Store, password: str | None) -> None`
  - `create_app(config, store, scan=None, password: str | None = None) -> Flask`
  - Flask endpoints named `login_page` (`GET /login`), `login_submit`
    (`POST /login`), `logout` (`POST /logout`)
  - Session cookie name `worklog_session`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_auth.py`, adding these imports at the top:

```python
import dataclasses
from urllib.parse import parse_qs, urlsplit

import pytest

from worklog.app import create_app, main
```


```python
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
    assert client.post("/logout").status_code == 302


def test_non_loopback_without_a_password_refuses_to_start(monkeypatch, tmp_path):
    monkeypatch.delenv("WORKLOG_PASSWORD", raising=False)
    with pytest.raises(SystemExit) as err:
        main(["--host", "0.0.0.0", "--cache", str(tmp_path / "w.sqlite")])
    assert "WORKLOG_PASSWORD" in str(err.value)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_auth.py -q`
Expected: FAIL — `ImportError: cannot import name 'create_app'` / `main`, or 404s on
the new routes.

- [ ] **Step 3: Implement `install_auth` and wire it up**

In `worklog/auth.py`, `install_auth(app, store, password)` always registers the
three routes; when `password` is non-empty it also sets the session config and
registers the guard:

- Set `app.secret_key = session_secret(store, password)` and
  `app.config.update(SESSION_COOKIE_NAME="worklog_session",
  SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
  PERMANENT_SESSION_LIFETIME=timedelta(days=7))`.
- `GET /login`: authenticated → redirect `/`; otherwise serve
  `login.html` from `app.static_folder`. Without a password it redirects `/`.
- `POST /login`: build `next_path = safe_next(request.form.get("next"))`, key the
  throttle by `request.remote_addr or "unknown"`, and:
  - when `blocked_for(key)` is non-zero, return status 429 with a `Retry-After`
    header and a tiny HTML body saying how many seconds remain and linking back
    to `/login?next=…` (use `render_template_string` so the link is escaped);
  - on a wrong password call `throttle.fail(key)` and redirect 303 to
    `/login?error=1&next=<encoded next_path>`;
  - on success call `throttle.clear(key)`, `session.clear()`, set
    `session["auth"] = True`, `session.permanent = True`, and redirect 303 to
    `next_path`.
- `POST /logout`: `session.clear()` and redirect 303 to `/login`; without a
  password, redirect `/`.
- Guard (`before_request`) only when a password is set: allow paths `/login`,
  `/logout` and any path starting `/static/`; allow when
  `session.get("auth") is True`; otherwise `401 {"error": "authentication
  required"}` for paths starting `/api/`, and a redirect to
  `/login?next=<encoded request path plus query>` for everything else.
- Compare bytes with `hmac.compare_digest`.

In `worklog/app.py`:

- Import `os`, `install_auth`, `is_loopback`.
- Add `password: str | None = None` as the last `create_app` keyword; call
  `install_auth(app, store, password)` before `return app`. The call is
  unconditional so `/login` and `/logout` exist in both modes.
- In `main()`, after `load_config`, read `password = os.environ.get("WORKLOG_PASSWORD") or None`;
  when it is `None` and `not is_loopback(config.host)`, raise
  `SystemExit(f"refusing to listen on {config.host} without WORKLOG_PASSWORD; "
  "set it, or use --host 127.0.0.1 for an open local dashboard")`.
- Pass `password=password` to `create_app`.

- [ ] **Step 4: Run the auth, API and view tests**

Run: `pytest tests/test_auth.py tests/test_api.py tests/test_views.py -q && ruff check worklog auth tests`
Expected: PASS, ruff clean. Existing tests are unaffected because the `client`
fixture never passes a password.

- [ ] **Step 5: Commit**

```bash
git add worklog/auth.py worklog/app.py tests/test_auth.py
git commit -m "worklog: require a password on pages and API"
```

---

### Task 3: Login page, sign-out and expired-session handling

**Files:**
- Create: `worklog/static/login.html`
- Create: `worklog/static/login.js`
- Modify: `worklog/static/index.html` (top actions)
- Modify: `worklog/static/inspect.html` (top actions)
- Modify: `worklog/static/util.js` (one helper)
- Modify: `worklog/static/app.js` (two fetch sites)
- Modify: `worklog/static/inspect.js` (two fetch sites)
- Modify: `worklog/static/style.css` (login card and inline form)
- Test: `tests/test_auth.py`, `tests/test_ui.py`

**Interfaces:**
- Consumes: `/login`, `/logout`, the 401 API answer.
- Produces: `W.loginRedirect() -> void` in `util.js`; element ids
  `login-password`, `login-next`, `login-error` on the login page.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_auth.py`:

```python
def test_login_page_has_the_form(cfg, store, tmp_path):
    page = locked_app(cfg, store, tmp_path).test_client().get("/login")
    assert page.status_code == 200
    assert b'name="password"' in page.data
    assert b'id="login-next"' in page.data
```

Add to `tests/test_ui.py`:

```python
@pytest.fixture
def locked_server(tmp_path):
    cfg = load_config(overrides={"roots": [str(tmp_path)], "include": ["*"], "cache": str(tmp_path / "w.sqlite")})
    store = Store(cfg.cache)
    app = create_app(cfg, store, password="letmein")
    server = make_server("127.0.0.1", 0, app)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_login_shields_the_dashboard(locked_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        page = context.new_page()
        page.goto(locked_server)
        page.wait_for_selector("#login-password")
        assert "/login" in page.url

        page.fill("#login-password", "wrong")
        page.click("form.login-card button[type=submit]")
        page.wait_for_selector("#login-error:not([hidden])")
        assert "not right" in page.inner_text("#login-error")

        page.fill("#login-password", "letmein")
        page.click("form.login-card button[type=submit]")
        page.wait_for_selector("#kpis .kpi")
        assert page.evaluate("() => fetch('/api/snapshot').then((r) => r.status)") == 200

        context.clear_cookies()
        page.click("#refresh")
        page.wait_for_selector("#login-password")

        page.fill("#login-password", "letmein")
        page.click("form.login-card button[type=submit]")
        page.wait_for_selector("#refresh")
        page.click("form[action='/logout'] button")
        page.wait_for_selector("#login-password")
        assert page.evaluate("() => fetch('/api/snapshot').then((r) => r.status)") == 401
        browser.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_auth.py::test_login_page_has_the_form -q`
Expected: FAIL — no `login.html`.

Run: `LD_LIBRARY_PATH=$(cat /tmp/opencode/pwlibs.path) pytest tests/test_ui.py::test_login_shields_the_dashboard -q`
Expected: FAIL — the login page never appears.

- [ ] **Step 3: Build the page and the client glue**

`login.html`: reuse the `index.html` head (title "Sign in · SpeechEval
Worklog", `/static/style.css`), then a form with `class="login-card"
method="post" action="/login"` containing the `SE` brand, a hidden
`#login-error` paragraph, a labelled password input `#login-password`
(`name="password"`, `autocomplete="current-password"`, `autofocus`,
`required`), a hidden `#login-next` (`name="next"`, value `/`), and a submit
button "Sign in". Load `/static/util.js` then `/static/login.js`.

`login.js`: initialise the theme with `W.theme.init()`, read
`new URLSearchParams(location.search)`, set `#login-next` to `next` (or `/`),
and when `error` is present unhide `#login-error` with
"That password is not right." — or, for `error=throttled`, use the `wait`
value in "Too many failed attempts. Try again in N seconds."

`util.js`: add `W.loginRedirect = function () { location.assign(
"/login?next=" + encodeURIComponent(location.pathname + location.search +
location.hash)); };` next to the theme helpers.

`app.js`: in `load()` and in `refreshNow()` treat `response.status === 401` by
calling `W.loginRedirect()` and returning.

`inspect.js`: in `loadOverview()` and `loadSamples()` treat
`response.status === 401` the same way before the existing `!response.ok`
branch.

`index.html` and `inspect.html`: add
`<form class="inline-form" method="post" action="/logout"><button class="btn ghost"
type="submit" title="Sign out">Sign out</button></form>` to the `top-actions`
block. The button is harmless when auth is off: `/logout` redirects home.

`style.css`: add `.login-page` (grid, centred, min-height 100vh),
`.login-card` (panel, padding, max-width 24rem, border and `--radius`),
`.login-card label`, `.login-card input`, `.login-error` (uses `--bad`), and
`.inline-form { display: inline; margin: 0; }`.

- [ ] **Step 4: Run the auth, API and UI tests**

Run: `pytest tests/test_auth.py tests/test_api.py -q`
Expected: PASS.

Run: `LD_LIBRARY_PATH=$(cat /tmp/opencode/pwlibs.path) pytest tests/test_ui.py -q`
Expected: PASS, including the existing matrix, inspector and theme tests.

- [ ] **Step 5: Commit**

```bash
git add worklog/static/login.html worklog/static/login.js worklog/static/index.html \
        worklog/static/inspect.html worklog/static/util.js worklog/static/app.js \
        worklog/static/inspect.js worklog/static/style.css tests/test_auth.py tests/test_ui.py
git commit -m "worklog: add the sign-in page and sign-out control"
```

---

### Task 4: Documentation and the full suite

**Files:**
- Modify: `README.md`
- Modify: `docs/design.md` (append a part; do not rewrite Parts A–C)

**Interfaces:**
- Consumes: everything above.
- Produces: no code.

- [ ] **Step 1: Update the README**

- Replace the "**No authentication.** Bind loopback by default; share
  deliberately (below)." bullet with a short description of the shared password.
- In "Install and run", show the shared invocation
  `WORKLOG_PASSWORD=… python app.py --host 0.0.0.0`, and state that without it a
  non-loopback bind refuses to start.
- Replace the existing "Sharing" paragraph: the password is required beyond
  loopback; sessions last 7 days; "Sign out" is in the header; the login page
  and static assets are the only unauthenticated paths; list the throttle (five
  failures in ten minutes, per process); and say plainly that the password does
  not encrypt traffic, so an untrusted network still needs an SSH tunnel or an
  HTTPS reverse proxy (whose address the throttle will see).
- Add `worklog/auth.py` to the Layout block with the line
  "single-password login, session guard, login throttle".

- [ ] **Step 2: Append Part D to `docs/design.md`**

A short section `# Part D — the password shield` dated 2026-11-16: why it exists,
the two rejected alternatives (HTTP Basic, proxy auth), the cookie and derived
key, the guard's 401-vs-redirect split, the `next` rule, and a pointer to the
spec. Note that this supersedes the one line of Part B that said authentication
was out of scope.

- [ ] **Step 3: Run the whole suite and lint**

Run: `pytest tests -q && ruff check .`
Expected: PASS, ruff clean.

Run: `LD_LIBRARY_PATH=$(cat /tmp/opencode/pwlibs.path) pytest tests/test_ui.py -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/design.md
git commit -m "docs: describe the password shield"
```

---

## Self-review notes

- **Spec coverage:** password source and CLI refusal (Task 2), guard and 401 split
  (Task 2), login/logout/session and derived key (Tasks 1–2), throttle (Tasks 1–2),
  login page and sign-out (Task 3), expired-session handling (Task 3), tests
  (Tasks 1–3), docs (Task 4).
- **Deliberately not planned:** multiple users, password reset, persistent
  throttling across processes, TLS, a `Secure` cookie flag. All are non-goals in
  the spec.
- **No new file for the secret:** `auth.salt` uses the existing `Store` meta table,
  so the read-only write budget is unchanged apart from one row.
- **Existing tests untouched:** `create_app` without `password` keeps the current
  open behavior, and `/login`/`/logout` exist in both modes so the sign-out control
  never 404s in development.
