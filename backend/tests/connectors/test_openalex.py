import datetime as dt
from urllib.parse import parse_qs

import httpx
import pytest

from researchly.connectors import openalex as oa
from researchly.connectors.base import ExternalId, QuotaExhausted, SearchQuery, SourceUnavailable
from researchly.connectors.http import PoliteClient
from researchly.connectors.openalex import OpenAlexConnector, rebuild_abstract

pytestmark = pytest.mark.anyio

WORK = {
    "id": "https://openalex.org/W1",
    "doi": "https://doi.org/10.48550/arxiv.1706.03762",
    "title": "Attention Is All You Need",
    "publication_year": 2017,
    "abstract_inverted_index": {"b": [1], "a": [0]},
    "ids": {
        "openalex": "https://openalex.org/W1",
        "pmid": "https://pubmed.ncbi.nlm.nih.gov/26017442",
        "pmcid": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4176673",
    },
    "authorships": [
        {
            "author": {
                "id": "https://openalex.org/A9",
                "display_name": "Ashish Vaswani",
                "orcid": "https://orcid.org/0000-0001-2345-6789",
            }
        },
        {"author": {"id": None, "display_name": "Noam Shazeer", "orcid": None}},
    ],
    "primary_location": {"source": {"display_name": "arXiv", "type": "repository"}, "license": "cc-by"},
    "best_oa_location": {"pdf_url": None, "license": None},
    "open_access": {"is_oa": True, "oa_url": "https://arxiv.org/abs/1706.03762"},
    "locations": [{"landing_page_url": "http://arxiv.org/abs/1706.03762v7"}, {"landing_page_url": None}],
    "cited_by_count": 5,
    "fwci": None,
    "citation_normalized_percentile": {"value": 0.99},
    "is_retracted": False,
    "referenced_works": ["https://openalex.org/W10", "https://openalex.org/W11"],
    "topics": [{"display_name": "Neural networks"}],
}


def client_for(handler) -> PoliteClient:
    return PoliteClient(transport=httpx.MockTransport(handler), limits={"default": (0.0, 1)})


def budget(remaining: float, limit: float = 0.1) -> dict[str, str]:
    return {"x-ratelimit-remaining-usd": str(remaining), "x-ratelimit-limit-usd": str(limit)}


def test_abstract_rebuilds_in_index_order():
    assert rebuild_abstract({"b": [1], "a": [0]}) == "a b"
    assert rebuild_abstract(None) is None
    assert rebuild_abstract({"a": [0, 2], "b": [1]}) == "a b a"


def test_parse_work():
    rec = oa.parse_work(WORK)
    assert rec.source == "openalex"
    assert rec.id("openalex") == "W1" and rec.id("pmid") == "26017442" and rec.id("pmcid") == "PMC4176673"
    assert rec.id("arxiv") == "1706.03762"
    assert rec.id("doi") == "10.48550/arxiv.1706.03762"
    assert [i.scheme for i in rec.ids].count("doi") == 1
    assert rec.abstract == "a b" and rec.year == 2017 and rec.cited_by == 5 and rec.fwci is None and rec.pct == 0.99
    assert rec.oa_url == "https://arxiv.org/abs/1706.03762" and rec.is_oa is True and rec.license == "cc-by"
    assert (rec.venue, rec.venue_type) == ("arXiv", "repository")
    assert [(a.name, a.orcid, a.openalex_id) for a in rec.authors] == [
        ("Ashish Vaswani", "0000-0001-2345-6789", "A9"),
        ("Noam Shazeer", None, None),
    ]
    assert rec.references == [ExternalId("openalex", "W10"), ExternalId("openalex", "W11")]
    assert rec.extra["topics"] == ["Neural networks"]


def test_parse_work_tolerates_sparse_payload():
    rec = oa.parse_work({"id": "https://openalex.org/W2", "title": None})
    assert rec.title == "" and rec.ids == [ExternalId("openalex", "W2")] and rec.authors == []


async def test_budget_low_blocks_search_but_not_singleton():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers=budget(0.02), json=WORK)

    async with client_for(handler) as client:
        conn = OpenAlexConnector(client)
        assert conn.budget_low is False
        assert [r.id("openalex") for r in await conn.get([ExternalId("openalex", "W1")])] == ["W1"]
        assert conn.budget_low is True  # 0.02 / 0.1 = 20%
        with pytest.raises(QuotaExhausted):
            await conn.search(SearchQuery("x"))
        with pytest.raises(QuotaExhausted):
            await conn.citations(ExternalId("openalex", "W1"), "in")
        assert await conn.get([ExternalId("openalex", "W1")])  # still free


