"""Nightly: the shipped SearXNG settings reach real engines. Run: RESEARCHLY_SEARXNG_URL=... pytest -m nightly."""

import os

import pytest

from researchly.connectors.sidecar import SidecarClient
from researchly.services.web_search import web_search

pytestmark = [pytest.mark.nightly, pytest.mark.anyio]


@pytest.fixture
async def client():
    url = os.environ.get("RESEARCHLY_SEARXNG_URL")
    if not url:
        pytest.skip("set RESEARCHLY_SEARXNG_URL to a SearXNG running infra/searxng/settings.yml")
    sidecar = SidecarClient(url, source="web")
    yield sidecar
    await sidecar.aclose()


async def test_general_search_reaches_the_general_engines(client):
    outcome = await web_search(client, "retrieval augmented generation survey")
    assert outcome.results, outcome.unresponsive
    engines = {e for r in outcome.results for e in r.engines}
    assert engines & {"brave", "mojeek", "yahoo"}, outcome.unresponsive
    assert not engines - {"brave", "mojeek", "yahoo", "github"}  # scholarly engines stay off


async def test_code_search_reaches_github(client):
    outcome = await web_search(client, "transformer implementation pytorch", category="code")
    assert any("github" in r.engines for r in outcome.results), outcome.unresponsive
