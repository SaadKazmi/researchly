# Step 1d — Frontend Shell, SPA Auth and First E2E Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `researchly start` prints a one-time login link. Opening it shows the Researchly SPA shell (navigation, the eleven §18 routes as placeholders, a live health indicator, the app version from an authenticated API call). A Playwright `@smoke` run proves this end to end in CI.

**Architecture:**
- **Auth.** Browsers authenticate with an HttpOnly, SameSite=Strict, host-only cookie. The cookie is set by `GET <non-API path>?token=<t>`, which answers with a 303 that strips the token (the Jupyter / VS Code serve-web pattern).
  - The guard accepts the `x-researchly-token` header (CLI, MCP, tests) or that cookie. The cookie counts only on `Sec-Fetch-Site: same-origin | none`.
  - GET/HEAD outside `/api` and `/mcp` is public, because the SPA's HTML and assets carry no data.
  - uvicorn's access log is redacted.
- **Serving.** The SPA is built by Vite into `backend/src/researchly/web/` (gitignored package data). A `StaticFiles` subclass mounted last serves it, with an `index.html` fallback for client routes.
- **E2E.** `e2e/` is its own pnpm project. Playwright `globalSetup` runs `researchly start` in native mode with isolated data and log dirs, signs in through the login link, and stops the stack at teardown.

**Tech Stack:**
- Node 24.22.2 (provided by pnpm via `devEngines.runtime`), pnpm 12.15.0, TypeScript 6.0.3.
- React 19.3.1, Vite 8.3.5, @vitejs/plugin-react 6.1.x.
- TanStack Router 1.170.x (file-based) + Router plugin 1.168.x, TanStack Query 5.104.x.
- Tailwind 4.3.3 + @tailwindcss/vite, shadcn 4.22.1 (Base UI), i18next 26.5 + react-i18next 17.1.
- openapi-typescript 7.13.0 + openapi-fetch 0.17.0.
- Vitest 5.0.5 + jsdom 30 + Testing Library.
- ESLint 10 + typescript-eslint 8.72 + Prettier 3.9.
- Playwright 1.64.1 (Chromium headless shell) + @axe-core/playwright 4.14.0.
- Backend: FastAPI 0.157 / Starlette 1.9 / uvicorn 0.54 (already locked).

**Spec:** `docs/superpowers/specs/2026-10-28-researchly-design.md`: §4.3 (amended by ADR 0003 in Task 8), §5, §16.1, §17, §18, §19.5, §22, §24 step 1. Research: `docs/research/2026-11-02-step1d-frontend-shell.md`. Read its §2.4, §3 and §5 before Tasks 1, 2 and 7.

## Global Constraints

- **Commands.** All Python commands go through `uv` (`uv run --directory backend …`, `uv run --directory cli …`); the system `python` is 3.8. All Node commands go through `pnpm` 12.15.0 (`pnpm -C frontend …`, `pnpm -C e2e …`); never npm or npx inside the projects. pnpm provides Node 24.22.2 from `devEngines.runtime`; never upgrade the system Node.
- **Supply chain (spec §19.7).** Both `pnpm-workspace.yaml` files set `minimumReleaseAge: 10080` and `trustPolicy: no-downgrade`. Lockfiles are committed. `pnpm install --frozen-lockfile` must pass. Never add a package published less than 7 days ago.
  - The shadcn template's `cn` package (0.5.1, published 2026-10-30) fails this rule: use `clsx` + `tailwind-merge` in `src/lib/utils.ts` instead.
- **TypeScript 6.0.3, not 7.** openapi-typescript crashes on TS 7 and typescript-eslint requires TS < 6.1.
- **Token handling (spec §16.1, ADR 0003).**
  - The SPA contains **no token code**: no reading, storing or sending of the token. The cookie does it.
  - The token never appears in logs, `state.json`, `config.json`, fixtures or committed files.
  - `researchly start` is the only place that prints it, inside the login link.