async def test_budget_above_threshold_is_not_low():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers=budget(0.03), json=WORK)

    async with client_for(handler) as client:
        conn = OpenAlexConnector(client)
        await conn.get([ExternalId("openalex", "W1")])
        assert conn.budget_low is False


async def test_fifty_searches_a_day_then_resets_at_utc_midnight(monkeypatch):
    day = dt.date(2026, 11, 3)
    monkeypatch.setattr(oa, "_today", lambda: day)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": []})

    async with client_for(handler) as client:
        conn = OpenAlexConnector(client)
        for i in range(oa.SEARCHES_PER_DAY):
            await conn.search(SearchQuery(f"q{i}"))
        with pytest.raises(QuotaExhausted):
            await conn.search(SearchQuery("one too many"))
        monkeypatch.setattr(oa, "_today", lambda: day + dt.timedelta(days=1))
        assert await conn.search(SearchQuery("next day")) == []


async def test_not_found_html_returns_empty():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="<html><title>404 Not Found</title></html>")

    async with client_for(handler) as client:
        conn = OpenAlexConnector(client)
        assert await conn.get([ExternalId("doi", "10.1234/none")]) == []
        assert await conn.citations(ExternalId("doi", "10.1234/none"), "out") == []
        assert await conn.citations(ExternalId("doi", "10.1234/none"), "in") == []


async def test_other_errors_are_source_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "bad"})

    async with client_for(handler) as client:
        with pytest.raises(SourceUnavailable):
            await OpenAlexConnector(client).get([ExternalId("doi", "10.1234/x")])


