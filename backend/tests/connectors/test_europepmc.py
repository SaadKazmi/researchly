import json
from urllib.parse import parse_qs

import httpx
import pytest

from researchly.connectors import europepmc as ep
from researchly.connectors.base import ExternalId, SearchQuery, SourceUnavailable
from researchly.connectors.europepmc import EuropePmcConnector, parse_result
from researchly.connectors.http import PoliteClient

pytestmark = pytest.mark.anyio

RESULT = {
    "id": "26017442",
    "source": "MED",
    "pmid": "26017442",
    "pmcid": "PMC9999",
    "doi": "10.1038/NATURE14539",
    "title": "Deep <i>learning</i>.",
    "abstractText": "<h4>Background</h4> Deep  learning &amp; <sub>x</sub> methods.",
    "pubYear": "2015",
    "authorList": {
        "author": [
            {"fullName": "LeCun Y", "authorId": {"type": "ORCID", "value": "0000-0001-2345-678X"}},
            {"fullName": "Bengio Y", "authorId": {"type": "SCOPUS", "value": "123"}},
            {"collectiveName": "The Consortium"},
            {"firstName": "no name"},
        ]
    },
    "journalInfo": {"journal": {"title": "Nature"}},
    "citedByCount": 19318,
    "isOpenAccess": "Y",
    "license": "cc by",
    "pubTypeList": {"pubType": ["Journal Article", "Retracted Publication"]},
}


def wrap(*results: dict) -> str:
    return json.dumps({"hitCount": len(results), "resultList": {"result": list(results)}})


def client_for(handler) -> PoliteClient:
    return PoliteClient(transport=httpx.MockTransport(handler), limits={"default": (0.0, 1)})


def test_parse_result():
    rec = parse_result(RESULT, "europepmc")
    assert rec is not None
    assert rec.title == "Deep learning"
    assert rec.abstract == "Background Deep learning & x methods."
    assert rec.year == 2015 and rec.venue == "Nature" and rec.cited_by == 19318
    assert rec.ids == [
        ExternalId("pmid", "26017442"),
        ExternalId("pmcid", "PMC9999"),
        ExternalId("doi", "10.1038/nature14539"),
    ]
    assert [(a.name, a.orcid) for a in rec.authors] == [
        ("LeCun Y", "0000-0001-2345-678X"),
        ("Bengio Y", None),
        ("The Consortium", None),
    ]
    assert rec.is_oa is True and rec.license == "cc by" and rec.is_retracted is True


def test_sparse_result_and_no_ids():
    rec = parse_result({"pmid": "5", "title": "T", "isOpenAccess": "N", "pubTypeList": {"pubType": ["Review"]}}, "e")
    assert rec is not None
    assert rec.is_oa is False and rec.is_retracted is False and rec.year is None and rec.abstract is None
    bare = parse_result({"title": "T", "pmid": "7"}, "e")
    assert bare is not None and bare.is_oa is None and bare.is_retracted is None and bare.cited_by is None
    assert parse_result({"title": "no ids", "id": "x", "source": "PPR"}, "e") is None


