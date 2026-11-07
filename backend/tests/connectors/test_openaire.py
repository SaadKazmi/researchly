import httpx
import pytest

from researchly.connectors import openaire as oa
from researchly.connectors.base import Author, ExternalId, SearchQuery, SourceUnavailable
from researchly.connectors.http import PoliteClient
from researchly.connectors.openaire import OpenAireConnector

pytestmark = pytest.mark.anyio

ARXIV_DOI = ExternalId("doi", "10.48550/arxiv.1706.03762")
ARXIV_LICENCE = "http://arxiv.org/licenses/nonexclusive-distrib/1.0/"

MERGED = {
    "id": "doi_dedup___::5e53125f15c47535bb231d51dc2a2cce",
    "mainTitle": "Attention Is All You Need",
    "descriptions": ["<jats:p>The dominant sequence transduction models</jats:p>"],
    "authors": [
        {"fullName": "Noam Shazeer", "rank": 2, "pid": None},
        {"fullName": "Ashish Vaswani", "rank": 1, "pid": {"id": {"scheme": "orcid", "value": "0000-0002-1825-009X"}}},
    ],
    "publicationDate": "2025-08-23",  # a spam repost's date: never used for a merged record
    "publisher": "Shenzhen Medical Academy of Research and Translation",
    "container": {"name": "Spam Journal"},
    "pids": [{"scheme": "doi", "value": "10.48550/arxiv.1706.03762"}, {"scheme": "doi", "value": "10.65215/spam.1"}],
    "indicators": {"citationImpact": {"citationCount": 16770}},
    "instances": [
        {
            "pids": [{"scheme": "doi", "value": "10.65215/spam.1"}],
            "publicationDate": "2025-08-23",
            "license": "CC BY",
            "accessRight": {"label": "OPEN"},
            "urls": ["https://spam.example/1"],
        },
        {
            "pids": [{"scheme": "doi", "value": "10.48550/arxiv.1706.03762"}],
            "alternateIdentifiers": [{"scheme": "arXiv", "value": "1706.03762"}],
            "publicationDate": "2017-01-01",
            "license": ARXIV_LICENCE,
            "accessRight": None,
            "urls": ["https://arxiv.org/abs/1706.03762"],
        },
    ],
}

PLAIN = {
    "id": "od______::abc",
    "mainTitle": "A Journal Paper",
    "publicationDate": "2021-05-01",
    "container": {"name": "Neurocomputing"},
    "instances": [
        {
            "pids": [{"scheme": "doi", "value": "10.1016/j.neucom.2021.01.001"}],
            "publicationDate": "2021-05-01",
            "accessRight": {"label": "OPEN"},
            "license": "CC BY",
            "urls": ["ftp://bad.example/x", "https://repo.example/paper.pdf"],
        }
    ],
}


def client_for(handler) -> PoliteClient:
    return PoliteClient(transport=httpx.MockTransport(handler), limits={"default": (0.0, 1)})


def results(*rows):
    return httpx.Response(200, json={"header": {"numFound": len(rows)}, "results": list(rows)})


def test_a_merged_record_takes_ids_year_licence_and_access_from_the_matching_instance():
    rec = oa.parse_result(MERGED, {ARXIV_DOI})
    assert rec is not None
    assert rec.ids == [ExternalId("arxiv", "1706.03762"), ARXIV_DOI]  # sorted; no spam DOI
    assert rec.year == 2017 and rec.license == ARXIV_LICENCE
    assert rec.is_oa is None and rec.oa_url is None  # the arXiv instance states no access right
    assert rec.venue is None  # a merged record's container may be a repost's
    assert rec.title == "Attention Is All You Need"
    assert rec.abstract == "The dominant sequence transduction models"
    assert rec.authors == [Author("Ashish Vaswani", orcid="0000-0002-1825-009X"), Author("Noam Shazeer")]
    assert rec.cited_by == 16770 and rec.extra == {"openaire_id": MERGED["id"]}


def test_a_merged_record_without_the_wanted_instance_is_dropped():
    assert oa.parse_result(MERGED, {ExternalId("doi", "10.1234/other")}) is None


def test_without_a_wanted_id_the_earliest_instance_wins():
    rec = oa.parse_result(MERGED)
    assert rec is not None and rec.year == 2017


def test_a_plain_record():
    rec = oa.parse_result(PLAIN)
    assert rec is not None
    assert rec.ids == [ExternalId("doi", "10.1016/j.neucom.2021.01.001")] and rec.year == 2021
    assert rec.venue == "Neurocomputing" and rec.is_oa is True and rec.oa_url == "https://repo.example/paper.pdf"
    assert oa.parse_result({"id": "x", "mainTitle": ""}) is None
    assert oa.parse_result("junk") is None


async def test_get_asks_by_pid_and_skips_comma_dois(caplog):
    caplog.set_level("INFO")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        response = results(MERGED)
        response.headers["x-ratelimit-used"] = "3"
        return response

    async with client_for(handler) as client:
        recs = await OpenAireConnector(client).get(
            [ExternalId("arxiv", "1706.03762"), ExternalId("doi", "10.1000/a,b")]
        )
    assert seen == [{"pid": "10.48550/arxiv.1706.03762", "pageSize": "5"}]
    assert [r.year for r in recs] == [2017]
    assert "x-ratelimit-used=3" in caplog.text


async def test_search_params():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/graph/v1/researchProducts"
        assert dict(request.url.params) == {
            "search": "attention",
            "type": "publication",
            "pageSize": "100",
            "fromPublicationDate": "2017",
            "toPublicationDate": "2020",
        }
        return results(PLAIN, {"mainTitle": "no instances, unmerged", "id": "x"})

    async with client_for(handler) as client:
        recs = await OpenAireConnector(client).search(SearchQuery("attention", limit=500, year_from=2017, year_to=2020))
    assert [r.title for r in recs] == ["A Journal Paper"]  # a record without any ID is dropped


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(400, json={"message": "Unknown parameter"}), "HTTP 400"),
        (httpx.Response(200, text="<html>"), "unreadable response"),
        (httpx.Response(200, json={"results": {"not": "a list"}}), "unreadable response"),
    ],
)
async def test_errors(response, message):
    async with client_for(lambda r: response) as client:
        with pytest.raises(SourceUnavailable, match=message):
            await OpenAireConnector(client).search(SearchQuery("x"))


@pytest.mark.vcr
async def test_get_resolves_the_merged_arxiv_record(polite):
    [rec] = await OpenAireConnector(polite).get([ExternalId("doi", "10.48550/arXiv.1706.03762")])
    assert "Attention" in rec.title and rec.year == 2017
    assert ARXIV_DOI in rec.ids and not any(i.value.startswith("10.65215/") for i in rec.ids)
    assert rec.license == ARXIV_LICENCE


@pytest.mark.vcr
async def test_search_finds_the_transformer_paper(polite):
    recs = await OpenAireConnector(polite).search(SearchQuery("attention is all you need", limit=3))
    assert recs and all(r.title and r.ids for r in recs)


@pytest.mark.vcr
async def test_get_not_found(polite):
    assert await OpenAireConnector(polite).get([ExternalId("doi", "10.1234/does-not-exist-xyz")]) == []