- **Guard status codes.** Host/Origin failures answer **403**. Token failures answer **401**. A WebSocket on either failure closes with **1008**.
- **Build output.** The SPA builds into `backend/src/researchly/web/`, which is gitignored and listed in hatch `artifacts`. Never commit it. Backend tests must not depend on whether it exists locally (Task 2's autouse fixture).
- **Generated files are committed:** `frontend/src/routeTree.gen.ts`, `frontend/src/api/openapi.json`, `frontend/src/api/schema.d.ts`. ESLint and Prettier ignore all three.
- **Keyless, free (spec D14).** Users never need Node; the wheel and the Docker image carry the built SPA.
- **Licenses.** Everything added is MIT, ISC or Apache-2.0, except `@axe-core/playwright` / `axe-core` (MPL-2.0). That is acceptable only as an e2e dev dependency.
- **Python style.** ruff (line length 120, rules E,F,I,UP,B,SIM,ASYNC,S; S101 ignored) and pyright `standard` stay clean in `backend/` and `cli/`.
- **TS style.** TS strict with `erasableSyntaxOnly` (no parameter properties, enums or namespaces). Prettier style from the shadcn template: no semicolons, double quotes, trailing commas es5, width 80.
- **UI.**
  - UI strings come from `src/locales/en.json` through i18next (spec §18).
  - Accessibility: landmarks, a skip link, a labelled nav, `role="status"` / `role="alert"`, and zero axe violations.
- **Commits.** Conventional Commits; every commit ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File Structure

| Path | Responsibility | Task |
|---|---|---|
| `backend/src/researchly/security/localhost_guard.py` | Host/Origin checks, header-or-cookie token, login link → cookie | 1 |
| `backend/src/researchly/security/redact.py` | + `RedactingArgsFilter` for uvicorn's access formatter | 1 |
| `backend/src/researchly/api/__init__.py`, `api/system.py` | `GET /api/v1/system` → `{"version"}` | 1 |
| `backend/src/researchly/app.py` | OpenAPI under `/api`, token registration, log redaction, router, SPA mount | 1, 2 |
| `backend/src/researchly/spa.py` | `SPAStaticFiles`: index fallback, cache and security headers | 2 |
| `backend/src/researchly/openapi.py` | `python -m researchly.openapi <out.json>` | 2 |
| `backend/tests/security/test_localhost_guard.py`, `tests/test_app.py`, `tests/test_spa.py`, `tests/conftest.py` | Backend tests | 1, 2 |
| `frontend/**` | Vite + React SPA (scaffolded from the shadcn vite template) | 3, 4 |
| `cli/src/researchly_cli/main.py`, `cli/tests/test_main.py` | Login link output; web UI build check | 5 |
| `infra/app.Dockerfile`, `infra/compose.yaml`, `frontend/.dockerignore`, `cli/tests/test_docker.py` | Node build stage in the app image | 6 |
| `e2e/**` | Playwright project, global setup, auth setup, smoke spec | 7 |
| `.claude/hooks/format.py`, `typecheck.py` + tests | Prettier/typecheck for `frontend/` and `e2e/` | 0 |
| `.github/workflows/ci.yml`, `AGENTS.md`, `frontend/AGENTS.md`, `docs/adr/0003-spa-auth-cookie.md`, spec §4.3, `docs/progress.md` | CI jobs and docs | 8 |

---

### Task 0 (controller): hooks cover `frontend/` typecheck correctly and `e2e/`

The shadcn template's root `tsconfig.json` has `"files": []` plus project references. That makes `tsc --noEmit` a no-op, so the typecheck command becomes `pnpm -C frontend typecheck` (the script runs `tsc -b`).

**Files:**
- Modify: `.claude/hooks/format.py`, `.claude/hooks/typecheck.py`
- Test: `.claude/hooks/tests/test_format.py`, `.claude/hooks/tests/test_typecheck.py`

- [ ] **Step 1: Failing tests**

Add to `test_format.py`:

```python
def test_ts_in_e2e(tmp_path):
    make(tmp_path, "e2e/package.json")
    assert fmt.commands_for("e2e/specs/smoke.spec.ts", tmp_path) == [
        ["pnpm", "-C", "e2e", "exec", "prettier", "--write", "specs/smoke.spec.ts"]
    ]
```

Change the frontend expectation in `test_typecheck.py::test_checks_for_backend_and_frontend` to `["pnpm", "-C", "frontend", "typecheck"]`, and add:

```python
def test_checks_for_e2e(tmp_path):
    make(tmp_path, "e2e/package.json")
    assert typecheck.checks_for(["e2e/specs/smoke.spec.ts"], tmp_path) == [["pnpm", "-C", "e2e", "typecheck"]]
```

- [ ] **Step 2: Run** `uv run --no-project --python 3.12 --with pytest pytest .claude/hooks/tests -q` → the new tests FAIL.
- [ ] **Step 3: Implement.**
  - `format.py`: loop `for pkg in ("frontend", "e2e")` with the same prettier command shape.
  - `typecheck.py`: loop `for pkg in ("frontend", "e2e")`, appending `["pnpm", "-C", pkg, "typecheck"]` when `.ts`/`.tsx` files under `pkg/` changed and `pkg/package.json` exists.
- [ ] **Step 4: Run** the hook tests → PASS.
- [ ] **Step 5: Commit** `chore(hooks): format and typecheck frontend and e2e through their package scripts`.

---

### Task 1: Guard: login link → cookie, header-or-cookie auth, public SPA paths, `/api/v1/system`, redacted access log

**Files:**
- Modify: `backend/src/researchly/security/localhost_guard.py` (rewrite), `backend/src/researchly/security/redact.py`, `backend/src/researchly/app.py`
- Create: `backend/src/researchly/api/__init__.py` (empty), `backend/src/researchly/api/system.py`
- Test: `backend/tests/security/test_localhost_guard.py` (rewrite), `backend/tests/test_app.py`

**Interfaces:**
- Produces:
  - `TOKEN_HEADER = "x-researchly-token"`, `TOKEN_COOKIE = "researchly_token"`, `COOKIE_MAX_AGE = 34560000` in `researchly.security.localhost_guard`.
  - `RedactingArgsFilter` in `researchly.security.redact`.
  - `GET /api/v1/system` → `{"version": "<researchly package version>"}` (response model `SystemInfo`).
  - OpenAPI at `/api/openapi.json`, docs at `/api/docs`, no ReDoc.
  - `create_app(settings=None, *, token=None)` keeps its signature.

- [ ] **Step 1: Write the failing tests.** Replace `backend/tests/security/test_localhost_guard.py` with:

```python
import io
import logging

import httpx
import pytest
from uvicorn.logging import AccessFormatter

from researchly.app import create_app
from researchly.config import Settings
from researchly.security.localhost_guard import COOKIE_MAX_AGE, TOKEN_COOKIE, TOKEN_HEADER

TOKEN = "t" * 43
BASE = "http://127.0.0.1:8765"


def client(base_url: str = BASE) -> httpx.AsyncClient:
    app = create_app(Settings(env="test"), token=TOKEN)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base_url)


@pytest.mark.anyio
async def test_get_outside_api_is_public():
    async with client() as c:
        assert (await c.get("/health")).status_code == 200
        assert (await c.get("/p/1/library")).status_code == 404  # no SPA built in tests: public, but nothing there
        assert (await c.head("/p/1/library")).status_code == 404


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/api/openapi.json", "/api/v1/system", "/api", "/mcp", "/mcp/x"])
async def test_api_and_mcp_need_the_token(path):
    async with client() as c:
        assert (await c.get(path)).status_code == 401
        assert (await c.get(path, headers={TOKEN_HEADER: "wrong"})).status_code == 401
        assert (await c.get(path, headers={TOKEN_HEADER: TOKEN})).status_code not in (401, 403)


@pytest.mark.anyio
async def test_writes_outside_api_still_need_the_token():
    async with client() as c:
        assert (await c.post("/p/1/library")).status_code == 401


@pytest.mark.anyio
@pytest.mark.parametrize("base_url", ["http://evil.example:8765", "http://192.168.1.5:8765"])
async def test_foreign_host_rejected(base_url):
    async with client(base_url) as c:
        assert (await c.get("/health")).status_code == 403


@pytest.mark.anyio
async def test_cross_origin_rejected_even_with_token():
    async with client() as c:
        for origin in ("http://evil.example", "null", "http://127.0.0.1:5173"):
            r = await c.get("/api/openapi.json", headers={TOKEN_HEADER: TOKEN, "origin": origin})
            assert r.status_code == 403
        ok = await c.get("/api/openapi.json", headers={TOKEN_HEADER: TOKEN, "origin": BASE})
        assert ok.status_code == 200


@pytest.mark.anyio
async def test_localhost_and_ipv6_hosts_allowed():
    for base in ("http://localhost:8765", "http://[::1]:8765"):
        async with client(base) as c:
            assert (await c.get("/health")).status_code == 200


@pytest.mark.anyio
async def test_login_link_sets_the_cookie_and_strips_the_token():
    async with client() as c:
        r = await c.get(f"/p/1/library?x=1&token={TOKEN}")
    assert r.status_code == 303
    assert r.headers["location"] == "/p/1/library?x=1"
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["referrer-policy"] == "no-referrer"
    cookie = r.headers["set-cookie"]
    for part in (f"{TOKEN_COOKIE}={TOKEN}", "HttpOnly", "Path=/", "SameSite=strict", f"Max-Age={COOKIE_MAX_AGE}"):
        assert part in cookie
    assert "Domain" not in cookie and "Secure" not in cookie  # host-only; plain http on loopback


@pytest.mark.anyio
async def test_login_link_with_a_wrong_token_is_rejected():
    async with client() as c:
        r = await c.get("/?token=wrong")
    assert r.status_code == 401 and "set-cookie" not in r.headers


@pytest.mark.anyio
@pytest.mark.parametrize(("path", "location"), [("//evil.example/x", "/evil.example/x"), ("/\\evil.example", "/%5Cevil.example")])
async def test_login_redirect_never_leaves_this_origin(path, location):
    async with client() as c:
        r = await c.get(httpx.URL(f"{BASE}{path}", params={"token": TOKEN}))
    assert r.status_code == 303 and r.headers["location"] == location


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("site", "cookie", "status"),
    [
        ("same-origin", TOKEN, 200),
        ("none", TOKEN, 200),  # the user typed or bookmarked the URL
        ("same-site", TOKEN, 401),  # another port on 127.0.0.1: SameSite does not stop it
        ("cross-site", TOKEN, 401),
        (None, TOKEN, 401),  # non-browser clients send the header instead
        ("same-origin", "wrong", 401),
    ],
)
async def test_cookie_counts_only_for_same_origin_requests(site, cookie, status):
    headers = {"cookie": f"other=1; {TOKEN_COOKIE}={cookie}"}
    if site is not None:
        headers["sec-fetch-site"] = site
    async with client() as c:
        assert (await c.get("/api/v1/system", headers=headers)).status_code == status


@pytest.mark.anyio
async def test_system_reports_the_version():
    async with client() as c:
        r = await c.get("/api/v1/system", headers={TOKEN_HEADER: TOKEN})
    assert r.status_code == 200 and r.json() == {"version": "0.0.0"}


def test_openapi_and_docs_live_under_api():
    app = create_app(Settings(env="test"), token=TOKEN)
    assert (app.openapi_url, app.docs_url, app.redoc_url) == ("/api/openapi.json", "/api/docs", None)


def test_uvicorn_access_log_redacts_the_login_token():
    create_app(Settings(env="test"), token=TOKEN)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(AccessFormatter(use_colors=False))
    logger = logging.getLogger("uvicorn.access")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        logger.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "GET", f"/?token={TOKEN}", "1.1", 303)
    finally:
        logger.removeHandler(handler)
    assert TOKEN not in stream.getvalue()
    assert "/?token=[REDACTED]" in stream.getvalue()
```

In `backend/tests/test_app.py::test_env_token_is_used_without_the_keychain`, change both `/nope` requests to `/api/nope`, and the expected tuple to `(404, 401)`.

- [ ] **Step 2: Run** `uv run --directory backend pytest tests/security/test_localhost_guard.py tests/test_app.py -q` → FAIL (401 vs 403, missing names, no `/api/v1/system`).

- [ ] **Step 3: Implement.** Replace `localhost_guard.py` with:

```python
"""Localhost guard (spec §4.3, ADR 0003): Host allowlist, same-origin only, per-install token.

The token arrives in the `x-researchly-token` header (CLI, MCP, tests) or in an HttpOnly cookie that the one-time
login link `/?token=<t>` printed by `researchly start` sets (browsers). GET/HEAD outside /api and /mcp is public:
the SPA's HTML and assets carry no data.
"""

import hmac
from urllib.parse import parse_qsl, urlencode, urlsplit

from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

TOKEN_HEADER = "x-researchly-token"  # noqa: S105 (header name, not a secret)
TOKEN_COOKIE = "researchly_token"  # noqa: S105 (cookie name, not a secret)
COOKIE_MAX_AGE = 400 * 24 * 3600  # Chrome's cap; the token never rotates
_HOSTS = {"localhost", "127.0.0.1", "::1"}
_PRIVATE = {"api", "mcp"}  # first path segments that always need the token
_FETCH_SITES = {"same-origin", "none"}  # "none": the user typed or bookmarked the URL


def _private(path: str) -> bool:
    return path.split("/", 2)[1] in _PRIVATE


def _cookie(header: str, name: str) -> str:
    for part in header.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return ""


class LocalhostGuard:
    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
        host = headers.get("host", "")
        origin = headers.get("origin")
        if urlsplit(f"//{host}").hostname not in _HOSTS:
            await _deny(scope, receive, send, "host not allowed", 403)
            return
        if origin is not None and urlsplit(origin).netloc != host:
            await _deny(scope, receive, send, "cross-origin request rejected", 403)
            return
        public = scope["type"] == "http" and scope["method"] in ("GET", "HEAD") and not _private(scope["path"])
        query = parse_qsl(scope.get("query_string", b"").decode("latin-1"), keep_blank_values=True)
        if public and scope["method"] == "GET" and any(key == "token" for key, _ in query):
            await self._login(scope["path"], query)(scope, receive, send)
            return
        if not public and not self._authenticated(headers):
            await _deny(scope, receive, send, "missing or invalid token", 401)
            return
        await self.app(scope, receive, send)

    def _valid(self, value: str) -> bool:
        return hmac.compare_digest(value.encode(), self.token.encode())

    def _authenticated(self, headers: dict[str, str]) -> bool:
        if self._valid(headers.get(TOKEN_HEADER, "")):
            return True
        # a cookie rides along on every request to 127.0.0.1, whatever the port: only trust it from our own pages
        same_origin = headers.get("sec-fetch-site") in _FETCH_SITES
        return same_origin and self._valid(_cookie(headers.get("cookie", ""), TOKEN_COOKIE))

    def _login(self, path: str, query: list[tuple[str, str]]) -> Response:
        """Swap the one-time `?token=` link for the cookie, then reload the same page without the token."""
        given = next(value for key, value in query if key == "token")
        if not self._valid(given):
            return JSONResponse({"detail": "missing or invalid token"}, status_code=401)
        rest = urlencode([(k, v) for k, v in query if k != "token"])
        target = "/" + path.lstrip("/")  # "//evil.example" would be a protocol-relative redirect
        response = RedirectResponse(
            target + (f"?{rest}" if rest else ""),
            status_code=303,
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )
        response.set_cookie(TOKEN_COOKIE, given, max_age=COOKIE_MAX_AGE, httponly=True, samesite="strict")
        return response


async def _deny(scope: Scope, receive: Receive, send: Send, detail: str, status: int) -> None:
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": 1008})
    else:
        await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send)
```


`RedirectResponse` percent-quotes the location, which turns `\` into `%5C`. Browsers would otherwise read `/\evil.example` as `//evil.example`.

Append to `redact.py`:

```python
class RedactingArgsFilter(logging.Filter):
    """For formatters that unpack `record.args` (uvicorn's AccessFormatter): redact each string argument in place."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        return True
```

Create `api/system.py`:

```python
"""System information (spec §17)."""

from importlib.metadata import version

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1")


class SystemInfo(BaseModel):
    version: str


@router.get("/system")
async def system() -> SystemInfo:
    return SystemInfo(version=version("researchly"))
```

Replace `app.py` with:

```python
"""FastAPI application factory."""

import logging
from importlib.metadata import version

from fastapi import FastAPI

from researchly.api import system
from researchly.config import Settings
from researchly.security.keychain import ensure_local_token
from researchly.security.localhost_guard import LocalhostGuard
from researchly.security.redact import RedactingArgsFilter, RedactingFilter, register_secret


def _redact_server_logs() -> None:
    """uvicorn logs request paths with their query string, so a `?token=` login link would reach the access log."""
    for name, kind in (("uvicorn.access", RedactingArgsFilter), ("uvicorn.error", RedactingFilter)):
        target = logging.getLogger(name)
        if not any(isinstance(f, kind) for f in target.filters):
            target.addFilter(kind())


def create_app(settings: Settings | None = None, *, token: str | None = None) -> FastAPI:
    settings = settings or Settings()
    if token is None:
        token = settings.local_token.get_secret_value() if settings.local_token is not None else ensure_local_token()
    register_secret(token)
    _redact_server_logs()
    app = FastAPI(
        title="Researchly",
        version=version("researchly"),
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
    )
    app.state.settings = settings
    app.add_middleware(LocalhostGuard, token=token)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(system.router)
    return app
```

- [ ] **Step 4: Run** `uv run --directory backend pytest -q`, `uv run --directory backend ruff check`, `ruff format --check`, `pyright` → all green. The `-m db` tests need Postgres, as before; run them if the testcontainer is available.
- [ ] **Step 5: Commit** `feat(security): login link sets an HttpOnly cookie; token errors answer 401; redact uvicorn logs`.

---

### Task 2: Serve the built SPA, OpenAPI dump module, packaging

**Files:**
- Create: `backend/src/researchly/spa.py`, `backend/src/researchly/openapi.py`, `backend/tests/test_spa.py`
- Modify: `backend/src/researchly/app.py`, `backend/tests/conftest.py`, `backend/pyproject.toml`, `.gitignore`

**Interfaces:**
- Consumes: `create_app` from Task 1.
- Produces:
  - `researchly.app.WEB_DIR: Path` (= `Path(app.py).parent / "web"`, read at `create_app` time, so tests monkeypatch `researchly.app.WEB_DIR`).
  - `python -m researchly.openapi <out.json>` writes the schema as sorted, 2-space-indented JSON with LF line endings and a trailing newline.

- [ ] **Step 1: Failing tests.** Add an autouse fixture to `backend/tests/conftest.py`:

```python
@pytest.fixture(autouse=True)
def _no_built_frontend(tmp_path, monkeypatch):
    """Tests never depend on whether `pnpm -C frontend build` ran in this checkout."""
    monkeypatch.setattr("researchly.app.WEB_DIR", tmp_path / "no-web")
```

Create `backend/tests/test_spa.py`:

```python
import json

import httpx
import pytest

from researchly import openapi
from researchly.app import create_app
from researchly.config import Settings
from researchly.security.localhost_guard import TOKEN_HEADER

TOKEN = "t" * 43


@pytest.fixture
def web(tmp_path, monkeypatch):
    root = tmp_path / "web"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text('<!doctype html><div id="root"></div>', "utf-8")
    (root / "assets" / "index-abc123.js").write_text("console.log(1)", "utf-8")
    (root / "favicon.svg").write_text("<svg/>", "utf-8")
    monkeypatch.setattr("researchly.app.WEB_DIR", root)
    return root


def client() -> httpx.AsyncClient:
    app = create_app(Settings(env="test"), token=TOKEN)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765")


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/", "/p/42/chat/7", "/settings/providers", "/memory"])
async def test_client_routes_get_index_html(web, path):
    async with client() as c:
        r = await c.get(path)
    assert r.status_code == 200 and 'id="root"' in r.text
    assert r.headers["cache-control"] == "no-cache"
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"


@pytest.mark.anyio
async def test_hashed_assets_are_immutable_and_missing_ones_404(web):
    async with client() as c:
        asset = await c.get("/assets/index-abc123.js")
        missing = await c.get("/assets/index-gone.js")
        favicon = await c.get("/favicon.svg")
    assert asset.status_code == 200 and "immutable" in asset.headers["cache-control"]
    assert missing.status_code == 404
    assert favicon.status_code == 200 and favicon.headers["cache-control"] == "no-cache"


@pytest.mark.anyio
async def test_unknown_api_paths_stay_json_404s(web):
    async with client() as c:
        r = await c.get("/api/v1/nope", headers={TOKEN_HEADER: TOKEN})
        health = await c.get("/health")
    assert r.status_code == 404 and r.json() == {"detail": "Not Found"}
    assert health.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_without_a_build_the_api_still_works():
    async with client() as c:
        assert (await c.get("/")).status_code == 404
        assert (await c.get("/health")).status_code == 200


def test_openapi_module_writes_the_schema(tmp_path):
    out = tmp_path / "openapi.json"
    openapi.main([str(out)])
    raw = out.read_bytes()
    assert raw.endswith(b"}\n") and b"\r\n" not in raw
    assert {"/health", "/api/v1/system"} <= set(json.loads(raw)["paths"])
```

- [ ] **Step 2: Run** `uv run --directory backend pytest tests/test_spa.py -q` → FAIL (no `researchly.openapi`, no mount).

- [ ] **Step 3: Implement.** Create `spa.py`:

```python
"""Serve the built SPA (spec §18): index.html for client-side routes, immutable hashed assets, security headers."""

import os

from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

_NO_FALLBACK = {"api", "mcp", "assets"}  # unknown API paths stay JSON 404s; a missing asset is a real 404
_HEADERS = {
    # 'unsafe-inline' styles: Base UI positions popups with style attributes
    "Content-Security-Policy": (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or path.split(os.sep, 1)[0] in _NO_FALLBACK:
                raise
            response = await super().get_response("index.html", scope)
        immutable = path.startswith("assets" + os.sep) and response.status_code == 200  # Vite hashes these names
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable" if immutable else "no-cache"
        response.headers.update(_HEADERS)
        return response
```

Add to `app.py`:

```python
from pathlib import Path

from researchly.spa import SPAStaticFiles

WEB_DIR = Path(__file__).parent / "web"  # `pnpm -C frontend build` writes the SPA here
logger = logging.getLogger(__name__)
```

At the end of `create_app`, before `return app`:

```python
    if WEB_DIR.is_dir():
        app.mount("/", SPAStaticFiles(directory=WEB_DIR, html=True), name="spa")  # last: the routes above win
    else:
        logger.warning("frontend not built (%s is missing): run `pnpm -C frontend build`", WEB_DIR)
```

Create `openapi.py`:

```python
"""Write the OpenAPI schema without a server or the keychain (spec §17): `python -m researchly.openapi <out.json>`."""

import json
import sys
from pathlib import Path

from researchly.app import create_app


def main(argv: list[str] | None = None) -> None:
    out = Path((sys.argv[1:] if argv is None else argv)[0])
    schema = create_app(token="openapi-dump").openapi()  # noqa: S106 (placeholder; the schema holds no token)
    out.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", "utf-8", newline="\n")


if __name__ == "__main__":
    main()
```

`backend/pyproject.toml`, under `[tool.hatch.build.targets.wheel]`:

```toml
artifacts = ["src/researchly/web/**"]  # the built SPA is gitignored, but it ships in the wheel
```

`.gitignore`, under `# Node`, add `backend/src/researchly/web/`.

- [ ] **Step 4: Run** the full backend suite plus ruff/pyright → green. Then check packaging:

```bash
mkdir -p backend/src/researchly/web && echo '<!doctype html>' > backend/src/researchly/web/index.html
uv build --directory backend --wheel -o "$TMP/wheel" && python -c "import zipfile,glob; print([n for n in zipfile.ZipFile(glob.glob('$TMP/wheel/*.whl')[0]).namelist() if '/web/' in n])"
rm -rf backend/src/researchly/web
```

Use `uv run --no-project python` for the zip check on this box. Expected: `['researchly/web/index.html']`.

- [ ] **Step 5: Commit** `feat(app): serve the built SPA with an index fallback; OpenAPI dump module`.

---

### Task 3: Frontend scaffold and tooling

**Files:** Create `frontend/*` from the shadcn vite template, keeping the existing `frontend/AGENTS.md`.

**Interfaces:**
- Produces scripts:
  - `pnpm -C frontend dev | build | lint | format | typecheck | test`;
  - `api:schema` (Task 4 fills `src/api/`).
- Produces the path alias `@/` → `src/`, and the Vitest setup at `frontend/tests/setup.ts`.

- [ ] **Step 1: Scaffold** in a scratch directory outside the repo, then copy in:

```bash
cd "$SCRATCH" && pnpm dlx shadcn@4.22.1 init -t vite -b base -p nova -n frontend --no-monorepo --no-rtl -y
rm -rf frontend/.git frontend/node_modules frontend/README.md frontend/pnpm-lock.yaml frontend/src/App.tsx frontend/src/assets frontend/public/vite.svg
cp -r frontend/. <repo>/frontend/   # frontend/AGENTS.md is kept
```

- [ ] **Step 2: Apply these changes** to the copied files.

`package.json`:
- name `researchly-web`, version `0.0.0`.
- Add `"packageManager": "pnpm@12.15.0"`.
- Add `"devEngines": {"runtime": {"name": "node", "version": "24.22.2", "onFail": "download"}}`.
- Scripts:

```json
{
  "dev": "vite",
  "build": "tsc -b && vite build",
  "lint": "eslint .",
  "format": "prettier --write .",
  "typecheck": "tsc -b",
  "test": "vitest run",
  "api:schema": "uv run --directory ../backend python -m researchly.openapi ../frontend/src/api/openapi.json && openapi-typescript src/api/openapi.json -o src/api/schema.d.ts"
}
```

- Dependencies:
  - Remove `cn`. Move `shadcn` to devDependencies (it is only imported by CSS at build time).
  - Add dependencies: `@tanstack/react-router@^1.170.0`, `@tanstack/react-query@^5.104.0`, `i18next@^26.5.0`, `react-i18next@^17.1.0`, `openapi-fetch@0.17.0`, `clsx@^2.1.1`, `tailwind-merge@^3.7.0`.
  - Add devDependencies: `@tanstack/router-plugin@^1.168.0`, `openapi-typescript@7.13.0`, `vitest@^5.0.5`, `jsdom@^30.1.0`, `@testing-library/react@^16.3.0`, `@testing-library/dom@^10.4.0`, `@testing-library/jest-dom@^7.0.0`.
  - Set `typescript` to `~6.0.3`.
- Install with `pnpm -C frontend install`: the cooldown picks versions at least 7 days old.

`pnpm-workspace.yaml`:

```yaml
packages: []

# Supply chain (spec §19.7): nothing younger than 7 days; no trust downgrades
minimumReleaseAge: 10080
trustPolicy: no-downgrade

allowBuilds:
  esbuild: true
  msw: false

peerDependencyRules:
  allowedVersions:
    openapi-typescript>typescript: "6" # works on TS 6 (verified); its peer range still says ^5
```

If `trustPolicy: no-downgrade` rejects a specific package, add it to `trustPolicyExclude` with a one-line comment naming why, and report it.

`src/lib/utils.ts`:

```ts
import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
```

In `src/components/ui/button.tsx`, change `from "cn"` to `from "@/lib/utils"`.

`eslint.config.js`: `globalIgnores(["dist", "src/routeTree.gen.ts", "src/api/schema.d.ts"])`.

`.prettierignore`: append `src/routeTree.gen.ts`, `src/api/schema.d.ts` and `src/api/openapi.json`; dedupe the template's duplicate `pnpm-lock.yaml`.

`tsconfig.app.json`:
- `"include": ["src", "tests"]`;
- add `"resolveJsonModule": true`;
- add `"@testing-library/jest-dom/vitest"` to `types`.

`index.html`:
- `<title>Researchly</title>`;
- favicon `<link rel="icon" type="image/svg+xml" href="/favicon.svg" />`.

`public/favicon.svg`:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="7" fill="#171717"/><path d="M11 24V8h6a5 5 0 0 1 0 10h-6m6 0 5 6" fill="none" stroke="#fafafa" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
```

`vite.config.ts`:

```ts
/// <reference types="vitest/config" />
import { resolve } from "node:path"
import tailwindcss from "@tailwindcss/vite"
import { tanstackRouter } from "@tanstack/router-plugin/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

// Dev: `researchly start`, open its login link once (the cookie is host-wide), then use http://127.0.0.1:5173
const app = `http://127.0.0.1:${process.env.RESEARCHLY_PORT ?? "8765"}`
const api = { target: app, changeOrigin: true, headers: { origin: app } } // the guard rejects a foreign Origin

export default defineConfig({
  plugins: [
    tanstackRouter({ target: "react", autoCodeSplitting: true }), // must come before react()
    react(),
    tailwindcss(),
  ],
  resolve: { alias: { "@": resolve(import.meta.dirname, "./src") } },
  build: { outDir: "../backend/src/researchly/web", emptyOutDir: true }, // served by FastAPI
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: { "/api": api, "/health": api },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.{ts,tsx}"],
  },
})
```

`tests/setup.ts`:

```ts
import "@testing-library/jest-dom/vitest"
import { cleanup } from "@testing-library/react"
import { afterEach } from "vitest"

