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
        revalidated = await c.get("/assets/index-abc123.js", headers={"if-none-match": asset.headers["etag"]})
    assert asset.status_code == 200 and "immutable" in asset.headers["cache-control"]
    assert revalidated.status_code == 304 and "immutable" in revalidated.headers["cache-control"]
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
        hint = await c.get("/")  # where the login link lands: say why there is no UI
        assert hint.status_code == 200 and "pnpm -C frontend build" in hint.text
        assert (await c.get("/health")).status_code == 200


def test_openapi_module_writes_the_schema(tmp_path):
    out = tmp_path / "openapi.json"
    openapi.main([str(out)])
    raw = out.read_bytes()
    assert raw.endswith(b"}\n") and b"\r\n" not in raw
    assert {"/health", "/api/v1/system"} <= set(json.loads(raw)["paths"])