async def test_search_params_and_year_bounds():
    seen: list[dict[str, list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(parse_qs(request.url.query.decode()))
        return httpx.Response(200, json={"results": [WORK]})

    async with client_for(handler) as client:
        conn = OpenAlexConnector(client)
        out = await conn.search(SearchQuery("graphs", limit=500, year_from=2015, year_to=2017))
        await conn.search(SearchQuery("graphs", year_from=2015))
        await conn.search(SearchQuery("graphs", year_to=2017))
        await conn.search(SearchQuery("graphs"))
    assert len(out) == 1
    assert seen[0]["search.title_and_abstract"] == ["graphs"] and seen[0]["per-page"] == ["100"]
    assert seen[0]["filter"] == ["publication_year:2015-2017"]
    assert seen[1]["filter"] == ["publication_year:>2014"] and seen[2]["filter"] == ["publication_year:<2018"]
    assert "filter" not in seen[3] and "select" in seen[3]


async def test_get_many_is_one_filter_call_and_budget_low_falls_back_to_singletons():
    urls: list[str] = []
    remaining = {"v": 0.09}

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        body = {"results": [WORK]} if request.url.path == "/works" else WORK
        return httpx.Response(200, headers=budget(remaining["v"]), json=body)

    ids = [
        ExternalId("doi", "10.1234/a"),
        ExternalId("arxiv", "1706.03762"),
        ExternalId("pmid", "5"),
        ExternalId("s2", "x"),
    ]
    async with client_for(handler) as client:
        conn = OpenAlexConnector(client)
        await conn.get(ids)
        filters = [parse_qs(u.split("?")[1])["filter"][0] for u in urls if "filter=" in u]
        assert filters == ["doi:10.1234/a|10.48550/arxiv.1706.03762"]  # the lone PMID is a free singleton
        assert any("/works/pmid:5" in u for u in urls)
        remaining["v"] = 0.01
        await conn.get([ExternalId("openalex", "W1")])  # records the low budget
        urls.clear()
        await conn.get(ids)
        assert all("/works/" in u for u in urls) and len(urls) == 3  # s2 skipped, no filter calls


async def test_citers_stop_at_the_cap(monkeypatch):
    monkeypatch.setattr(oa, "CITERS_CAP", 3)
    pages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        pages.append(parse_qs(request.url.query.decode())["page"][0])
        rows = [{"id": f"https://openalex.org/W{i}"} for i in range(200)]
        return httpx.Response(200, json={"results": rows})

    async with client_for(handler) as client:
        out = await OpenAlexConnector(client).citations(ExternalId("openalex", "W1"), "in")
    assert out == [ExternalId("openalex", f"W{i}") for i in range(3)] and pages == ["1"]


# ---- cassettes (recorded live, keyless) ----------------------------------------------------------------------------


@pytest.mark.vcr
async def test_search_attention(polite):
    recs = await OpenAlexConnector(polite).search(SearchQuery("attention is all you need", limit=3))
    assert 0 < len(recs) <= 3
    top = next(r for r in recs if "Attention Is All You Need" in r.title)
    assert top.year == 2017 and top.authors and top.id("openalex")


@pytest.mark.vcr
async def test_get_by_arxiv_id(polite):
    [rec] = await OpenAlexConnector(polite).get([ExternalId("arxiv", "1706.03762")])
    assert "Attention" in rec.title and rec.year == 2017
    assert rec.id("openalex") == "W2626778328"
    assert rec.id("arxiv") == "1706.03762" and rec.id("doi") == "10.48550/arxiv.1706.03762"
    assert rec.authors and (rec.authors[0].openalex_id or "").startswith("A")
    assert rec.abstract and rec.cited_by and rec.cited_by > 100_000
    assert rec.references


@pytest.mark.vcr
async def test_get_by_doi(polite):
    [rec] = await OpenAlexConnector(polite).get([ExternalId("doi", "10.1038/nature14539")])
    assert rec.title == "Deep learning" and rec.year == 2015
    assert rec.id("doi") == "10.1038/nature14539" and rec.id("pmid") == "26017442"
    assert rec.venue == "Nature" and rec.venue_type == "journal"
    assert rec.fwci and rec.pct is not None and rec.extra["topics"]


@pytest.mark.vcr
async def test_get_by_pmid(polite):
    [rec] = await OpenAlexConnector(polite).get([ExternalId("pmid", "26017442")])
    assert rec.id("doi") == "10.1038/nature14539"


@pytest.mark.vcr
async def test_get_many_uses_a_filter(polite):
    recs = await OpenAlexConnector(polite).get(
        [ExternalId("doi", "10.1038/nature14539"), ExternalId("arxiv", "1706.03762")]
    )
    assert {r.id("doi") for r in recs} == {"10.1038/nature14539", "10.48550/arxiv.1706.03762"}


@pytest.mark.vcr
async def test_references_out(polite):
    refs = await OpenAlexConnector(polite).citations(ExternalId("arxiv", "1706.03762"), "out")
    assert len(refs) > 10 and all(r.scheme == "openalex" and r.value.startswith("W") for r in refs)


@pytest.mark.vcr
async def test_citers_in(polite, monkeypatch):
    monkeypatch.setattr(oa, "CITERS_CAP", 200)  # one page keeps the cassette small
    citers = await OpenAlexConnector(polite).citations(ExternalId("openalex", "W2626778328"), "in")
    assert len(citers) == 200 and all(c.scheme == "openalex" for c in citers)


@pytest.mark.vcr
async def test_not_found(polite):
    conn = OpenAlexConnector(polite)
    assert await conn.get([ExternalId("openalex", "W1")]) == []
    assert await conn.citations(ExternalId("doi", "10.1038/doesnotexist0000"), "out") == []


# ---- fix round 1 ---------------------------------------------------------------------------------------------------


async def test_doi_with_reserved_characters_is_percent_encoded_in_the_path():
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.raw_path.decode())
        return httpx.Response(200, json=WORK)

    async with client_for(handler) as client:
        conn = OpenAlexConnector(client)
        await conn.get([ExternalId("doi", "10.1234/a#b")])
        await conn.get([ExternalId("doi", "10.1234/a?x")])
    assert "%23" in paths[0] and "%3F" in paths[1] and all("/works/doi:10.1234/a" in p for p in paths)


async def test_doi_with_comma_or_pipe_never_enters_a_filter():
    reqs: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        reqs.append(request.url.raw_path.decode())
        return httpx.Response(200, json={"results": [WORK]} if request.url.path == "/works" else WORK)

    ids_ = [ExternalId("doi", "10.1234/a,b"), ExternalId("doi", "10.1234/c|d")]
    ids_ += [ExternalId("doi", "10.1234/e"), ExternalId("doi", "10.1234/f")]
    async with client_for(handler) as client:
        await OpenAlexConnector(client).get(ids_)
    filters = [r for r in reqs if r.startswith("/works?")]
    assert len(filters) == 1 and "a%2Cb" not in filters[0] and "c%7Cd" not in filters[0]
    assert "10.1234%2Fe" in filters[0] or "10.1234/e" in filters[0]
    assert sum(r.startswith("/works/doi:") for r in reqs) == 2


async def test_budget_reading_goes_stale_at_utc_midnight(monkeypatch):
    day = dt.date(2026, 11, 3)
    monkeypatch.setattr(oa, "_today", lambda: day)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers=budget(0.01), json=WORK | {"results": [WORK]})

    async with client_for(handler) as client:
        conn = OpenAlexConnector(client)
        await conn.get([ExternalId("openalex", "W1")])
        assert conn.budget_low is True
        with pytest.raises(QuotaExhausted):
            await conn.search(SearchQuery("x"))
        monkeypatch.setattr(oa, "_today", lambda: day + dt.timedelta(days=1))
        assert conn.budget_low is False
        assert await conn.search(SearchQuery("x"))  # no request needed first


