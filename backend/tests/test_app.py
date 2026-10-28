import httpx
import pytest

from researchly.app import create_app
from researchly.config import Settings


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