afterEach(cleanup)
```

Temporary `src/main.tsx` until Task 4 replaces it. Keep the template's `ThemeProvider` and render `<p>Researchly</p>` inside it.

`tests/smoke.test.tsx` (proves the toolchain; Task 4 replaces it):

```tsx
import { render, screen } from "@testing-library/react"
import { expect, test } from "vitest"

import { Button } from "@/components/ui/button"

test("renders a shadcn button", () => {
  render(<Button>Go</Button>)
  expect(screen.getByRole("button", { name: "Go" })).toBeInTheDocument()
})
```

- [ ] **Step 3: Verify.**
  - `pnpm -C frontend install`, then `lint`, `exec prettier --check .`, `typecheck`, `test` and `build` all pass.
  - `pnpm -C frontend build` writes `backend/src/researchly/web/index.html`.
  - `git status` shows nothing under `backend/src/researchly/web/`.
  - `pnpm -C frontend exec node --version` prints `v24.22.2`.
- [ ] **Step 4: Commit** `feat(frontend): scaffold Vite + React 19 + shadcn (Base UI) + Tailwind 4 with Vitest`. Commit `pnpm-lock.yaml`.

---

### Task 4: App shell, routes, API client, i18n

**Files:**
- Create:
  - `frontend/src/router.tsx`, `src/i18n.ts`, `src/locales/en.json`;
  - `src/api/client.ts`, `src/api/queries.ts`, plus the generated `src/api/openapi.json` and `src/api/schema.d.ts`;
  - `src/components/{placeholder,status-indicator,version,auth-notice}.tsx`;
  - `src/routes/__root.tsx`, `index.tsx`, `p/$id/chat/$thread.tsx`, `p/$id/library.tsx`, `p/$id/graph.tsx`, `p/$id/docs/$doc.tsx`, `p/$id/studio.tsx`, `p/$id/review.tsx`, `memory.tsx`, `usage.tsx`, `settings/$.tsx`, `onboarding.tsx`;
  - the generated `src/routeTree.gen.ts`;
  - `tests/shell.test.tsx`.
- Modify: `src/main.tsx`. Delete: `tests/smoke.test.tsx`.

**Interfaces:**
- Consumes: `GET /health` → `{"status": "ok"}`; `GET /api/v1/system` → `{"version": string}`; 401 when the cookie is missing (Task 1).
- Produces:
  - `createAppRouter(history?: RouterHistory)` from `@/router`;
  - `api` (openapi-fetch client) and `ApiError` (field `status: number`) from `@/api/client`;
  - `healthQuery` and `systemQuery` from `@/api/queries`.
- The E2E (Task 7) relies on this exact UI text and these roles:
  - `navigation "Main"` containing links `Projects`, `Memory`, `Usage`, `Settings`;
  - `role="status"` with text `Connected` / `Disconnected` / `Checking…`;
  - the version text `v<version>`;
  - `role="alert"` containing `researchly start` on a 401;
  - a placeholder `h1` per route (`Projects`, `Memory`, `Library` …).

- [ ] **Step 1: Generate the API types.** `pnpm -C frontend api:schema` writes `src/api/openapi.json` and `src/api/schema.d.ts`. Commit both with this task.

- [ ] **Step 2: Failing tests.** Create `tests/shell.test.tsx` and delete `tests/smoke.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router"
import { render, screen, within } from "@testing-library/react"
import { afterEach, expect, test, vi } from "vitest"

