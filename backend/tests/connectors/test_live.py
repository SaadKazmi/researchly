"""Nightly checks against the real keyless APIs (no email, default limits). Run: pytest -m nightly."""

import pytest

from researchly.connectors import build_connectors
from researchly.connectors.base import ExternalId, SearchQuery
from researchly.connectors.http import PoliteClient

pytestmark = [pytest.mark.nightly, pytest.mark.anyio]

GET_PROBES = {
    "openalex": ExternalId("openalex", "W2626778328"),
    "arxiv": ExternalId("arxiv", "1706.03762"),
    "crossref": ExternalId("doi", "10.1038/nature14539"),
    "datacite": ExternalId("doi", "10.48550/arxiv.1706.03762"),
    "europepmc": ExternalId("pmid", "26017442"),
    "pubmed": ExternalId("pmid", "26017442"),
    "hf": ExternalId("arxiv", "1706.03762"),
    "biorxiv": ExternalId("doi", "10.1101/2020.03.22.002386"),
}


@pytest.fixture
async def connectors():
    async with PoliteClient() as client:
        yield build_connectors(client)


async def test_probes_cover_every_get_capable_connector(connectors):
    assert {n for n, c in connectors.items() if c.capabilities().get} == set(GET_PROBES)


async def test_every_searchable_connector_finds_the_transformer_paper(connectors):
    for name, c in connectors.items():
        if c.capabilities().search:
            records = await c.search(SearchQuery("attention is all you need", limit=3))
            assert records, name
            assert all(r.title for r in records), name


@pytest.mark.parametrize("name", sorted(GET_PROBES))
async def test_get(connectors, name):
    assert len(await connectors[name].get([GET_PROBES[name]])) == 1


async def test_opencitations_citations(connectors):
    ids = await connectors["opencitations"].citations(ExternalId("doi", "10.1038/nature14539"), "out")
    assert len(ids) >= 10


async def test_openreview_decision_lookup(connectors):
    rec = await connectors["openreview"].by_title("Dataflow Is All You Need")  # pyright: ignore[reportAttributeAccessIssue]
    assert rec is not None and isinstance(
        rec.extra["openreview_decision"], str
    )  # the cassette paper: a miss is breakage
