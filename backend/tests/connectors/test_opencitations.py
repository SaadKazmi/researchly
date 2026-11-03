import httpx
import pytest

from researchly.connectors import opencitations as oc
from researchly.connectors.base import ExternalId, SearchQuery, SourceUnavailable, TooManyResults
from researchly.connectors.http import TTL_COUNTS, TTL_METADATA, PoliteClient
from researchly.connectors.opencitations import OpenCitationsConnector, parse_multi_id

pytestmark = pytest.mark.anyio

DOI = ExternalId("doi", "10.1038/nature14539")


def client_for(handler) -> PoliteClient:
    return PoliteClient(transport=httpx.MockTransport(handler), limits={"default": (0.0, 1)})


def count(n) -> httpx.Response:
    return httpx.Response(200, json=[{"count": str(n)}])


def test_parse_multi_id_prefers_doi_then_pmid_then_openalex():
    full = "omid:br/0650349701 pmid:26017442 doi:10.1038/NATURE14539 openalex:W2919115771"
    assert parse_multi_id(full) == ExternalId("doi", "10.1038/nature14539")
    assert parse_multi_id("omid:br/1 pmid:0026017442 openalex:W1") == ExternalId("pmid", "26017442")
    assert parse_multi_id("omid:br/1 openalex:w2919115771") == ExternalId("openalex", "W2919115771")
    assert parse_multi_id("omid:br/1 isbn:9780000000002") is None
    assert parse_multi_id("doi:bad pmid:x") is None
    assert parse_multi_id("") is None


def test_capabilities_and_cache_key_prefix():
    caps = OpenCitationsConnector(PoliteClient()).capabilities()
    assert (caps.search, caps.get, caps.citations_in, caps.citations_out) == (False, False, True, True)
    assert oc.KEY == "opencitations:v1"


async def test_search_and_get_make_no_request():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    async with client_for(handler) as client:
        conn = OpenCitationsConnector(client)
        assert await conn.search(SearchQuery("x")) == [] and await conn.get([DOI]) == []


async def test_other_schemes_make_no_request():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    async with client_for(handler) as client:
        conn = OpenCitationsConnector(client)
        assert await conn.citations(ExternalId("openalex", "W1"), "in") == []
        assert await conn.citations(ExternalId("arxiv", "1706.03762"), "out") == []
        assert await conn.citations(ExternalId("doi", "not a doi"), "in") == []


async def test_too_many_results_above_the_threshold_before_the_list_request():
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return count(oc.MAX_ROWS + 1)

    async with client_for(handler) as client:
        conn = OpenCitationsConnector(client)
        with pytest.raises(TooManyResults, match="5001 citations"):
            await conn.citations(DOI, "in")
        with pytest.raises(TooManyResults, match="5001 references"):
            await conn.citations(DOI, "out")
    assert paths == [
        "/index/v2/citation-count/doi:10.1038/nature14539",
        "/index/v2/reference-count/doi:10.1038/nature14539",
    ]


async def test_zero_count_skips_the_list_request():
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return count(0)

    async with client_for(handler) as client:
        assert await OpenCitationsConnector(client).citations(DOI, "in") == []
    assert len(paths) == 1


async def test_both_directions_parse_the_right_field_and_dedupe():
    rows = [
        {"citing": "omid:br/1 doi:10.1000/a", "cited": "omid:br/9 doi:10.1000/x"},
        {"citing": "omid:br/2 pmid:5", "cited": "omid:br/8 pmid:7 doi:10.1000/y"},
        {"citing": "omid:br/3 doi:10.1000/A", "cited": "omid:br/7 isbn:1"},  # a duplicate and an unusable row
        {"citing": "omid:br/4", "cited": ""},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return count(len(rows)) if "count" in request.url.path else httpx.Response(200, json=rows)

    async with client_for(handler) as client:
        conn = OpenCitationsConnector(client)
        assert await conn.citations(DOI, "in") == [ExternalId("doi", "10.1000/a"), ExternalId("pmid", "5")]
        assert await conn.citations(DOI, "out") == [ExternalId("doi", "10.1000/x"), ExternalId("doi", "10.1000/y")]


async def test_pmid_is_a_valid_scheme():
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return count(0)

    async with client_for(handler) as client:
        await OpenCitationsConnector(client).citations(ExternalId("pmid", "26017442"), "out")
    assert paths == ["/index/v2/reference-count/pmid:26017442"]


async def test_reserved_characters_are_percent_encoded_in_the_path():
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.raw_path.decode())
        return count(0)

    async with client_for(handler) as client:
        conn = OpenCitationsConnector(client)
        for doi in ("10.1234/a#b", "10.1234/a?x", "10.1234/a%b"):
            await conn.citations(ExternalId("doi", doi), "in")
    assert "%23" in paths[0] and "%3F" in paths[1] and "%25" in paths[2]
    assert all(p.startswith("/index/v2/citation-count/doi:10.1234/a") and "?" not in p for p in paths)


