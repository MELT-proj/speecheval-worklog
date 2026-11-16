# Password login — design

**Date:** 2026-11-16
**Status:** awaiting approval

## Intent

The worklog is meant to be sent as a link, but it currently has no authentication
of any kind: anyone who learns the address can read every score, log path and
per-sample generation. This adds one shared password in front of the app — no
accounts, no roles, no per-user data — so that finding the URL is not enough.
The app stays read-only; the password is a shield, not an access-control system.

## Goals

- Every page and every JSON endpoint requires the session, except the login page
  and the static assets it and the app already load.
- One password, configured outside the repository, checked in constant time.
- A normal login form, a visible "Sign out", and a session that survives a
  server restart.
- An unauthenticated API call returns `401` JSON rather than an HTML login page,
  so the existing SPA can send the browser to `/login` instead of trying to parse
  HTML as JSON.
- Binding beyond loopback without a password refuses to start, so the shield
  cannot be forgotten when the app is shared.
- No new runtime dependency.

## Non-goals

- Multiple users, usernames, roles, audit trails, or per-person permissions.
- Password reset flows, password storage, or account management.
- TLS termination. If the link crosses an untrusted network, it still goes
  through an SSH tunnel or a reverse proxy that terminates HTTPS; the README
  says so and does not pretend the password encrypts the connection.
- Rate limiting that survives a restart or spans processes. This is a single
  `python app.py` process; a best-effort in-memory throttle is the honest scope.

## Anti-goals

- A login system that stores the password (or a recoverable form of it) in
  `worklog.yaml`: the repository is public.
- Redirecting API requests to an HTML login page, which would turn a 401 into a
  confusing JSON parse error.
- An open redirect: `next` is a same-origin path or it is ignored.
- Making the password check depend on a package that is not already installed.

## Constraints from the existing code

- `create_app(config, store, scan=None)` builds the whole app; tests call it with
  no auth arguments and must keep working. Auth is therefore opt-in through a
  new keyword, not a required config field.
- The scanner thread and SQLite cache are unaffected. `Store` already has
  `get_meta`/`set_meta`, so one small secret can live in the existing `meta`
  table instead of a new file under the cache directory.
- The SPA (`app.js`, `inspect.js`) fetches JSON directly and polls
  `/api/snapshot` every 10 s; both fetch sites need the 401 escape hatch.
- Static files are served from `worklog/static`; the login page can be a plain
  HTML file there, like `index.html` and `inspect.html`.
- The frontend has no build step, so the login page uses the existing
  `style.css` and a small `login.js` (or inline script).

## Approaches considered

1. **Login form + signed session cookie** (chosen). `POST /login` checks the
   password, Flask's existing signed-cookie session records the authentication,
   and a `before_request` guard protects everything else. Gives a real logout,
   no browser credential dialogs, and a clean 401 for the API. Costs one small
   module and one page.
2. **HTTP Basic Auth** (rejected). Fewer lines, but the browser owns the
   credentials, so there is no logout short of clearing site data, the dialog is
   re-shown on every new tab, and the API and page share one undifferentiated
   challenge. The user picked the form.
3. **Reverse-proxy auth** (rejected for this change). Offloading to nginx/Caddy
   avoids app code but pushes a deployment requirement onto every way the app is
   run, including `python app.py` on the workstation.

## Design

### Enabling auth

- The password is read from `WORKLOG_PASSWORD`. An empty value counts as unset.
  It never enters `worklog.yaml`, command-line arguments or SQLite.
- `create_app(..., password=None)` installs the guard only when a non-empty
  password is supplied. Existing tests and a local `python app.py` on loopback
  therefore keep working with no auth.
- `main()` reads the environment and passes it through. If the configured host
  is not a loopback name/address (`127.0.0.1`, `localhost`, `::1`) and no
  password is set, it exits with a message naming `WORKLOG_PASSWORD`; it does not
  silently start an open server on a shareable address.

### The guard (`worklog/auth.py`)

`install_auth(app, store, password)` sets the session configuration and
registers:

- `session.permanent = True`, lifetime 7 days, cookie `HttpOnly` (Flask default)
  and `SameSite=Lax`, named `worklog_session`.
- A `before_request` hook. It allows `/login`, `/logout` and paths under
  `/static/` (the login page needs the stylesheet and `util.js`; no data lives
  there). Everything else requires `session["auth"] is True`.