async def test_unreadable_200_body_is_source_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>maintenance</html>")

    async with client_for(handler) as client:
        conn = OpenAlexConnector(client)
        with pytest.raises(SourceUnavailable, match="unreadable response"):
            await conn.get([ExternalId("openalex", "W1")])
        with pytest.raises(SourceUnavailable, match="unreadable response"):
            await conn.search(SearchQuery("x"))


async def test_one_id_per_scheme_is_a_free_singleton_not_a_filter():
    reqs: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        reqs.append(request.url.path)
        return httpx.Response(200, json=WORK)

    async with client_for(handler) as client:
        await OpenAlexConnector(client).get([ExternalId("doi", "10.1234/a"), ExternalId("pmid", "5")])
    assert reqs == ["/works/doi:10.1234/a", "/works/pmid:5"]


async def test_references_out_reuses_the_full_record_request():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.query.decode())
        return httpx.Response(200, json=WORK)

    async with client_for(handler) as client:
        conn = OpenAlexConnector(client)
        refs = await conn.citations(ExternalId("openalex", "W1"), "out")
        await conn.get([ExternalId("openalex", "W1")])
    assert refs == [ExternalId("openalex", "W10"), ExternalId("openalex", "W11")]
    assert calls[0] == calls[1]  # same request, hence the same cache entry


def test_cache_keys_are_versioned():
    assert oa.singleton_key("doi", "10.1234/a") == "openalex:v1:doi:10.1234/a"


async def test_citers_recheck_the_budget_between_pages():
    pages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = parse_qs(request.url.query.decode())["page"][0]
        pages.append(page)
        rows = [{"id": f"https://openalex.org/W{(int(page) - 1) * 200 + i}"} for i in range(200)]
        return httpx.Response(200, headers=budget(0.01), json={"results": rows})

    async with client_for(handler) as client:
        with pytest.raises(QuotaExhausted):
            await OpenAlexConnector(client).citations(ExternalId("openalex", "W1"), "in")
    assert pages == ["1"]


async def test_citers_two_pages_are_unioned():
    pages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = parse_qs(request.url.query.decode())["page"][0]
        pages.append(page)
        n = 200 if page == "1" else 5
        rows = [{"id": f"https://openalex.org/W{(int(page) - 1) * 1000 + i}"} for i in range(n)]
        return httpx.Response(200, json={"results": rows})

    async with client_for(handler) as client:
        out = await OpenAlexConnector(client).citations(ExternalId("openalex", "W1"), "in")
    assert pages == ["1", "2"] and len(out) == 205
    assert ExternalId("openalex", "W199") in out and ExternalId("openalex", "W1004") in out


def test_orcid_and_author_id_are_validated():
    def author(orcid, aid):
        work = {
            "id": "https://openalex.org/W2",
            "authorships": [{"author": {"display_name": "X", "orcid": orcid, "id": aid}}],
        }
        return oa.parse_work(work).authors[0]

    good = author("https://orcid.org/0000-0002-1825-009X", "https://openalex.org/A5023888391")
    assert (good.orcid, good.openalex_id) == ("0000-0002-1825-009X", "A5023888391")
    bad = author("https://orcid.org/not-an-orcid", "https://openalex.org/I123")
    assert (bad.orcid, bad.openalex_id) == (None, None)