async def test_cache_keys_and_ttls():
    seen: list[tuple[str, object, object]] = []

    class Spy(PoliteClient):
        async def get(self, url, **kwargs):
            seen.append((kwargs["cache_key"], kwargs["ttl"], kwargs.get("max_bytes")))
            return await super().get(url, **kwargs)

    def handler(request: httpx.Request) -> httpx.Response:
        return (
            count(1)
            if "count" in request.url.path
            else httpx.Response(200, json=[{"citing": "doi:10.1000/a", "cited": "doi:10.1000/b"}])
        )

    async with Spy(transport=httpx.MockTransport(handler), limits={"default": (0.0, 1)}) as client:
        conn = OpenCitationsConnector(client)
        await conn.citations(DOI, "in")
        await conn.citations(DOI, "out")
    d = "doi:10.1038/nature14539"
    assert seen == [
        (f"opencitations:v1:citation-count:{d}", TTL_COUNTS, None),
        (f"opencitations:v1:citations:{d}", TTL_COUNTS, 20_000_000),
        (f"opencitations:v1:reference-count:{d}", TTL_COUNTS, None),
        (f"opencitations:v1:references:{d}", TTL_METADATA, 20_000_000),
    ]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>maintenance</html>"),
        httpx.Response(200, json={"count": "1"}),
        httpx.Response(200, json=[]),
        httpx.Response(200, json=[{"count": "many"}]),
        httpx.Response(200, json=[{"nope": 1}]),
        httpx.Response(200, json=[{"count": None}]),
    ],
)
async def test_unreadable_count_is_source_unavailable(response):
    async with client_for(lambda r: response) as client:
        with pytest.raises(SourceUnavailable, match="unreadable response"):
            await OpenCitationsConnector(client).citations(DOI, "in")


async def test_not_found_returns_empty():
    async with client_for(lambda r: httpx.Response(404, text="")) as client:
        assert await OpenCitationsConnector(client).citations(DOI, "in") == []


async def test_unreadable_list_and_http_errors_are_source_unavailable():
    def bad_list(request: httpx.Request) -> httpx.Response:
        if "count" in request.url.path:
            return count(1)
        return httpx.Response(200, json={"rows": []})

    def bad_rows(request: httpx.Request) -> httpx.Response:
        if "count" in request.url.path:
            return count(1)
        return httpx.Response(200, json=["x"])

    for handler in (bad_list, bad_rows, lambda r: httpx.Response(400, text="bad id")):
        async with client_for(handler) as client:
            with pytest.raises(SourceUnavailable):
                await OpenCitationsConnector(client).citations(DOI, "in")


async def test_row_cap_applies_to_the_list_too():
    rows = [{"citing": f"doi:10.1000/{i}"} for i in range(oc.MAX_ROWS + 1)]

    def handler(request: httpx.Request) -> httpx.Response:
        return count(10) if "count" in request.url.path else httpx.Response(200, json=rows)  # a stale count

    async with client_for(handler) as client:
        with pytest.raises(TooManyResults):
            await OpenCitationsConnector(client).citations(DOI, "in")


# ---- cassettes (recorded live, keyless) ----------------------------------------------------------------------------


@pytest.mark.vcr
async def test_references_out(polite):
    refs = await OpenCitationsConnector(polite).citations(DOI, "out")
    assert 20 < len(refs) <= 45 and all(r.scheme in ("doi", "pmid", "openalex") for r in refs)
    assert any(r.scheme == "doi" for r in refs)


@pytest.mark.vcr
async def test_citers_in(polite):
    citers = await OpenCitationsConnector(polite).citations(ExternalId("doi", "10.1016/j.joi.2017.08.002"), "in")
    assert 30 < len(citers) < 100 and ExternalId("doi", "10.1016/j.ipm.2025.104546") in citers


@pytest.mark.vcr
async def test_famous_paper_is_refused_by_count_alone(polite):
    with pytest.raises(TooManyResults):
        await OpenCitationsConnector(polite).citations(ExternalId("pmid", "26017442"), "in")


@pytest.mark.vcr
async def test_unknown_doi_has_no_citations(polite):
    assert await OpenCitationsConnector(polite).citations(ExternalId("doi", "10.1038/doesnotexist0000"), "in") == []