import "@/i18n"
import { createAppRouter } from "@/router"

function serve(routes: Record<string, () => Response>) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const handler = routes[new URL(request.url).pathname]
      if (!handler) throw new TypeError("Failed to fetch")
      return handler()
    })
  )
}

function renderAt(path: string) {
  const router = createAppRouter(createMemoryHistory({ initialEntries: [path] }))
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  )
}

const healthy = () => Response.json({ status: "ok" })

afterEach(() => {
  vi.unstubAllGlobals()
})

test("shows the navigation, a connected status and the version", async () => {
  serve({ "/health": healthy, "/api/v1/system": () => Response.json({ version: "1.2.3" }) })
  renderAt("/")
  expect(await screen.findByRole("heading", { name: "Projects" })).toBeInTheDocument()
  const nav = screen.getByRole("navigation", { name: "Main" })
  for (const name of ["Projects", "Memory", "Usage", "Settings"]) {
    expect(within(nav).getByRole("link", { name })).toBeInTheDocument()
  }
  expect(await screen.findByText("Connected")).toBeInTheDocument()
  expect(await screen.findByText("v1.2.3")).toBeInTheDocument()
  expect(screen.queryByRole("alert")).not.toBeInTheDocument()
})

test("asks for the login link when the API answers 401", async () => {
  serve({
    "/health": healthy,
    "/api/v1/system": () => Response.json({ detail: "missing or invalid token" }, { status: 401 }),
  })
  renderAt("/")
  expect(await screen.findByRole("alert")).toHaveTextContent("researchly start")
})

