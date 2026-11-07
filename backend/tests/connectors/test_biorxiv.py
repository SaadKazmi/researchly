import httpx
import pytest

from researchly.connectors import biorxiv as bx
from researchly.connectors.base import ExternalId, SearchQuery, SourceUnavailable
from researchly.connectors.biorxiv import BiorxivConnector
from researchly.connectors.http import PoliteClient

pytestmark = pytest.mark.anyio

DOI = "10.1101/2020.03.22.002386"


def row(version, date, **kw):
    base = {
        "doi": DOI,
        "title": "A SARS-CoV-2-Human Protein-Protein Interaction Map",
        "authors": "Gordon, D. E.; Jang, G. M.; Bouhaddou, M.",
        "author_corresponding": "Nevan Krogan",
        "date": date,
        "version": str(version),
        "type": "new results",
        "license": "cc_by",
        "category": "systems biology",
        "jatsxml": f"https://www.biorxiv.org/content/early/{date}/{DOI}.source.xml",
        "abstract": "An abstract.",
        "published": "10.1038/s41586-020-2286-9",
        "server": "bioRxiv",
    }
    return base | kw


ROWS = [row(1, "2020-03-22", title="Old title"), row(3, "2020-04-03"), row(2, "2020-03-27")]


def envelope(rows, status="ok"):
    return {"messages": [{"status": status}], "collection": rows}


def client_for(handler) -> PoliteClient:
    return PoliteClient(transport=httpx.MockTransport(handler), limits={"default": (0.0, 1)})


def test_parse_details_takes_the_latest_version_and_the_first_year():
    rec = bx.parse_details(ROWS, "biorxiv")
    assert rec is not None
    assert rec.source == "biorxiv" and rec.ids == [ExternalId("doi", DOI)]
    assert rec.title == "A SARS-CoV-2-Human Protein-Protein Interaction Map"  # v3, not v1's
    assert rec.year == 2020 and rec.abstract == "An abstract."
    assert [a.name for a in rec.authors] == ["Gordon, D. E.", "Jang, G. M.", "Bouhaddou, M."]
    assert (rec.venue, rec.venue_type, rec.is_oa, rec.license) == ("bioRxiv", "repository", True, "cc-by")
    assert rec.extra == {
        "server": "biorxiv",
        "version": 3,
        "category": "systems biology",
        "jatsxml": f"https://www.biorxiv.org/content/early/2020-04-03/{DOI}.source.xml",
        "published_doi": "10.1038/s41586-020-2286-9",  # extra only: the journal version is another work
    }


def test_parse_details_edge_cases():
    unpublished = bx.parse_details([row(1, "2021-01-05", published="NA", license="cc_no")], "medrxiv")
    assert unpublished is not None
    assert unpublished.extra["published_doi"] is None and unpublished.license is None
    assert unpublished.venue == "medRxiv"
    assert bx.parse_details([], "biorxiv") is None
    assert bx.parse_details([{"doi": "not a doi"}, "junk"], "biorxiv") is None


async def test_get_tries_biorxiv_then_medrxiv():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if "/biorxiv/" in request.url.path:
            return httpx.Response(200, json=envelope([], "no posts found"))
        return httpx.Response(200, json=envelope([row(1, "2021-01-05")]))

    async with client_for(handler) as client:
        [rec] = await BiorxivConnector(client).get([ExternalId("doi", f"https://doi.org/{DOI.upper()}")])
    assert seen == [f"/details/biorxiv/{DOI}", f"/details/medrxiv/{DOI}"]
    assert rec.venue == "medRxiv"


async def test_other_dois_and_other_schemes_make_no_request():
    seen = []
    async with client_for(lambda r: seen.append(r) or httpx.Response(500)) as client:
        conn = BiorxivConnector(client)
        assert await conn.get([ExternalId("doi", "10.1038/nature14539"), ExternalId("arxiv", "1706.03762")]) == []
        assert await conn.search(SearchQuery("anything")) == []
        assert await conn.citations(ExternalId("doi", DOI), "in") == []
    assert seen == []


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(403, text="Forbidden"), "HTTP 403"),
        (httpx.Response(200, text="<html>Cloudflare</html>"), "unreadable response"),
        (httpx.Response(200, json={"collection": []}), "unreadable response"),  # no messages
    ],
)
async def test_errors(response, message):
    async with client_for(lambda r: response) as client:
        with pytest.raises(SourceUnavailable, match=message):
            await BiorxivConnector(client).get([ExternalId("doi", DOI)])


async def test_a_404_is_not_found():
    async with client_for(lambda r: httpx.Response(404)) as client:
        assert await BiorxivConnector(client).get([ExternalId("doi", DOI)]) == []


@pytest.mark.vcr
async def test_get_a_published_preprint(polite):
    [rec] = await BiorxivConnector(polite).get([ExternalId("doi", DOI)])
    assert "SARS-CoV-2" in rec.title and rec.year == 2020 and rec.venue == "bioRxiv"
    assert rec.extra["published_doi"] == "10.1038/s41586-020-2286-9"
    assert rec.authors and rec.abstract and rec.license


@pytest.mark.vcr
async def test_not_found_on_either_server(polite):
    assert await BiorxivConnector(polite).get([ExternalId("doi", "10.1101/0000.00.00.000000")]) == []


async def test_only_biorxiv_shaped_suffixes_are_asked():
    seen = []
    async with client_for(lambda r: seen.append(r) or httpx.Response(404)) as client:
        assert await BiorxivConnector(client).get([ExternalId("doi", "10.1101/gad.123456.120")]) == []
    assert seen == []
    assert bx.is_biorxiv_doi("10.1101/433706") and bx.is_biorxiv_doi(DOI) and not bx.is_biorxiv_doi("10.1101/gr.1")