- Unauthenticated requests under `/api/` get
  `401 {"error": "authentication required"}`, never a redirect. Everything else
  is redirected to `/login?next=<path+query>`.
- `GET /login` serves the static login page. When already authenticated it
  redirects to `/`.
- `POST /login` compares `password` with `hmac.compare_digest` on UTF-8 bytes.
  Success clears the failure count for that address and redirects to the
  sanitized `next` (or `/`). Failure redirects to `/login?error=1&next=…`, so a
  reload does not resubmit the form.
- `POST /logout` clears the session and redirects to `/login`.

`next` is accepted only when it starts with a single `/` and not `//` or `/\`;
anything else becomes `/`. This is the same rule the inspector uses for `back`,
and it prevents an open redirect from a crafted login link.

### Session key

The signing key is derived as `HMAC-SHA256(random_salt, password)`, where the
random salt is generated once with `secrets.token_hex(32)` and stored in the
SQLite `meta` table (`auth.salt`). This gives three properties the obvious
alternatives do not:

- Sessions survive a restart without a key file elsewhere.
- Changing `WORKLOG_PASSWORD` changes the key, so every existing session is
  invalidated.
- Reading the cache file alone does not reveal a key that can mint sessions; an
  attacker also needs the password.

### Throttle

A module-level, lock-protected map from `remote_addr` to recent failure
timestamps: after 5 failures within 10 minutes, `POST /login` answers `429` with
`Retry-After` until the oldest failure ages out. A successful login clears the
entry. Behind a reverse proxy this sees the proxy's address and throttles
everyone together; that is documented, and trusting a spoofable `X-Forwarded-For`
is deliberately not done.

### Frontend

- `login.html` + `login.js`: a small card with the brand, one password field and
  a "Sign in" button; reads `?error=1` and shows one line, carries `next` in a
  hidden input. Reuses `style.css` and the theme helper.
- `index.html` and `inspect.html` gain a "Sign out" form button (POST `/logout`)
  in the existing top-actions block. It is harmless when auth is disabled.
- `app.js` and `inspect.js` check for `401` on their fetches and send the browser
  to `/login?next=<current path+query+hash>`; no other response handling changes.

### Read-only guarantee

The password check writes nothing. The only new write is the one-time
`auth.salt` row in the existing SQLite `meta` table, covered by the "cache is the
only write" rule.

## Tests

- **Unit (`tests/test_auth.py`)**: password compare, `next` sanitization, guard
  allows `/login` and `/static`, pages redirect while APIs return 401, login with
  the right and wrong password, logout, session survives a second app built on
  the same store, changing the password invalidates the old cookie, throttle
  after five failures, auth disabled when no password is passed.
- **API (`tests/test_api.py` or the new file)**: with a password, an anonymous
  `GET /api/snapshot` is 401; after posting the password with the test client's
  cookie jar it is 200.
- **CLI**: a non-loopback host without `WORKLOG_PASSWORD` refuses to start; the
  same host with one starts auth-enabled (tested at the argument-resolution
  level, not by binding a socket).
- **Browser (`tests/test_ui.py`)**: visiting `/` lands on `/login`; wrong password
  shows the error; the right one reaches the matrix; "Sign out" returns to the
  login page and the API is 401 again.
- Existing tests stay unchanged because `create_app` without `password` is
  open on loopback, exactly as today.

## Documentation

- README: replace "No authentication" with the shield; document
  `WORKLOG_PASSWORD`, the loopback-without-password rule, sign-out, the
  loopback/HTTPS caveat, and the proxy/throttle caveat.
- `docs/design.md`: append a short Part D pointing at this spec; do not rewrite
  Parts A–C.

## Deliberate limits

- One password, one session shape. No "remember me" checkbox, no idle timeout
  beyond the 7-day cookie, no forced re-login.
- The throttle is in memory: a restart clears it, and a multi-process deployment
  would get one bucket per process. This app runs as one process.
- The cookie is not `Secure` by default because the documented setup is an SSH
  tunnel to loopback HTTP. A reverse proxy on HTTPS should set it via a
  deployment flag if this becomes a real second mode; not built now.
- Static assets are readable without logging in. They contain no results, logs or
  paths, only the page shell and vendored libraries.

## Open questions for the human

None that change the shape. The cookie lifetime (7 days) and the five-failures
in ten minutes throttle are calls I can change without touching the design.
