import httpx
import pytest

from researchly.connectors import dblp
from researchly.connectors.base import ExternalId, SearchQuery, SourceUnavailable, UnwantedContent
from researchly.connectors.dblp import DblpConnector
from researchly.connectors.http import PoliteClient

pytestmark = pytest.mark.anyio

ARXIV_DOI = "10.48550/arxiv.1706.03762"
S = "https://dblp.org/rdf/schema#"


def binding(key, kind, venue, year="2017", doi=None, title="Attention is All you Need."):
    b = {
        "p": {"type": "uri", "value": f"https://dblp.org/rec/{key}"},
        "t": {"type": "literal", "value": title},
        "venue": {"type": "literal", "value": venue},
        "year": {"type": "literal", "value": year},
        "type": {"type": "uri", "value": S + kind},
    }
    if doi:
        b["doi"] = {"type": "uri", "value": f"https://doi.org/{doi}"}
    return b


BINDINGS = [
    binding("journals/corr/VaswaniSPUJGKP17", "Informal", "CoRR", doi="10.48550/ARXIV.1706.03762"),
    binding("conf/nips/VaswaniSPUJGKP17", "Publication", "NIPS"),  # the super-type: ignored
    binding("conf/nips/VaswaniSPUJGKP17", "Inproceedings", "NIPS"),
]


def sparql(bindings):
    return httpx.Response(
        200,
        json={"head": {"vars": ["p", "t", "venue", "year", "type", "doi"]}, "results": {"bindings": bindings}},
        headers={"content-type": "application/sparql-results+json"},
    )


def client_for(handler) -> PoliteClient:
    return PoliteClient(transport=httpx.MockTransport(handler), limits={"default": (0.0, 1)})


def test_parse_bindings_prefers_the_venue_record():
    rec = dblp.parse_bindings(BINDINGS, ARXIV_DOI)
    assert rec is not None
    assert rec.source == "dblp"
    assert rec.ids == [ExternalId("doi", ARXIV_DOI), ExternalId("dblp", "conf/nips/VaswaniSPUJGKP17")]
    assert (rec.title, rec.year, rec.venue, rec.venue_type) == ("Attention is All you Need", 2017, "NIPS", "conference")
    assert rec.extra == {"dblp_venue": "NIPS", "dblp_type": "Inproceedings", "dblp_doi": None}


def test_parse_bindings_falls_back_to_the_preprint_record():
    rec = dblp.parse_bindings(BINDINGS[:1], ARXIV_DOI)
    assert rec is not None and rec.venue_type == "repository" and rec.venue == "CoRR"
    assert rec.extra["dblp_doi"] == ARXIV_DOI  # lower-cased; extra only (ruling 12)
    assert dblp.parse_bindings([], ARXIV_DOI) is None
    assert dblp.parse_bindings([binding("x/y", "Publication", "V")], ARXIV_DOI) is None


async def test_get_asks_for_both_doi_cases_and_maps_arxiv_ids():
    queries = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "sparql.dblp.org" and request.url.path == "/sparql"
        queries.append(request.url.params["query"])
        return sparql(BINDINGS)

    async with client_for(handler) as client:
        [rec] = await DblpConnector(client).get([ExternalId("arxiv", "1706.03762v7")])
    assert rec.id("dblp") == "conf/nips/VaswaniSPUJGKP17"
    assert "<https://doi.org/10.48550/ARXIV.1706.03762>" in queries[0]
    assert "<https://doi.org/10.48550/arxiv.1706.03762>" in queries[0]


async def test_unsafe_dois_and_other_schemes_make_no_request():
    seen = []
    async with client_for(lambda r: seen.append(r) or sparql([])) as client:
        conn = DblpConnector(client)
        assert await conn.get([ExternalId("doi", "10.1000/a>b"), ExternalId("pmid", "26017442")]) == []
        assert await conn.search(SearchQuery("attention")) == []
    assert seen == []


async def test_not_found_is_empty():
    async with client_for(lambda r: sparql([])) as client:
        assert await DblpConnector(client).get([ExternalId("doi", "10.1234/none")]) == []


async def test_the_anubis_page_is_unavailable_never_parsed():
    page = httpx.Response(202, text="<html>Making sure you're not a bot!</html>", headers={"content-type": "text/html"})
    async with client_for(lambda r: page) as client:
        with pytest.raises(SourceUnavailable, match="HTTP 202"):
            await DblpConnector(client).get([ExternalId("doi", ARXIV_DOI)])


async def test_bad_answers():
    html = httpx.Response(200, text="<html>", headers={"content-type": "text/html"})
    async with client_for(lambda r: html) as client:
        with pytest.raises(UnwantedContent):
            await DblpConnector(client).get([ExternalId("doi", ARXIV_DOI)])
    shapeless = httpx.Response(200, json={"results": []}, headers={"content-type": "application/sparql-results+json"})
    async with client_for(lambda r: shapeless) as client:
        with pytest.raises(SourceUnavailable, match="unreadable response"):
            await DblpConnector(client).get([ExternalId("doi", ARXIV_DOI)])


@pytest.mark.vcr
async def test_get_an_arxiv_paper_finds_its_conference(polite):
    [rec] = await DblpConnector(polite).get([ExternalId("arxiv", "1706.03762")])
    assert rec.id("dblp") == "conf/nips/VaswaniSPUJGKP17"
    assert (rec.venue, rec.year, rec.venue_type) == ("NIPS", 2017, "conference")


@pytest.mark.vcr
async def test_get_a_publisher_doi(polite):
    [rec] = await DblpConnector(polite).get([ExternalId("doi", "10.1109/CVPR.2016.90")])
    assert rec.venue == "CVPR" and rec.venue_type == "conference" and rec.year == 2016


@pytest.mark.vcr
async def test_get_not_found(polite):
    assert await DblpConnector(polite).get([ExternalId("doi", "10.1234/does-not-exist-xyz")]) == []