test("shows disconnected when the app is unreachable", async () => {
  serve({})
  renderAt("/")
  expect(await screen.findByText("Disconnected")).toBeInTheDocument()
})

test.each([
  ["/p/42/library", "Library"],
  ["/p/42/chat/7", "Chat"],
  ["/settings/providers", "Settings"],
  ["/onboarding", "Welcome"],
])("renders the %s placeholder", async (path, heading) => {
  serve({ "/health": healthy, "/api/v1/system": () => Response.json({ version: "0.0.0" }) })
  renderAt(path)
  expect(await screen.findByRole("heading", { level: 1, name: heading })).toBeInTheDocument()
})
```


- [ ] **Step 3: Run** `pnpm -C frontend test` → FAIL (`@/router` missing).

- [ ] **Step 4: Implement.**

`src/locales/en.json`:

```json
{
  "app": { "name": "Researchly", "version": "v{{version}}" },
  "shell": { "skip": "Skip to content", "nav": "Main" },
  "nav": {
    "projects": "Projects",
    "memory": "Memory",
    "usage": "Usage",
    "settings": "Settings"
  },
  "status": {
    "checking": "Checking…",
    "connected": "Connected",
    "disconnected": "Disconnected"
  },
  "auth": {
    "signIn": "Open Researchly from the link printed by <code>researchly start</code>."
  },
  "routes": {
    "projects": "Projects",
    "chat": "Chat",
    "library": "Library",
    "graph": "Graph",
    "document": "Document",
    "studio": "Studio",
    "review": "Review",
    "memory": "Memory",
    "usage": "Usage",
    "settings": "Settings",
    "onboarding": "Welcome"
  },
  "placeholder": "This view arrives in a later build step."
}
```

`src/i18n.ts`:

```ts
import i18n from "i18next"
import { initReactI18next } from "react-i18next"

import en from "./locales/en.json"

// English ships first (spec §18); resources are bundled, so initialise synchronously
void i18n.use(initReactI18next).init({
  resources: { en: { translation: en } },
  lng: "en",
  fallbackLng: "en",
  initAsync: false,
  interpolation: { escapeValue: false }, // React escapes
})

export default i18n
```

`src/api/client.ts`:

```ts
import createClient from "openapi-fetch"

import type { paths } from "./schema"

// Same-origin calls carry the HttpOnly login cookie (ADR 0003): the SPA never handles the token.
// fetch is looked up per call so tests can stub it.
export const api = createClient<paths>({
  baseUrl: window.location.origin,
  fetch: (request) => fetch(request),
})

export class ApiError extends Error {
  status: number

  constructor(status: number) {
    super(`HTTP ${status}`)
    this.status = status
  }
}
```

`src/api/queries.ts`:

```ts
import { queryOptions } from "@tanstack/react-query"

import { api, ApiError } from "./client"

export const healthQuery = queryOptions({
  queryKey: ["health"],
  queryFn: async () => {
    const { data, response } = await api.GET("/health")
    if (!data) throw new ApiError(response.status)
    return data
  },
  refetchInterval: 5_000,
})

export const systemQuery = queryOptions({
  queryKey: ["system"],
  queryFn: async () => {
    const { data, response } = await api.GET("/api/v1/system")
    if (!data) throw new ApiError(response.status)
    return data
  },
})
```

`src/router.tsx`:

```tsx
import { createRouter, type RouterHistory } from "@tanstack/react-router"

import { routeTree } from "./routeTree.gen"

export function createAppRouter(history?: RouterHistory) {
  return createRouter({ routeTree, history })
}

declare module "@tanstack/react-router" {
  interface Register {
    router: ReturnType<typeof createAppRouter>
  }
}
```

`src/components/placeholder.tsx`:

```tsx
import { useTranslation } from "react-i18next"

export function Placeholder({ titleKey }: { titleKey: string }) {
  const { t } = useTranslation()
  return (
    <section className="flex flex-col gap-2">
      <h1 className="text-2xl font-semibold">{t(titleKey)}</h1>
      <p className="text-muted-foreground">{t("placeholder")}</p>
    </section>
  )
}
```

`src/components/status-indicator.tsx`:

```tsx
import { useQuery } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"

import { healthQuery } from "@/api/queries"
import { cn } from "@/lib/utils"

const DOT = {
  checking: "bg-muted-foreground",
  connected: "bg-emerald-600",
  disconnected: "bg-destructive",
}

export function StatusIndicator() {
  const { t } = useTranslation()
  const { isPending, isError } = useQuery(healthQuery)
  const state = isPending ? "checking" : isError ? "disconnected" : "connected"
  return (
    <p role="status" className="mt-auto flex items-center gap-2 text-sm">
      <span aria-hidden className={cn("size-2 rounded-full", DOT[state])} />
      {t(`status.${state}`)}
    </p>
  )
}
```

`src/components/version.tsx`:

```tsx
import { useQuery } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"

import { systemQuery } from "@/api/queries"