async def test_search_params_and_year_range():
    seen: list[dict[str, list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(parse_qs(request.url.query.decode()))
        return httpx.Response(200, text=wrap(RESULT))

    async with client_for(handler) as client:
        out = await EuropePmcConnector(client).search(SearchQuery("graph nets", limit=3, year_from=2015, year_to=2017))
    assert len(out) == 1
    assert seen[0] == {
        "query": ["graph nets AND PUB_YEAR:[2015 TO 2017]"],
        "format": ["json"],
        "resultType": ["core"],
        "pageSize": ["3"],
    }


async def test_search_open_year_range_and_empty_text():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(parse_qs(request.url.query.decode())["query"][0])
        return httpx.Response(200, text=wrap())

    async with client_for(handler) as client:
        conn = EuropePmcConnector(client)
        assert await conn.search(SearchQuery("  ")) == []
        await conn.search(SearchQuery("x", year_from=2020))
        await conn.search(SearchQuery("y", year_to=2001))
    assert seen == ["x AND PUB_YEAR:[2020 TO 3000]", "y AND PUB_YEAR:[1000 TO 2001]"]


async def test_get_builds_one_escaped_or_query_per_25():
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        queries.append(parse_qs(request.url.query.decode())["query"][0])
        return httpx.Response(200, text=wrap(RESULT))

    ids_ = [
        ExternalId("doi", "10.1038/NATURE14539"),
        ExternalId("doi", '10.1234/a"b\\c'),
        ExternalId("pmid", "pmid:0026017442"),
        ExternalId("pmcid", "pmc99"),
        ExternalId("pmid", "1 OR 2"),  # fails validation: dropped, never interpolated
        ExternalId("arxiv", "1706.03762"),  # unsupported scheme
        ExternalId("doi", "10.1038/nature14539"),  # duplicate
    ]
    async with client_for(handler) as client:
        conn = EuropePmcConnector(client)
        assert len(await conn.get(ids_)) == 1
        assert await conn.get([ExternalId("arxiv", "1706.03762"), ExternalId("pmid", "x")]) == []
    assert queries == [
        'DOI:"10.1038/nature14539" OR DOI:"10.1234/a\\"b\\\\c" OR (EXT_ID:26017442 AND SRC:MED) OR PMCID:PMC99'
    ]


async def test_get_chunks_of_25():
    sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sizes.append(parse_qs(request.url.query.decode())["query"][0].count(" OR ") + 1)
        return httpx.Response(200, text=wrap())

    ids_ = [ExternalId("pmid", str(i)) for i in range(1, 61)]
    async with client_for(handler) as client:
        assert await EuropePmcConnector(client).get(ids_) == []
    assert sizes == [25, 25, 10]


async def test_citations_paths_and_med_filter():
    paths: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append((request.url.path, request.url.query.decode()))
        if request.url.path.endswith("/citations"):
            body = {"citationList": {"citation": [{"source": "MED", "id": "11"}, {"source": "PPR", "id": "PPR1"}]}}
        else:
            body = {"referenceList": {"reference": [{"source": "MED", "id": "22"}, {"source": "AGR", "id": "3"}, {}]}}
        return httpx.Response(200, text=json.dumps(body))

    async with client_for(handler) as client:
        conn = EuropePmcConnector(client)
        assert await conn.citations(ExternalId("pmid", "26017442"), "in") == [ExternalId("pmid", "11")]
        assert await conn.citations(ExternalId("pmid", "26017442"), "out") == [ExternalId("pmid", "22")]
        assert await conn.citations(ExternalId("doi", "10.1038/nature14539"), "in") == []
        assert await conn.citations(ExternalId("pmid", "../x"), "out") == []
    assert paths == [
        ("/europepmc/webservices/rest/MED/26017442/citations", "format=json&pageSize=1000"),
        ("/europepmc/webservices/rest/MED/26017442/references", "format=json&pageSize=1000"),
    ]


@pytest.mark.parametrize(
    "body",
    ["<html>portal</html>", '{"resultList": ', "[]", '{"resultList": []}', '{"resultList": {"result": [1]}}'],
)
async def test_unreadable_bodies_are_source_unavailable(body):
    async with client_for(lambda r: httpx.Response(200, text=body)) as client:
        conn = EuropePmcConnector(client)
        with pytest.raises(SourceUnavailable, match="unreadable response"):
            await conn.search(SearchQuery("x"))
        with pytest.raises(SourceUnavailable, match="unreadable response"):
            await conn.get([ExternalId("pmid", "5")])


async def test_unreadable_citation_bodies():
    for body in ("<html/>", "[]", '{"citationList": {"citation": 5}}', '{"citationList": {"citation": [5]}}'):
        async with client_for(lambda r, b=body: httpx.Response(200, text=b)) as client:
            with pytest.raises(SourceUnavailable, match="unreadable response"):
                await EuropePmcConnector(client).citations(ExternalId("pmid", "5"), "in")


async def test_no_hits_and_errors():
    async with client_for(lambda r: httpx.Response(200, text='{"hitCount": 0}')) as client:  # malformed query: 200
        assert await EuropePmcConnector(client).search(SearchQuery("((")) == []
        assert await EuropePmcConnector(client).citations(ExternalId("pmid", "5"), "in") == []
    async with client_for(lambda r: httpx.Response(404, text="nope")) as client:
        assert await EuropePmcConnector(client).get([ExternalId("pmid", "5")]) == []
        assert await EuropePmcConnector(client).citations(ExternalId("pmid", "5"), "out") == []
    async with client_for(lambda r: httpx.Response(400, text="bad")) as client:
        with pytest.raises(SourceUnavailable, match="HTTP 400"):
            await EuropePmcConnector(client).search(SearchQuery("x"))
        with pytest.raises(SourceUnavailable, match="HTTP 400"):
            await EuropePmcConnector(client).citations(ExternalId("pmid", "5"), "in")


async def test_capabilities():
    async with client_for(lambda r: httpx.Response(500)) as client:
        caps = EuropePmcConnector(client).capabilities()
    assert (caps.search, caps.get, caps.citations_in, caps.citations_out) == (True, True, True, True)


def test_cache_keys_are_versioned():
    assert ep.KEY == "europepmc:v1"


async def test_cache_keys_and_ttls():
    from researchly.connectors.http import TTL_COUNTS, TTL_METADATA, TTL_SEARCH

    calls: list[tuple[str | None, object]] = []

    class Fake(PoliteClient):
        async def get(self, url, **kw):  # type: ignore[override]
            calls.append((kw["cache_key"], kw["ttl"]))
            from researchly.connectors.http import Fetched

            return Fetched(200, {}, b"{}", url)

    conn = EuropePmcConnector(Fake())
    await conn.search(SearchQuery("x"))
    await conn.get([ExternalId("pmid", "5")])
    await conn.get([ExternalId("pmid", "5"), ExternalId("pmid", "6")])
    await conn.citations(ExternalId("pmid", "5"), "in")
    await conn.citations(ExternalId("pmid", "5"), "out")
    keys = [k for k, _ in calls]
    assert keys[0] and keys[0].startswith("europepmc:v1:search:") and calls[0][1] == TTL_SEARCH
    assert calls[1] == ("europepmc:v1:pmid:5", TTL_METADATA)
    assert keys[2] and keys[2].startswith("europepmc:v1:get:") and calls[2][1] == TTL_METADATA
    assert calls[3] == ("europepmc:v1:count:in:5", TTL_COUNTS)
    assert calls[4] == ("europepmc:v1:refs:pmid:5", TTL_METADATA)


# ---- cassettes (recorded live, keyless) ----------------------------------------------------------------------------


@pytest.mark.vcr
async def test_search_attention(polite):
    recs = await EuropePmcConnector(polite).search(SearchQuery("attention is all you need", limit=3))
    assert len(recs) == 3
    assert all(r.title and r.ids and r.id("doi") and r.source == "europepmc" for r in recs)  # PPR rows have no PMID
    assert [r.year for r in recs] == [2026, 2026, 2023] and recs[0].abstract


@pytest.mark.vcr
async def test_get_by_pmid_and_doi(polite):
    [by_pmid] = await EuropePmcConnector(polite).get([ExternalId("pmid", "26017442")])
    [by_doi] = await EuropePmcConnector(polite).get([ExternalId("doi", "10.1038/nature14539")])
    for rec in (by_pmid, by_doi):
        assert rec.title == "Deep learning" and rec.year == 2015 and rec.venue == "Nature"
        assert rec.id("pmid") == "26017442" and rec.id("doi") == "10.1038/nature14539"
        assert rec.authors and rec.authors[0].name.startswith("LeCun") and rec.cited_by and rec.cited_by > 10000
        assert rec.abstract and "<" not in rec.abstract and rec.is_retracted is False


@pytest.mark.vcr
async def test_references_out(polite):
    refs = await EuropePmcConnector(polite).citations(ExternalId("pmid", "26017442"), "out")
    assert ExternalId("pmid", "25521294") in refs and all(r.scheme == "pmid" for r in refs)


@pytest.mark.vcr
async def test_citers_in(polite):
    citers = await EuropePmcConnector(polite).citations(ExternalId("pmid", "23456789"), "in")
    assert ExternalId("pmid", "42333381") in citers and 0 < len(citers) <= 50  # hitCount 50, MED rows only


@pytest.mark.vcr
async def test_not_found(polite):
    assert await EuropePmcConnector(polite).get([ExternalId("pmid", "999999999")]) == []
