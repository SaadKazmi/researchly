import httpx
import pytest

from researchly.connectors import hf
from researchly.connectors.base import ExternalId, SearchQuery, SourceUnavailable
from researchly.connectors.hf import HfConnector
from researchly.connectors.http import TTL_METADATA, TTL_SEARCH, PoliteClient

pytestmark = pytest.mark.anyio

PAPER = {
    "id": "1706.03762",
    "title": "Attention Is All You Need \U0001f917",
    "summary": "The dominant sequence models",
    "publishedAt": "2017-06-12T17:57:34.000Z",
    "authors": [{"name": "Ashish Vaswani"}, {"name": ""}, {"hidden": True}],
    "upvotes": 0,
    "githubRepo": "https://github.com/x/y",
    "githubStars": None,
    "ai_keywords": ["transformer"],
}


def client_for(handler) -> PoliteClient:
    return PoliteClient(transport=httpx.MockTransport(handler), limits={"default": (0.0, 1)})


def test_parse_paper():
    rec = hf.parse_paper(PAPER)
    assert rec is not None and rec.source == "hf"
    assert rec.ids == [ExternalId("arxiv", "1706.03762"), ExternalId("doi", "10.48550/arxiv.1706.03762")]
    assert rec.title.startswith("Attention") and rec.year == 2017
    assert rec.abstract == "The dominant sequence models"
    assert [a.name for a in rec.authors] == ["Ashish Vaswani"]
    assert rec.extra == {"upvotes": 0, "githubRepo": "https://github.com/x/y", "ai_keywords": ["transformer"]}


def test_parse_paper_without_arxiv_id_is_skipped():
    assert hf.parse_paper({"id": "not-an-id", "title": "x"}) is None
    assert hf.parse_paper({"title": "x"}) is None


def test_parse_paper_tolerates_sparse_payload():
    rec = hf.parse_paper({"id": "2401.00001", "title": None, "publishedAt": "garbage", "authors": None})
    assert rec is not None and rec.title == "" and rec.year is None and rec.authors == [] and rec.extra == {}


async def test_emoji_survives_utf8_decoding():
    body = '{"id":"1706.03762","title":"Hug \U0001f917 face"}'.encode()
    c = HfConnector(client_for(lambda r: httpx.Response(200, content=body)))
    [rec] = await c.get([ExternalId("arxiv", "1706.03762")])
    assert rec.title == "Hug \U0001f917 face"


async def test_get_maps_arxiv_doi_dedupes_and_skips_other_schemes():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json=PAPER)

    c = HfConnector(client_for(handler))
    recs = await c.get(
        [
            ExternalId("arxiv", "arXiv:1706.03762v7"),
            ExternalId("doi", "10.48550/ARXIV.1706.03762"),
            ExternalId("doi", "10.1038/nature14539"),
            ExternalId("pmid", "26017442"),
        ]
    )
    assert seen == ["/api/papers/1706.03762"] and len(recs) == 1


async def test_get_old_style_id_keeps_its_slash():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path.decode())
        return httpx.Response(200, json={"id": "hep-th/9901001", "title": "x"})

    [rec] = await HfConnector(client_for(handler)).get([ExternalId("arxiv", "hep-th/9901001")])
    assert seen == ["/api/papers/hep-th/9901001"]
    assert rec.id("arxiv") == "hep-th/9901001"


async def test_get_nothing_usable_makes_no_request():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    c = HfConnector(client_for(handler))
    assert await c.get([ExternalId("pmid", "1"), ExternalId("doi", "10.1234/a#b")]) == []
    assert await c.get([]) == []


async def test_get_404_skips_that_id():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("1706.03762"):
            return httpx.Response(200, json=PAPER)
        return httpx.Response(404, json={"error": "Paper not found"})

    recs = await HfConnector(client_for(handler)).get(
        [ExternalId("arxiv", "2401.00001"), ExternalId("arxiv", "1706.03762")]
    )
    assert [r.id("arxiv") for r in recs] == ["1706.03762"]