export function Version() {
  const { t } = useTranslation()
  const { data } = useQuery(systemQuery)
  if (!data) return null
  return <p className="text-xs text-muted-foreground">{t("app.version", { version: data.version })}</p>
}
```

`src/components/auth-notice.tsx`:

```tsx
import { useQuery } from "@tanstack/react-query"
import { Trans } from "react-i18next"

import { ApiError } from "@/api/client"
import { systemQuery } from "@/api/queries"

export function AuthNotice() {
  const { error } = useQuery(systemQuery)
  if (!(error instanceof ApiError && error.status === 401)) return null
  return (
    <p role="alert" className="rounded-md border border-destructive/50 p-3 text-sm">
      <Trans i18nKey="auth.signIn" components={{ code: <code className="font-mono" /> }} />
    </p>
  )
}
```

`src/routes/__root.tsx`:

```tsx
import { createRootRoute, Link, Outlet } from "@tanstack/react-router"
import { useTranslation } from "react-i18next"

import { AuthNotice } from "@/components/auth-notice"
import { StatusIndicator } from "@/components/status-indicator"
import { Version } from "@/components/version"

export const Route = createRootRoute({ component: Shell })

const link = "block rounded-md px-2 py-1 text-sm hover:bg-muted"
const active = { className: "bg-muted font-medium" }

function Shell() {
  const { t } = useTranslation()
  return (
    <div className="flex min-h-svh">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:m-2 focus:rounded-md focus:bg-background focus:p-2"
      >
        {t("shell.skip")}
      </a>
      <aside className="flex w-56 shrink-0 flex-col gap-6 border-r p-4">
        <div>
          <p className="font-semibold">{t("app.name")}</p>
          <Version />
        </div>
        <nav aria-label={t("shell.nav")}>
          <ul className="flex flex-col gap-1">
            <li>
              <Link to="/" className={link} activeProps={active} activeOptions={{ exact: true }}>
                {t("nav.projects")}
              </Link>
            </li>
            <li>
              <Link to="/memory" className={link} activeProps={active}>
                {t("nav.memory")}
              </Link>
            </li>
            <li>
              <Link to="/usage" className={link} activeProps={active}>
                {t("nav.usage")}
              </Link>
            </li>
            <li>
              <Link to="/settings/$" params={{ _splat: "" }} className={link} activeProps={active}>
                {t("nav.settings")}
              </Link>
            </li>
          </ul>
        </nav>
        <StatusIndicator />
      </aside>
      <main id="main" tabIndex={-1} className="flex flex-1 flex-col gap-4 p-6 outline-none">
        <AuthNotice />
        <Outlet />
      </main>
    </div>
  )
}
```

**Route files.** Each one is the same shape with its own path and key. The path strings must match the file location exactly; the router plugin checks them.

| File | `createFileRoute(...)` | `titleKey` |
|---|---|---|
| `index.tsx` | `"/"` | `routes.projects` |
| `p/$id/chat/$thread.tsx` | `"/p/$id/chat/$thread"` | `routes.chat` |
| `p/$id/library.tsx` | `"/p/$id/library"` | `routes.library` |
| `p/$id/graph.tsx` | `"/p/$id/graph"` | `routes.graph` |
| `p/$id/docs/$doc.tsx` | `"/p/$id/docs/$doc"` | `routes.document` |
| `p/$id/studio.tsx` | `"/p/$id/studio"` | `routes.studio` |
| `p/$id/review.tsx` | `"/p/$id/review"` | `routes.review` |
| `memory.tsx` | `"/memory"` | `routes.memory` |
| `usage.tsx` | `"/usage"` | `routes.usage` |
| `settings/$.tsx` | `"/settings/$"` | `routes.settings` |
| `onboarding.tsx` | `"/onboarding"` | `routes.onboarding` |

For example, `src/routes/memory.tsx`:

```tsx
import { createFileRoute } from "@tanstack/react-router"

import { Placeholder } from "@/components/placeholder"

export const Route = createFileRoute("/memory")({
  component: () => <Placeholder titleKey="routes.memory" />,
})
```

`src/main.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { RouterProvider } from "@tanstack/react-router"
import { StrictMode } from "react"
import { createRoot } from "react-dom/client"

import "./index.css"
import "./i18n"
import { ThemeProvider } from "@/components/theme-provider"
import { createAppRouter } from "./router"

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false } }, // the health poll is the retry
})
const router = createAppRouter()

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </ThemeProvider>
  </StrictMode>
)
```

`pnpm -C frontend build` (or `dev`) generates `src/routeTree.gen.ts`. Commit it.

- [ ] **Step 5: Run.**
  - `pnpm -C frontend test`, `lint`, `exec prettier --check .`, `typecheck` and `build` all pass.
  - `pnpm -C frontend api:schema` followed by `git diff --exit-code -- frontend/src/api` is clean.
- [ ] **Step 6: Manual check (required; report what you saw).**
  - `uv run --directory cli researchly start` prints the plain URL until Task 5. Open `http://127.0.0.1:<port>/?token=<RESEARCHLY_LOCAL_TOKEN>`. The local token is in the OS keychain; get it with `uv run --directory backend python -c "from researchly.security.keychain import ensure_local_token; print(ensure_local_token())"`.
  - Confirm the shell renders with "Connected" and the version, then `researchly stop`.
  - Use `playwright-cli` if available; otherwise describe the curl checks: 303 + `Set-Cookie`; then `GET /` with that cookie and `Sec-Fetch-Site: same-origin`; then `/api/v1/system`.
- [ ] **Step 7: Commit** `feat(frontend): app shell with §18 routes, health status, version and sign-in notice`.

---

### Task 5: CLI prints the login link and checks the web UI build

**Files:** Modify `cli/src/researchly_cli/main.py`; Test `cli/tests/test_main.py`.

**Interfaces:**
- Consumes: `researchly.app.WEB_DIR` (Task 2); `state.Secrets.local_token`.
- Produces:
  - `start` prints `Researchly is running at http://127.0.0.1:<port>/?token=<hex>`. On an existing healthy run it prints `Already running (<mode>) at http://127.0.0.1:<port>/?token=<hex>`.
  - The E2E global setup (Task 7) parses `/http:\/\/127\.0\.0\.1:\d+\/\?token=[0-9a-f]+/` from this output.

- [ ] **Step 1: Failing tests** in `cli/tests/test_main.py`:

```python
def test_start_native_writes_state_and_prints_url(monkeypatch, healthy, tmp_path):
    monkeypatch.setattr(main, "WEB_DIR", tmp_path)  # built
    monkeypatch.setattr(main, "_select", lambda: engines.Selection("native", "runtime pinned to native"))
    monkeypatch.setattr(native, "start", lambda secrets: state.State(mode="native", port=9999))
    result = runner.invoke(app, ["start"])
    assert result.exit_code == 0, result.output
    assert f"http://127.0.0.1:9999/?token={state.load_secrets().local_token}" in result.stdout
    assert "not built" not in result.output
    assert state.read_state() == state.State(mode="native", port=9999)
    assert state.load_config()["last_mode"] == "native"


def test_start_native_warns_when_the_web_ui_is_not_built(monkeypatch, healthy, tmp_path):
    monkeypatch.setattr(main, "WEB_DIR", tmp_path / "missing")
    monkeypatch.setattr(main, "_select", lambda: engines.Selection("native", "runtime pinned to native"))
    monkeypatch.setattr(native, "start", lambda secrets: state.State(mode="native", port=9999))
    result = runner.invoke(app, ["start"])
    assert result.exit_code == 0 and "pnpm -C frontend build" in result.output
```

Replace the existing `test_start_native_writes_state_and_prints_url` with the first test. In `test_start_is_a_noop_when_already_healthy`, also assert `f"/?token={state.load_secrets().local_token}" in result.stdout`. Add:

```python
def test_status_never_prints_the_token(healthy):
    state.write_state(state.State(mode="native", port=9999))
    healthy.add(9999)
    result = runner.invoke(app, ["status"])
    assert "http://127.0.0.1:9999" in result.stdout and "token" not in result.stdout


@pytest.mark.parametrize(("built", "expected"), [(True, "web ui: built"), (False, "web ui: not built")])
def test_doctor_reports_the_web_ui_build(monkeypatch, tmp_path, built, expected):
    monkeypatch.setattr(engines, "detect", lambda: None)
    monkeypatch.setattr(main, "WEB_DIR", tmp_path if built else tmp_path / "missing")
    assert expected in runner.invoke(app, ["doctor"]).stdout
```

- [ ] **Step 2: Run** `uv run --directory cli pytest tests/test_main.py -q` → FAIL.
- [ ] **Step 3: Implement** in `main.py`:

```python
from researchly.app import WEB_DIR

BUILD_HINT = "run `pnpm -C frontend install` and `pnpm -C frontend build`"


def _login_url(run: state.State, secrets: state.Secrets) -> str:
    """The one-time link: opening it sets the browser's auth cookie (ADR 0003). Only `start` prints it."""
    return f"{run.url}/?token={secrets.local_token}"
```

