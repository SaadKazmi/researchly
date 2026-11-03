from urllib.parse import parse_qs

import httpx
import pytest

from researchly.connectors import crossref as cr
from researchly.connectors.base import ExternalId, SearchQuery, SourceUnavailable
from researchly.connectors.crossref import CrossrefConnector
from researchly.connectors.http import PoliteClient

pytestmark = pytest.mark.anyio

ITEM = {
    "DOI": "10.1038/NATURE14539",
    "title": ["Deep learning"],
    "abstract": "<jats:title>Abstract</jats:title>\n<jats:p>Deep   learning <jats:i>allows</jats:i> models</jats:p>",
    "issued": {"date-parts": [[2015, 5, 27]]},
    "author": [
        {"given": "Yann", "family": "LeCun", "ORCID": "http://orcid.org/0000-0002-1825-009X"},
        {"name": "The Consortium"},
        {"given": "Only"},
        {},
    ],
    "container-title": ["Nature"],
    "type": "journal-article",
    "is-referenced-by-count": 79000,
    "license": [{"URL": "https://www.springer.com/tdm"}, {"URL": "https://other"}],
    "updated-by": [{"type": "correction"}, {"type": "retraction"}],
    "reference": [{"key": "a", "DOI": "10.1000/ABC"}, {"key": "b", "unstructured": "no doi"}, {"DOI": "bad"}],
}


def client_for(handler) -> PoliteClient:
    return PoliteClient(transport=httpx.MockTransport(handler), limits={"default": (0.0, 1)})


def test_parse_item():
    rec = cr.parse_item(ITEM)
    assert rec.source == "crossref" and rec.ids == [ExternalId("doi", "10.1038/nature14539")]
    assert rec.title == "Deep learning" and rec.year == 2015 and rec.venue == "Nature"
    assert rec.venue_type == "journal-article" and rec.cited_by == 79000
    assert rec.license == "https://www.springer.com/tdm" and rec.is_retracted is True
    assert [(a.name, a.orcid) for a in rec.authors] == [
        ("Yann LeCun", "0000-0002-1825-009X"),
        ("The Consortium", None),
        ("Only", None),
    ]
    assert rec.references == [ExternalId("doi", "10.1000/abc")]


def test_jats_is_stripped_to_plain_text():
    assert cr.parse_item(ITEM).abstract == "Abstract Deep learning allows models"
    assert cr.strip_jats(None) is None and cr.strip_jats("<jats:p> </jats:p>") is None


def test_retraction_detected_only_from_updated_by():
    base = {"DOI": "10.1/x", "title": ["t"]}
    assert cr.parse_item(base | {"updated-by": [{"type": "retraction"}]}).is_retracted is True
    assert cr.parse_item(base | {"updated-by": [{"type": "correction"}]}).is_retracted is False
    assert cr.parse_item(base).is_retracted is False


def test_parse_item_tolerates_sparse_payload():
    rec = cr.parse_item({"DOI": "10.1/x", "title": None, "issued": {"date-parts": [[None]]}})
    assert rec.title == "" and rec.year is None and rec.authors == [] and rec.venue is None


async def test_not_found_text_plain_returns_empty():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="", headers={"content-type": "text/plain"})

    async with client_for(handler) as client:
        conn = CrossrefConnector(client)
        assert await conn.get([ExternalId("doi", "10.1234/none")]) == []
        assert await conn.citations(ExternalId("doi", "10.1234/none"), "out") == []


async def test_get_without_dois_makes_no_request():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    async with client_for(handler) as client:
        assert await CrossrefConnector(client).get([ExternalId("pmid", "5"), ExternalId("s2", "x")]) == []
        assert await CrossrefConnector(client).citations(ExternalId("pmid", "5"), "out") == []


async def test_arxiv_ids_are_looked_up_as_nothing():
    async with client_for(lambda r: httpx.Response(500)) as client:
        assert await CrossrefConnector(client).get([ExternalId("arxiv", "1706.03762")]) == []