@pytest.mark.parametrize("response", [httpx.Response(200, text="<html>portal</html>"), httpx.Response(200, json=[1])])
async def test_get_unreadable_body_is_source_unavailable(response):
    with pytest.raises(SourceUnavailable, match="unreadable response"):
        await HfConnector(client_for(lambda r: response)).get([ExternalId("arxiv", "1706.03762")])


async def test_non_2xx_raises():
    c = HfConnector(client_for(lambda r: httpx.Response(403)))
    with pytest.raises(SourceUnavailable, match="HTTP 403"):
        await c.get([ExternalId("arxiv", "1706.03762")])


async def test_search_skips_rows_without_arxiv_id_and_uses_search_bucket():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[{"paper": PAPER}, {"paper": {"id": "x"}}, {"nopaper": 1}])

    c = HfConnector(client_for(handler))
    recs = await c.search(SearchQuery("graph neural network", limit=2))
    assert [r.id("arxiv") for r in recs] == ["1706.03762"]
    assert seen[0].url.params["q"] == "graph neural network" and seen[0].url.params["limit"] == "2"
    real = PoliteClient()  # the shipped limits.yaml gives the search pool its own, slower bucket
    assert real.limit_for(str(seen[0].url), hf.SEARCH_BUCKET) is not real.limit_for(str(seen[0].url), None)


@pytest.mark.parametrize("response", [httpx.Response(200, json={"a": 1}), httpx.Response(200, json=[1, 2])])
async def test_search_unreadable_body(response):
    with pytest.raises(SourceUnavailable, match="unreadable response"):
        await HfConnector(client_for(lambda r: response)).search(SearchQuery("x"))


async def test_search_non_2xx_raises():
    with pytest.raises(SourceUnavailable, match="HTTP 403"):
        await HfConnector(client_for(lambda r: httpx.Response(403))).search(SearchQuery("x"))


async def test_cache_keys_and_ttls(monkeypatch):
    calls: list[tuple[str, object]] = []
    real = PoliteClient.get

    async def spy(self, url, **kw):
        calls.append((kw["cache_key"], kw["ttl"]))
        return await real(self, url, **kw)

    monkeypatch.setattr(PoliteClient, "get", spy)
    c = HfConnector(
        client_for(lambda r: httpx.Response(200, json=[{"paper": PAPER}] if "search" in r.url.path else PAPER))
    )
    await c.search(SearchQuery("x"))
    await c.get([ExternalId("arxiv", "1706.03762")])
    assert calls[0][0].startswith("hf:v1:search:") and calls[0][1] == TTL_SEARCH
    assert calls[1] == ("hf:v1:paper:1706.03762", TTL_METADATA)


async def test_citations_none_without_request():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    c = HfConnector(client_for(handler))
    assert await c.citations(ExternalId("arxiv", "1706.03762"), "in") == []
    assert await c.citations(ExternalId("arxiv", "1706.03762"), "out") == []
    caps = c.capabilities()
    assert (caps.search, caps.get, caps.citations_in, caps.citations_out) == (True, True, False, False)


@pytest.mark.vcr
async def test_search_graph_neural_network(polite):
    recs = await HfConnector(polite).search(SearchQuery("graph neural network", limit=3))
    assert 1 <= len(recs) <= 3
    assert all(r.id("arxiv") and r.title and r.year for r in recs)


@pytest.mark.vcr
async def test_get_by_arxiv_id(polite):
    [rec] = await HfConnector(polite).get([ExternalId("arxiv", "1706.03762")])
    assert "Attention" in rec.title and rec.year == 2017
    assert rec.ids == [ExternalId("arxiv", "1706.03762"), ExternalId("doi", "10.48550/arxiv.1706.03762")]
    assert rec.authors and "Vaswani" in rec.authors[0].name


@pytest.mark.vcr
async def test_not_found(polite):
    assert await HfConnector(polite).get([ExternalId("arxiv", "1001.0001")]) == []
