import httpx
import pytest

from researchly.app import create_app
from researchly.config import Settings
from researchly.security.localhost_guard import TOKEN_HEADER

TOKEN = "t" * 43


def client(base_url: str = "http://127.0.0.1:8765") -> httpx.AsyncClient:
    app = create_app(Settings(env="test"), token=TOKEN)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base_url)


@pytest.mark.anyio
async def test_health_open_without_token():
    async with client() as c:
        assert (await c.get("/health")).status_code == 200


@pytest.mark.anyio
@pytest.mark.parametrize("base_url", ["http://evil.example:8765", "http://192.168.1.5:8765"])
async def test_foreign_host_rejected(base_url):
    async with client(base_url) as c:
        assert (await c.get("/health")).status_code == 403


@pytest.mark.anyio
async def test_cross_origin_rejected_even_with_token():
    async with client() as c:
        r = await c.get("/openapi.json", headers={TOKEN_HEADER: TOKEN, "origin": "http://evil.example"})
        assert r.status_code == 403
        r = await c.get("/openapi.json", headers={TOKEN_HEADER: TOKEN, "origin": "null"})
        assert r.status_code == 403


@pytest.mark.anyio
async def test_token_required_and_checked():
    async with client() as c:
        assert (await c.get("/openapi.json")).status_code == 403
        assert (await c.get("/openapi.json", headers={TOKEN_HEADER: "wrong"})).status_code == 403
        ok = await c.get("/openapi.json", headers={TOKEN_HEADER: TOKEN, "origin": "http://127.0.0.1:8765"})
        assert ok.status_code == 200


@pytest.mark.anyio
async def test_localhost_and_ipv6_hosts_allowed():
    for base in ("http://localhost:8765", "http://[::1]:8765"):
        async with client(base) as c:
            assert (await c.get("/health")).status_code == 200
