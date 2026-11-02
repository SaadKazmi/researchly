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
@pytest.mark.parametrize(
    ("path", "location"), [("//evil.example/x", "/evil.example/x"), ("/\\evil.example", "/%5Cevil.example")]
)
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
    logger = logging.getLogger("uvicorn.access")
    saved = logger.filters[:]
    logger.filters.clear()  # litellm also filters this logger once imported; test our own filter alone
    create_app(Settings(env="test"), token=TOKEN)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(AccessFormatter(use_colors=False))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        logger.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "GET", f"/?token={TOKEN}", "1.1", 303)
    finally:
        logger.removeHandler(handler)
        logger.filters[:] = saved
    assert TOKEN not in stream.getvalue()
    assert "/?token=[REDACTED]" in stream.getvalue()