- In `start`:
  - the "Already running" line uses `_login_url(current, secrets)`;
  - the final line uses `_login_url(run, secrets)`;
  - after `_warn_on_runtime_switch(...)`, add:

```python
    if selection.mode == "native" and not WEB_DIR.is_dir():  # the Docker image builds its own copy
        typer.secho(f"warning: the web UI is not built; {BUILD_HINT}", err=True)
```

- In `doctor`, after the `logs:` line:

```python
    typer.echo(f"web ui: {'built' if WEB_DIR.is_dir() else f'not built (native mode; {BUILD_HINT})'}")
```

- [ ] **Step 4: Run** `uv run --directory cli pytest -q` (the native tests start pg0, as before), `ruff check`, `ruff format --check` and `pyright` → green.
- [ ] **Step 5: Commit** `feat(cli): start prints the one-time login link; doctor reports the web UI build`.

---

### Task 6: The Docker app image builds the SPA

**Files:**
- Modify: `infra/app.Dockerfile`, `infra/compose.yaml` (the `x-app` anchor), `cli/tests/test_docker.py`
- Create: `frontend/.dockerignore`

- [ ] **Step 1: Failing test.** In `cli/tests/test_docker.py::test_docker_stack_serves_health_with_bm25`, after `assert state.healthy(run.port)`:

```python
        with urllib.request.urlopen(f"{run.url}/") as page:  # noqa: S310 - fixed local URL
            assert page.status == 200 and b'<div id="root">' in page.read()
```

Add `import urllib.request`. Also add a static test that runs without Docker:

```python
def test_app_image_builds_the_frontend():
    text = docker.COMPOSE_FILE.read_text("utf-8")
    assert "additional_contexts" in text and "frontend: ../frontend" in text
```

- [ ] **Step 2: Run** `uv run --directory cli pytest tests/test_docker.py -q` → the static test FAILS.
- [ ] **Step 3: Implement.** `infra/app.Dockerfile`:

```dockerfile
# syntax=docker/dockerfile:1
# The Researchly app (spec §4.1 Docker mode). Build context: backend/; named context `frontend`: frontend/.
FROM node:24.22.2-trixie-slim AS web
RUN npm install --global pnpm@12.15.0
WORKDIR /repo/frontend
COPY --from=frontend package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY --from=frontend . .
# vite's outDir is ../backend/src/researchly/web
RUN pnpm build

FROM python:3.12-slim-trixie
COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    LITELLM_LOCAL_MODEL_COST_MAP=True
WORKDIR /src
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY . .
COPY --from=web /repo/backend/src/researchly/web ./src/researchly/web
RUN uv sync --locked --no-dev --no-editable \
 && useradd --system --uid 10001 researchly
USER researchly
EXPOSE 8000
CMD ["uvicorn", "--factory", "researchly.app:create_app", "--host", "0.0.0.0", "--port", "8000"]
```

Check that the `node:24.22.2-trixie-slim` tag exists (`docker manifest inspect` or Docker Hub). If it does not, use the newest `24.x-trixie-slim` tag and report it.

`infra/compose.yaml` `x-app.build`: add `additional_contexts:` with `frontend: ../frontend`.

`frontend/.dockerignore`:

```
node_modules
dist
coverage
*.log
.env*
```

- [ ] **Step 4: Run** `RESEARCHLY_TEST_DOCKER=1 uv run --directory cli pytest -m docker -q -rA`. Start Docker Desktop if it is stopped; the CLI does this. The build log's frontend context transfer must be well under 5 MB, which proves `.dockerignore` applies to the named context; report the figure. If Docker cannot run on this box, report that. CI's `docker` job then runs the test. Also run `uv run --directory cli pytest -q` → green.
- [ ] **Step 5: Commit** `feat(infra): build the SPA into the app image`.

---

### Task 7: Playwright E2E project and `@smoke`

**Files:**
- Create: `e2e/package.json`, `e2e/pnpm-workspace.yaml`, `e2e/pnpm-lock.yaml`, `e2e/tsconfig.json`, `e2e/.prettierrc`, `e2e/.prettierignore`, `e2e/paths.ts`, `e2e/playwright.config.ts`, `e2e/global-setup.ts`, `e2e/fixtures.ts`, `e2e/specs/auth.setup.ts`, `e2e/specs/smoke.spec.ts`

**Interfaces:**
- Consumes:
  - the CLI login line (Task 5);
  - the UI roles and texts (Task 4);
  - the cookie name `researchly_token` and the 401 status (Task 1);
  - `RESEARCHLY_DATA_DIR`, `RESEARCHLY_LOG_DIR` and `PYTHON_KEYRING_BACKEND` (step 1c state/keychain).
- Produces `pnpm -C e2e exec playwright test --grep @smoke`. It needs `pnpm -C frontend build` first.

- [ ] **Step 1: Project files.**

`e2e/package.json`:

```json
{
  "name": "researchly-e2e",
  "private": true,
  "type": "module",
  "packageManager": "pnpm@12.15.0",
  "devEngines": {
    "runtime": { "name": "node", "version": "24.22.2", "onFail": "download" }
  },
  "scripts": {
    "test": "playwright test",
    "typecheck": "tsc --noEmit"
  },
  "devDependencies": {
    "@axe-core/playwright": "4.14.0",
    "@playwright/test": "1.64.1",
    "@types/node": "^24",
    "prettier": "^3.9.10",
    "typescript": "~6.0.3"
  }
}
```

`e2e/pnpm-workspace.yaml`:

```yaml
packages: []

# Supply chain (spec §19.7)
minimumReleaseAge: 10080
trustPolicy: no-downgrade
```

`e2e/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "es2023",
    "module": "esnext",
    "moduleResolution": "bundler",
    "types": ["node"],
    "strict": true,
    "noEmit": true,
    "skipLibCheck": true
  },
  "include": ["*.ts", "specs/**/*.ts"]
}
```

`e2e/.prettierrc`:

```json
{ "endOfLine": "lf", "semi": false, "singleQuote": false, "trailingComma": "es5", "printWidth": 100 }
```

`e2e/.prettierignore`: `.artifacts/`, `test-results/`, `playwright-report/`, `pnpm-lock.yaml`.

`e2e/paths.ts`:

```ts
import { resolve } from "node:path"

export const repo = resolve(import.meta.dirname, "..")
export const artifacts = resolve(import.meta.dirname, ".artifacts") // gitignored
export const authFile = resolve(artifacts, "auth.json")
export const appLog = resolve(artifacts, "logs", "app.log")
```

`e2e/global-setup.ts`:

```ts
import { execFileSync } from "node:child_process"
import { existsSync, mkdirSync } from "node:fs"
import { resolve } from "node:path"

import { artifacts, repo } from "./paths"

// A fixed data dir reuses one pg0 instance across runs (warm start); the file keyring keeps the
// developer's real OS keychain out of it (spec §16.1)
const env = {
  ...process.env,
  RESEARCHLY_DATA_DIR: resolve(artifacts, "data"),
  RESEARCHLY_LOG_DIR: resolve(artifacts, "logs"),
  PYTHON_KEYRING_BACKEND: "keyring.backends.fail.Keyring",
}

function researchly(...args: string[]): string {
  return execFileSync("uv", ["run", "--directory", resolve(repo, "cli"), "researchly", ...args], {
    env,
    encoding: "utf-8",
    timeout: 300_000,
  })
}

export default function globalSetup() {
  if (!existsSync(resolve(repo, "backend/src/researchly/web/index.html"))) {
    throw new Error("the web UI is not built: run `pnpm -C frontend build` first")
  }
  mkdirSync(artifacts, { recursive: true })
  researchly("config", "set", "runtime", "native") // CI runners have Docker; E2E pins native mode
  const out = researchly("start")
  const login = out.match(/http:\/\/127\.0\.0\.1:\d+\/\?token=[0-9a-f]+/)?.[0]
  if (!login) {
    researchly("stop")
    throw new Error(`no login link in the output of researchly start:\n${out}`)
  }
  process.env.RESEARCHLY_E2E_LOGIN_URL = login
  process.env.RESEARCHLY_E2E_URL = new URL(login).origin
  return () => {
    researchly("stop")
  }
}
```

`e2e/fixtures.ts`:

```ts
import { test as base } from "@playwright/test"

// globalSetup sets the URL after the config is loaded, so baseURL comes from a fixture
export const test = base.extend({
  baseURL: async ({}, use) => {
    await use(process.env.RESEARCHLY_E2E_URL)
  },
})

export { expect } from "@playwright/test"
```

`e2e/playwright.config.ts`:

