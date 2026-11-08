import asyncio
from pathlib import Path

import httpx
import pytest

from researchly.app import create_app, require_selector_loop
from researchly.config import Settings
from researchly.security.localhost_guard import TOKEN_HEADER
from researchly.security.redact import redact


@pytest.mark.anyio
async def test_health_ok():
    app = create_app(Settings(env="test"), token="t" * 43)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_settings_read_env(monkeypatch):
    monkeypatch.setenv("RESEARCHLY_ENV", "development")
    monkeypatch.setenv("RESEARCHLY_DEV_PROVIDER", "claude-code")
    s = Settings()
    assert (s.env, s.dev_provider, s.llm_fixtures) == ("development", "claude-code", "off")


@pytest.mark.anyio
async def test_env_token_is_used_without_the_keychain(monkeypatch):
    def no_keychain() -> str:
        raise AssertionError("keychain must not be read when RESEARCHLY_LOCAL_TOKEN is set")

    monkeypatch.setattr("researchly.app.ensure_local_token", no_keychain)
    monkeypatch.setenv("RESEARCHLY_LOCAL_TOKEN", "e" * 43)
    app = create_app(Settings(env="test"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        allowed = await client.get("/api/nope", headers={TOKEN_HEADER: "e" * 43})
        denied = await client.get("/api/nope")
    assert (allowed.status_code, denied.status_code) == (404, 401)
    assert "e" * 43 not in redact(f"token={'e' * 43}")


def test_data_dir_defaults_to_home_and_reads_env(monkeypatch, tmp_path):
    monkeypatch.delenv("RESEARCHLY_DATA_DIR", raising=False)
    assert Settings().data_dir == Path.home() / ".researchly"
    monkeypatch.setenv("RESEARCHLY_DATA_DIR", str(tmp_path))
    assert Settings().data_dir == tmp_path


def test_settings_read_the_searxng_url(monkeypatch):
    monkeypatch.setenv("RESEARCHLY_SEARXNG_URL", "http://127.0.0.1:8766")
    assert Settings().searxng_url == "http://127.0.0.1:8766"
    monkeypatch.delenv("RESEARCHLY_SEARXNG_URL")
    assert Settings().searxng_url is None


def test_a_proactor_loop_is_refused():
    proactor = getattr(asyncio, "ProactorEventLoop", None)
    if proactor is None:
        pytest.skip("Windows only")
    loop = proactor()
    try:
        with pytest.raises(RuntimeError, match="SelectorEventLoop"):
            require_selector_loop(loop)
    finally:
        loop.close()


def test_a_selector_loop_is_accepted():
    loop = asyncio.SelectorEventLoop()
    try:
        require_selector_loop(loop)
    finally:
        loop.close()


def test_the_docker_image_runs_uvicorn_on_a_selector_loop():
    dockerfile = Path(__file__).resolve().parents[2] / "infra" / "app.Dockerfile"
    assert '"--loop", "asyncio:SelectorEventLoop"' in dockerfile.read_text("utf-8")