async def test_other_errors_and_unreadable_bodies_are_source_unavailable():
    def bad(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"status": "failed"})

    def html(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>maintenance</html>")

    async with client_for(bad) as client:
        with pytest.raises(SourceUnavailable):
            await CrossrefConnector(client).get([ExternalId("doi", "10.1234/x")])
    async with client_for(html) as client:
        conn = CrossrefConnector(client)
        with pytest.raises(SourceUnavailable, match="unreadable response"):
            await conn.get([ExternalId("doi", "10.1234/x")])
        with pytest.raises(SourceUnavailable, match="unreadable response"):
            await conn.search(SearchQuery("x"))
    async with client_for(lambda r: httpx.Response(200, json={"message": "x"})) as client:
        with pytest.raises(SourceUnavailable, match="unreadable response"):
            await CrossrefConnector(client).citations(ExternalId("doi", "10.1234/x"), "out")


async def test_search_params_and_year_bounds():
    seen: list[dict[str, list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(parse_qs(request.url.query.decode()))
        return httpx.Response(200, json={"message": {"items": [ITEM]}})

    async with client_for(handler) as client:
        conn = CrossrefConnector(client)
        out = await conn.search(SearchQuery("graphs", limit=3, year_from=2015, year_to=2017))
        await conn.search(SearchQuery("graphs", year_from=2015))
        await conn.search(SearchQuery("graphs", year_to=2017))
        await conn.search(SearchQuery("graphs"))
    assert len(out) == 1
    assert seen[0]["query.bibliographic"] == ["graphs"] and seen[0]["rows"] == ["3"]
    assert seen[0]["select"] == [cr.SELECT]
    assert seen[0]["filter"] == ["from-pub-date:2015,until-pub-date:2017"]
    assert seen[1]["filter"] == ["from-pub-date:2015"] and seen[2]["filter"] == ["until-pub-date:2017"]
    assert "filter" not in seen[3]


async def test_search_uses_the_list_bucket():
    buckets: list[str | None] = []

    class Spy(PoliteClient):
        async def get(self, url, **kwargs):
            buckets.append(kwargs.get("bucket"))
            return await super().get(url, **kwargs)

    spy = Spy(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"message": {"items": []}})))
    async with spy:
        assert await CrossrefConnector(spy).search(SearchQuery("x")) == []
    assert buckets == ["api.crossref.org/list"]


async def test_doi_with_reserved_characters_is_percent_encoded_in_the_path():
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.raw_path.decode())
        return httpx.Response(200, json={"message": ITEM})

    async with client_for(handler) as client:
        conn = CrossrefConnector(client)
        await conn.get([ExternalId("doi", "10.1234/a#b")])
        await conn.get([ExternalId("doi", "10.1234/a?x")])
        await conn.get([ExternalId("doi", "10.1234/a%b")])
    assert "%23" in paths[0] and "%3F" in paths[1] and "%25" in paths[2]
    assert all(p.startswith("/works/10.1234/a") and "?" not in p for p in paths)


async def test_missing_dois_skip_and_the_rest_return():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/none"):
            return httpx.Response(404, text="")
        return httpx.Response(200, json={"message": ITEM})

    async with client_for(handler) as client:
        recs = await CrossrefConnector(client).get([ExternalId("doi", "10.1234/none"), ExternalId("doi", "10.1234/ok")])
    assert len(recs) == 1


async def test_references_out_reuses_the_singleton_request():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"message": ITEM})

    async with client_for(handler) as client:
        conn = CrossrefConnector(client)
        refs = await conn.citations(ExternalId("doi", "10.1038/nature14539"), "out")
        await conn.get([ExternalId("doi", "10.1038/nature14539")])
        assert await conn.citations(ExternalId("doi", "10.1038/nature14539"), "in") == []
    assert refs == [ExternalId("doi", "10.1000/abc")] and len(calls) == 2  # the "in" call made none


def test_cache_keys_are_versioned():
    assert cr.singleton_key("10.1234/a") == "crossref:v1:doi:10.1234/a"


def test_capabilities():
    caps = CrossrefConnector(PoliteClient()).capabilities()
    assert (caps.search, caps.get, caps.citations_in, caps.citations_out) == (True, True, False, True)


# ---- cassettes (recorded live, keyless) ----------------------------------------------------------------------------


@pytest.mark.vcr
async def test_search_attention(polite):
    recs = await CrossrefConnector(polite).search(SearchQuery("attention is all you need", limit=3))
    assert 0 < len(recs) <= 3 and all(r.id("doi") for r in recs)
    assert any("attention" in r.title.lower() for r in recs)


@pytest.mark.vcr
async def test_get_by_doi(polite):
    [rec] = await CrossrefConnector(polite).get([ExternalId("doi", "10.1038/nature14539")])
    assert rec.title == "Deep learning" and rec.year == 2015 and rec.venue == "Nature"
    assert rec.id("doi") == "10.1038/nature14539" and rec.venue_type == "journal-article"
    assert rec.authors and rec.authors[0].name == "Yann LeCun"
    assert rec.cited_by and rec.cited_by > 50_000 and rec.license and rec.is_retracted is False
    assert any(r.scheme == "doi" for r in rec.references)


@pytest.mark.vcr
async def test_references_out(polite):
    refs = await CrossrefConnector(polite).citations(ExternalId("doi", "10.1038/nature14539"), "out")
    assert len(refs) > 10 and all(r.scheme == "doi" for r in refs)


@pytest.mark.vcr
async def test_not_found(polite):
    conn = CrossrefConnector(polite)
    assert await conn.get([ExternalId("doi", "10.1038/doesnotexist0000")]) == []
