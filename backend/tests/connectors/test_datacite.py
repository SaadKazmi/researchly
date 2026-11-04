import httpx
import pytest

from researchly.connectors import datacite as dc
from researchly.connectors.base import ExternalId, SearchQuery, SourceUnavailable
from researchly.connectors.datacite import DataCiteConnector
from researchly.connectors.http import TTL_METADATA, PoliteClient

pytestmark = pytest.mark.anyio

ATTRS = {
    "doi": "10.48550/ARXIV.1706.03762",
    "titles": [{"title": "Attention Is All You Need"}],
    "descriptions": [
        {"descriptionType": "Other", "description": "not this"},
        {"descriptionType": "Abstract", "description": "The dominant sequence models"},
    ],
    "publicationYear": 2017,
    "publisher": "arXiv",
    "creators": [
        {
            "name": "Vaswani, Ashish",
            "nameIdentifiers": [
                {"nameIdentifierScheme": "ISNI", "nameIdentifier": "0000000000000000"},
                {"nameIdentifierScheme": "ORCID", "nameIdentifier": "https://orcid.org/0000-0002-1825-009X"},
            ],
        },
        {"name": "Shazeer, Noam", "nameIdentifiers": [{"nameIdentifierScheme": "ORCID", "nameIdentifier": "junk"}]},
        {"givenName": "No Name"},
    ],
    "identifiers": [{"identifier": "1706.03762v7", "identifierType": "arXiv"}],
    "rightsList": [{"rightsUri": "https://arxiv.org/licenses/nonexclusive-distrib/1.0/"}],
}


def client_for(handler) -> PoliteClient:
    return PoliteClient(transport=httpx.MockTransport(handler), limits={"default": (0.0, 1)})


def ok(attrs=ATTRS) -> httpx.Response:
    return httpx.Response(200, json={"data": {"attributes": attrs}})


def test_parse_item():
    rec = dc.parse_item(ATTRS)
    assert rec.source == "datacite"
    assert rec.ids == [ExternalId("doi", "10.48550/arxiv.1706.03762"), ExternalId("arxiv", "1706.03762")]
    assert rec.title == "Attention Is All You Need" and rec.year == 2017 and rec.venue == "arXiv"
    assert rec.abstract == "The dominant sequence models"
    assert rec.license == "https://arxiv.org/licenses/nonexclusive-distrib/1.0/"
    assert [(a.name, a.orcid) for a in rec.authors] == [
        ("Vaswani, Ashish", "0000-0002-1825-009X"),
        ("Shazeer, Noam", None),
    ]


def test_parse_item_tolerates_sparse_payload():
    rec = dc.parse_item({"doi": "10.1/x", "titles": None, "publicationYear": "2017", "publisher": {"name": "x"}})
    assert rec.title == "" and rec.year is None and rec.venue is None and rec.abstract is None
    assert rec.authors == [] and rec.license is None and rec.ids == []  # "10.1/x" is not a DOI


def test_capabilities_and_cache_key_prefix():
    caps = DataCiteConnector(PoliteClient()).capabilities()
    assert (caps.search, caps.get, caps.citations_in, caps.citations_out) == (False, True, False, False)
    assert dc.KEY == "datacite:v1"


async def test_search_and_citations_make_no_request():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    async with client_for(handler) as client:
        conn = DataCiteConnector(client)
        assert await conn.search(SearchQuery("x")) == []
        assert await conn.citations(ExternalId("doi", "10.1234/x"), "in") == []


async def test_get_maps_arxiv_passes_dois_skips_others_and_dedupes():
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return ok()

    async with client_for(handler) as client:
        recs = await DataCiteConnector(client).get(
            [
                ExternalId("arxiv", "1706.03762v7"),
                ExternalId("doi", "10.48550/arXiv.1706.03762"),  # same DOI as the line above
                ExternalId("doi", "10.5281/zenodo.1"),
                ExternalId("pmid", "5"),
                ExternalId("s2", "x"),
                ExternalId("doi", "bad"),
            ]
        )
    assert paths == ["/dois/10.48550/arxiv.1706.03762", "/dois/10.5281/zenodo.1"] and len(recs) == 2


async def test_get_without_usable_ids_makes_no_request():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    async with client_for(handler) as client:
        assert await DataCiteConnector(client).get([ExternalId("pmid", "5"), ExternalId("openalex", "W1")]) == []
        assert await DataCiteConnector(client).get([]) == []


async def test_404_skips_that_doi_only():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/none"):
            return httpx.Response(404, json={"errors": [{"status": "404"}]})
        return ok()

    async with client_for(handler) as client:
        recs = await DataCiteConnector(client).get([ExternalId("doi", "10.1234/none"), ExternalId("doi", "10.1234/ok")])
    assert len(recs) == 1


async def test_doi_with_reserved_characters_is_percent_encoded_in_the_path():
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.raw_path.decode())
        return ok()

    async with client_for(handler) as client:
        conn = DataCiteConnector(client)
        for doi in ("10.1234/a#b", "10.1234/a?x", "10.1234/a%b"):
            await conn.get([ExternalId("doi", doi)])
    assert "%23" in paths[0] and "%3F" in paths[1] and "%25" in paths[2]
    assert all(p.startswith("/dois/10.1234/a") and "?" not in p for p in paths)


async def test_cache_key_and_ttl():
    seen: list[tuple[str, object]] = []

    class Spy(PoliteClient):
        async def get(self, url, **kwargs):
            seen.append((kwargs["cache_key"], kwargs["ttl"]))
            return await super().get(url, **kwargs)

    async with Spy(transport=httpx.MockTransport(lambda r: ok()), limits={"default": (0.0, 1)}) as client:
        await DataCiteConnector(client).get([ExternalId("arxiv", "1706.03762")])
    assert seen == [("datacite:v1:doi:10.48550/arxiv.1706.03762", TTL_METADATA)]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>maintenance</html>"),
        httpx.Response(200, json={"data": []}),
        httpx.Response(200, json={"data": {"attributes": "x"}}),
        httpx.Response(200, json={"errors": []}),
        httpx.Response(200, json={"data": {"attributes": {"titles": ["x"]}}}),
        httpx.Response(400, json={"errors": []}),
    ],
)
async def test_unreadable_bodies_and_errors_are_source_unavailable(response):
    async with client_for(lambda r: response) as client:
        with pytest.raises(SourceUnavailable):
            await DataCiteConnector(client).get([ExternalId("doi", "10.1234/x")])


# ---- cassettes (recorded live, keyless) ----------------------------------------------------------------------------


@pytest.mark.vcr
async def test_get_by_arxiv_id(polite):
    [rec] = await DataCiteConnector(polite).get([ExternalId("arxiv", "1706.03762")])
    assert "Attention" in rec.title and rec.year == 2017 and rec.venue == "arXiv"
    assert rec.id("doi") == "10.48550/arxiv.1706.03762" and rec.id("arxiv") == "1706.03762"
    assert rec.authors and rec.abstract


@pytest.mark.vcr
async def test_get_old_style_arxiv_id(polite):
    [rec] = await DataCiteConnector(polite).get([ExternalId("arxiv", "hep-th/9901001")])
    assert rec.id("doi") == "10.48550/arxiv.hep-th/9901001" and rec.title


@pytest.mark.vcr
async def test_not_found(polite):
    assert await DataCiteConnector(polite).get([ExternalId("doi", "10.48550/arxiv.0000.00000")]) == []