```ts
import { defineConfig, devices } from "@playwright/test"

import { authFile } from "./paths"

export default defineConfig({
  testDir: "./specs",
  outputDir: "./test-results",
  globalSetup: "./global-setup.ts",
  forbidOnly: !!process.env.CI,
  workers: 1, // one shared app instance
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: { trace: "retain-on-failure" },
  projects: [
    { name: "setup", testMatch: /.*\.setup\.ts/ },
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], storageState: authFile },
      dependencies: ["setup"],
    },
  ],
})
```

`e2e/specs/auth.setup.ts`:

```ts
import { expect, test as setup } from "@playwright/test"

import { authFile } from "../paths"

setup("sign in through the login link", { tag: "@smoke" }, async ({ page }) => {
  const login = new URL(process.env.RESEARCHLY_E2E_LOGIN_URL!)
  await page.goto(login.href)
  await expect(page).toHaveURL(`${login.origin}/`) // the token is stripped from the address bar
  const cookie = (await page.context().cookies()).find((c) => c.name === "researchly_token")
  expect(cookie).toMatchObject({
    value: login.searchParams.get("token"),
    httpOnly: true,
    sameSite: "Strict",
  })
  await page.context().storageState({ path: authFile })
})
```

`e2e/specs/smoke.spec.ts`:

```ts
import { readFileSync } from "node:fs"

import AxeBuilder from "@axe-core/playwright"

import { expect, test } from "../fixtures"
import { appLog } from "../paths"

test.describe("shell", { tag: "@smoke" }, () => {
  test("renders, reports health and reads the API through the cookie", async ({ page }) => {
    const errors: string[] = []
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(message.text())
    })
    await page.goto("/")
    await expect(page.getByRole("heading", { level: 1, name: "Projects" })).toBeVisible()
    await expect(page.getByRole("status")).toHaveText("Connected")
    await expect(page.getByText(/^v\d+\.\d+\.\d+/)).toBeVisible() // GET /api/v1/system with the cookie
    await expect(page.getByRole("navigation", { name: "Main" })).toMatchAriaSnapshot(`
      - navigation "Main":
        - list:
          - listitem:
            - link "Projects"
          - listitem:
            - link "Memory"
          - listitem:
            - link "Usage"
          - listitem:
            - link "Settings"
    `)
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
    expect(errors).toEqual([]) // includes CSP violations
  })

  test("deep links load through the SPA fallback", async ({ page }) => {
    await page.goto("/p/demo/library")
    await expect(page.getByRole("heading", { level: 1, name: "Library" })).toBeVisible()
    await page.getByRole("link", { name: "Memory" }).click()
    await expect(page).toHaveURL(/\/memory$/)
  })

  test("a browser without the cookie is told how to sign in", async ({ browser, baseURL }) => {
    const context = await browser.newContext({ baseURL, storageState: { cookies: [], origins: [] } })
    const page = await context.newPage()
    await page.goto("/")
    await expect(page.getByRole("alert")).toContainText("researchly start")
    await context.close()
  })

  test("the API rejects requests without the token", async ({ playwright, baseURL }) => {
    const api = await playwright.request.newContext({ baseURL })
    expect((await api.get("/api/v1/system")).status()).toBe(401)
    await api.dispose()
  })

  test("the login token never reaches the app log", async () => {
    const token = new URL(process.env.RESEARCHLY_E2E_LOGIN_URL!).searchParams.get("token")!
    const log = readFileSync(appLog, "utf-8")
    expect(log).toContain("token=[REDACTED]")
    expect(log).not.toContain(token)
  })
})
```

- [ ] **Step 2: Install and run.**

```bash
pnpm -C e2e install
pnpm -C e2e exec playwright install --only-shell chromium   # add --with-deps only on CI/Linux
pnpm -C frontend build
pnpm -C e2e exec playwright test --grep @smoke
pnpm -C e2e typecheck && pnpm -C e2e exec prettier --check .
```

Expected: 1 setup + 5 smoke tests pass, and `researchly stop` runs at teardown (no app or pg0 left running).
- Running it once with an empty `frontend/` build must fail fast with the "not built" message.
- If axe reports violations, fix the UI (Task 4 files), not the test. Report each fix.

- [ ] **Step 3: Commit** `test(e2e): Playwright smoke for the shell, cookie auth, a11y and log redaction`. Commit `pnpm-lock.yaml`.

---

### Task 8 (controller): CI jobs, docs, ADR 0003

**Files:**
- Modify: `.github/workflows/ci.yml`, `AGENTS.md`, `frontend/AGENTS.md`, spec §4.3, `docs/progress.md`
- Create: `docs/adr/0003-spa-auth-cookie.md`

- [ ] **Step 1: CI.** Add two jobs. Both use the existing checkout/setup-uv pins, plus:
  - `pnpm/action-setup@ea17c68df8912ef543352723c149a84f56e3d413 # v6.1.0` with `package_json_file: frontend/package.json`;
  - `actions/setup-node@949feb2413d6458794dcd2491c4babbbce0c15c1 # v7.1.0` with `node-version-file: frontend/package.json`, `cache: pnpm`, and `cache-dependency-path` covering the relevant lockfiles.

`frontend` (ubuntu-24.04, 15 min) runs:
- `pnpm -C frontend install --frozen-lockfile`;
- `lint`, `exec prettier --check .`, `typecheck`, `test`;
- `api:schema` then `git diff --exit-code -- frontend/src/api` (step name "OpenAPI client is current");
- `build`.

`e2e` (ubuntu-24.04, 25 min) runs:
- frontend install + build;
- `uv sync --locked --directory cli`;
- `pnpm -C e2e install --frozen-lockfile`;
- `pnpm -C e2e exec playwright install --with-deps --only-shell chromium`;
- `pnpm -C e2e exec playwright test --grep @smoke`;
- on failure, `actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9 # v7.0.2` uploads `e2e/playwright-report`, `e2e/test-results` and `e2e/.artifacts/logs` (`if: failure()`).
- [ ] **Step 2: `AGENTS.md`.**
  - Frontend types/lint: `pnpm -C frontend typecheck` · `pnpm -C frontend lint`.
  - OpenAPI client: `pnpm -C frontend api:schema`.
  - E2E: `pnpm -C frontend build` then `pnpm -C e2e exec playwright test --grep @smoke`.
  - Repo map: `e2e/` is its own pnpm project.
  - Gotchas:
    - pnpm 12 provides Node 24.22.2 via `devEngines.runtime`;
    - TS stays on 6.0.3 until openapi-typescript supports TS 7;
    - the SPA builds into the gitignored `backend/src/researchly/web/`;
    - frontend dev: `researchly start`, open the login link once, then `pnpm -C frontend dev` on :5173.
- [ ] **Step 3: `frontend/AGENTS.md`.** Add:
  - auth is the HttpOnly cookie (no token code in the SPA);
  - API calls go through `@/api/client` + `queryOptions` in `@/api/queries`;
  - regenerate types with `api:schema`;
  - UI strings in `src/locales/en.json`;
  - the shadcn components use Base UI's `render` prop, not `asChild`;
  - `src/routeTree.gen.ts` is generated and committed.
- [ ] **Step 4: ADR 0003** "SPA authentication: one-time login link → HttpOnly cookie". Base it on research §2.2–§2.4: the options table, the rejected alternatives, the port-isolation risk and the Fetch Metadata mitigation. Amend spec §4.3 to "required in a header, or in an HttpOnly SameSite=Strict cookie obtained through the one-time login link printed by `researchly start` (ADR 0003); token failures answer 401".
- [ ] **Step 5: `docs/progress.md`.** Add the step 1d entry. No `features.json` flips: F10 and the onboarding features flip at step 9.
- [ ] **Step 6: Verify and commit.** Repo checks plus hook tests pass. Commit `ci: frontend and e2e jobs` and `docs: ADR 0003 SPA auth cookie and step 1d outcome`.

---

## Deferred (recorded, not built here)
- **Windows/macOS E2E legs:** non-gating, added at step 9 (native parity).
- **Docker-mode E2E leg:** when CI time allows. Task 6 already asserts `/` serves the SPA in the Docker E2E pytest.
- **WebSocket 1008 / `/health/` regression test:** with the first WebSocket route (step 3). Also verify the cookie works on the WebSocket handshake in Chromium, Firefox and WebKit (`Sec-Fetch-Site` sent?), per ADR 0003.
- **LLM fixtures directory in the E2E harness:** with the first LLM-backed flow (step 3).
- **Auto-open the browser on `start`:** Jupyter's 0600 redirect-file trick (research §2.4), step 9 onboarding.
- **TypeScript 7 / Node 26:** ADR when openapi-typescript supports TS 7. Re-check Node 26 LTS around 2026-12.
