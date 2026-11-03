import json
from urllib.parse import parse_qs

import httpx
import pytest

from researchly.connectors import pubmed as pm
from researchly.connectors.base import ExternalId, SearchQuery, SourceUnavailable
from researchly.connectors.http import TTL_COUNTS, TTL_METADATA, TTL_SEARCH, Fetched, PoliteClient
from researchly.connectors.pubmed import PubMedConnector, parse_articles

pytestmark = pytest.mark.anyio

ARTICLE = """
<PubmedArticle>
  <MedlineCitation>
    <PMID Version="1">26017442</PMID>
    <Article>
      <Journal>
        <Title>Nature</Title>
        <JournalIssue><PubDate><Year>2015</Year><Month>May</Month></PubDate></JournalIssue>
      </Journal>
      <ArticleTitle>Deep <i>learning</i>.</ArticleTitle>
      <Abstract>
        <AbstractText Label="BACKGROUND">Neural <sub>nets</sub> work.</AbstractText>
        <AbstractText Label="RESULTS" NlmCategory="RESULTS">They scale.</AbstractText>
      </Abstract>
      <AuthorList>
        <Author><LastName>LeCun</LastName><ForeName>Yann</ForeName>
          <Identifier Source="ORCID">https://orcid.org/0000-0001-2345-678X</Identifier></Author>
        <Author><LastName>Bengio</LastName><Initials>Y</Initials><Identifier Source="GRID">grid.1</Identifier></Author>
        <Author><CollectiveName>The Consortium</CollectiveName></Author>
        <Author><Initials>X</Initials></Author>
      </AuthorList>
      <PublicationTypeList>
        <PublicationType UI="D016428">Journal Article</PublicationType>
        <PublicationType UI="D016441">Retracted Publication</PublicationType>
      </PublicationTypeList>
      <ELocationID EIdType="doi">10.9999/elocation</ELocationID>
    </Article>
  </MedlineCitation>
  <PubmedData>
    <ArticleIdList>
      <ArticleId IdType="pubmed">26017442</ArticleId>
      <ArticleId IdType="doi">10.1038/NATURE14539</ArticleId>
      <ArticleId IdType="pmc">PMC1234</ArticleId>
    </ArticleIdList>
    <ReferenceList>
      <Reference><ArticleIdList>
        <ArticleId IdType="doi">10.1/leak</ArticleId><ArticleId IdType="pmc">PMC666</ArticleId>
        <ArticleId IdType="pubmed">111</ArticleId>
      </ArticleIdList></Reference>
    </ReferenceList>
  </PubmedData>
</PubmedArticle>
"""

SPARSE = """
<PubmedArticle>
  <MedlineCitation>
    <PMID>5</PMID>
    <Article>
      <Journal><JournalIssue><PubDate><MedlineDate>2015 Jan-Feb</MedlineDate></PubDate></JournalIssue></Journal>
      <ArticleTitle>Plain</ArticleTitle>
      <Abstract><AbstractText>Only text.</AbstractText></Abstract>
      <PublicationTypeList><PublicationType>Review</PublicationType></PublicationTypeList>
      <ELocationID EIdType="doi">10.9999/ELocation</ELocationID>
    </Article>
  </MedlineCitation>
</PubmedArticle>
"""


def sets(*articles: str) -> str:
    doctype = '<!DOCTYPE PubmedArticleSet PUBLIC "x" "y">'
    return f'<?xml version="1.0"?>{doctype}<PubmedArticleSet>{"".join(articles)}</PubmedArticleSet>'


def client_for(handler) -> PoliteClient:
    return PoliteClient(transport=httpx.MockTransport(handler), limits={"default": (0.0, 1)})


@pytest.mark.parametrize("medline", ["2015 Jan-Feb", "Spring 2015", "Winter 2014-2015"])
def test_medline_date_year_is_the_first_four_digits(medline):
    xml = SPARSE.replace("2015 Jan-Feb", medline)
    [rec] = parse_articles(sets(xml).encode(), "pubmed")
    assert rec.year == (2014 if medline.startswith("Winter") else 2015)


def test_parse_article():
    [rec] = parse_articles(sets(ARTICLE).encode(), "pubmed")
    assert rec.title == "Deep learning"
    assert rec.abstract == "BACKGROUND: Neural nets work.\nRESULTS: They scale."
    assert rec.year == 2015 and rec.venue == "Nature"
    assert rec.is_retracted is True
    assert [(a.name, a.orcid) for a in rec.authors] == [
        ("Yann LeCun", "0000-0001-2345-678X"),
        ("Bengio", None),
        ("The Consortium", None),
    ]
    # ids come from PubmedData/ArticleIdList only: the ReferenceList entries (doi leak, PMC666, 111) must not appear
    assert rec.ids == [
        ExternalId("pmid", "26017442"),
        ExternalId("doi", "10.1038/nature14539"),
        ExternalId("pmcid", "PMC1234"),
    ]


def test_sparse_article_uses_elocation_doi_and_medline_date():
    [rec] = parse_articles(sets(SPARSE).encode(), "pubmed")
    assert rec.year == 2015 and rec.venue is None and rec.abstract == "Only text."
    assert rec.is_retracted is False and rec.authors == []
    assert rec.ids == [ExternalId("pmid", "5"), ExternalId("doi", "10.9999/elocation")]


def test_empty_set_and_book_articles_are_skipped():
    assert parse_articles(sets().encode(), "pubmed") == []
    assert parse_articles(sets("<PubmedBookArticle><x/></PubmedBookArticle>").encode(), "pubmed") == []


@pytest.mark.parametrize(
    "body",
    [
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]>'
        b"<PubmedArticleSet>&b;</PubmedArticleSet>",
        b"<html>maintenance</html",
        b"<html><body>captive portal</body></html>",
        b"<PubmedArticleSet><PubmedArticle>",
        b"<eFetchResult><x/></eFetchResult>",
    ],
)
def test_hostile_or_unreadable_bodies_are_source_unavailable(body):
    with pytest.raises(SourceUnavailable, match="unreadable response"):
        parse_articles(body, "pubmed")


async def test_get_uses_one_comma_list_get_per_200():
    seen: list[dict[str, list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        seen.append(parse_qs(request.url.query.decode()))
        return httpx.Response(200, text=sets())

    ids_ = [ExternalId("pmid", str(i)) for i in range(1, 451)]
    ids_ += [ExternalId("pmid", "pmid:0000001"), ExternalId("doi", "10.1/x"), ExternalId("pmid", "9 OR 1")]
    async with client_for(handler) as client:
        conn = PubMedConnector(client)
        assert await conn.get(ids_) == []
        assert await conn.get([ExternalId("doi", "10.1/x")]) == []
    assert [len(q["id"][0].split(",")) for q in seen] == [200, 200, 50]
    assert seen[0]["db"] == ["pubmed"] and seen[0]["retmode"] == ["xml"]
    assert "email" not in seen[0] and "tool" not in seen[0] and "api_key" not in seen[0]


async def test_search_then_efetch_and_params():
    seen: list[tuple[str, dict[str, list[str]]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        q = parse_qs(request.url.query.decode())
        seen.append((request.url.path.rsplit("/", 1)[-1], q))
        if request.url.path.endswith("esearch.fcgi"):
            return httpx.Response(200, text=json.dumps({"esearchresult": {"idlist": ["26017442", "5"]}}))
        return httpx.Response(200, text=sets(ARTICLE, SPARSE))

    async with client_for(handler) as client:
        out = await PubMedConnector(client).search(SearchQuery("deep learning", limit=2, year_from=2015, year_to=2016))
    assert [r.id("pmid") for r in out] == ["26017442", "5"]
    assert [s[0] for s in seen] == ["esearch.fcgi", "efetch.fcgi"]
    assert seen[0][1] == {
        "db": ["pubmed"],
        "term": ["deep learning"],
        "retmode": ["json"],
        "retmax": ["2"],
        "sort": ["relevance"],
        "datetype": ["pdat"],
        "mindate": ["2015"],
        "maxdate": ["2016"],
    }
    assert seen[1][1]["id"] == ["26017442,5"]


async def test_search_open_range_empty_idlist_and_empty_text():
    seen: list[dict[str, list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(parse_qs(request.url.query.decode()))
        return httpx.Response(200, text=json.dumps({"esearchresult": {"idlist": []}}))

    async with client_for(handler) as client:
        conn = PubMedConnector(client)
        assert await conn.search(SearchQuery(" ")) == []
        assert await conn.search(SearchQuery("x", year_from=2020)) == []
    assert len(seen) == 1  # an empty idlist means no efetch
    assert (seen[0]["mindate"], seen[0]["maxdate"]) == (["2020"], ["3000"])


@pytest.mark.parametrize("body", ["<html/>", "[]", '{"esearchresult": []}', '{"esearchresult": {"idlist": 5}}', "{"])
async def test_unreadable_esearch(body):
    async with client_for(lambda r: httpx.Response(200, text=body)) as client:
        with pytest.raises(SourceUnavailable, match="unreadable response"):
            await PubMedConnector(client).search(SearchQuery("x"))


ELINK = {
    "linksets": [
        {
            "dbfrom": "pubmed",
            "ids": ["26017442"],
            "linksetdbs": [
                {"dbto": "pubmed", "linkname": "other", "links": ["999"]},
                {"dbto": "pubmed", "linkname": "pubmed_pubmed_citedin", "links": ["11", "0011", "x", "22"]},
                {"dbto": "pubmed", "linkname": "pubmed_pubmed_refs", "links": ["33"]},
            ],
        }
    ]
}


async def test_citations_linknames_and_parsing():
    seen: list[dict[str, list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(parse_qs(request.url.query.decode()))
        return httpx.Response(200, text=json.dumps(ELINK))

    async with client_for(handler) as client:
        conn = PubMedConnector(client)
        assert await conn.citations(ExternalId("pmid", "26017442"), "in") == [
            ExternalId("pmid", "11"),
            ExternalId("pmid", "22"),
        ]
        assert await conn.citations(ExternalId("pmid", "26017442"), "out") == [ExternalId("pmid", "33")]
        assert await conn.citations(ExternalId("doi", "10.1/x"), "in") == []
        assert await conn.citations(ExternalId("pmid", "1,2"), "out") == []
    assert [s["linkname"][0] for s in seen] == ["pubmed_pubmed_citedin", "pubmed_pubmed_refs"]
    assert seen[0]["dbfrom"] == ["pubmed"] and seen[0]["retmode"] == ["json"] and seen[0]["id"] == ["26017442"]


@pytest.mark.parametrize(
    "body", ['{"linksets": [{"dbfrom": "pubmed", "ids": ["5"]}]}', '{"linksets": []}', '{"header": {}}']
)
async def test_no_links_is_empty(body):
    async with client_for(lambda r: httpx.Response(200, text=body)) as client:
        assert await PubMedConnector(client).citations(ExternalId("pmid", "5"), "in") == []


@pytest.mark.parametrize(
    "body", ["<html/>", "[]", '{"linksets": 5}', '{"linksets": [5]}', '{"linksets": [{"linksetdbs": [5]}]}']
)
async def test_unreadable_elink(body):
    async with client_for(lambda r: httpx.Response(200, text=body)) as client:
        with pytest.raises(SourceUnavailable, match="unreadable response"):
            await PubMedConnector(client).citations(ExternalId("pmid", "5"), "in")


async def test_error_statuses():
    err = "<eFetchResult><ERROR>ID list is empty! Possibly it has no correct IDs.</ERROR></eFetchResult>"
    async with client_for(lambda r: httpx.Response(400, text=err)) as client:
        conn = PubMedConnector(client)
        assert await conn.get([ExternalId("pmid", "5")]) == []
        assert await conn.citations(ExternalId("pmid", "5"), "in") == []
    async with client_for(lambda r: httpx.Response(404, text="nope")) as client:
        assert await PubMedConnector(client).get([ExternalId("pmid", "5")]) == []
    for status, text in ((400, "bad request"), (403, err)):
        async with client_for(lambda r, s=status, t=text: httpx.Response(s, text=t)) as client:
            conn = PubMedConnector(client)
            with pytest.raises(SourceUnavailable, match=f"HTTP {status}"):
                await conn.get([ExternalId("pmid", "5")])
            with pytest.raises(SourceUnavailable, match=f"HTTP {status}"):
                await conn.search(SearchQuery("x"))
            with pytest.raises(SourceUnavailable, match=f"HTTP {status}"):
                await conn.citations(ExternalId("pmid", "5"), "out")


async def test_capabilities():
    async with client_for(lambda r: httpx.Response(500)) as client:
        caps = PubMedConnector(client).capabilities()
    assert (caps.search, caps.get, caps.citations_in, caps.citations_out) == (True, True, True, True)


async def test_cache_keys_and_ttls():
    calls: list[tuple[str | None, object]] = []

    class Fake(PoliteClient):
        async def get(self, url, **kw):  # type: ignore[override]
            calls.append((kw["cache_key"], kw["ttl"]))
            body = (
                json.dumps({"esearchresult": {"idlist": ["5"]}})
                if "esearch" in url
                else ("{}" if "elink" in url else sets())
            )
            return Fetched(200, {}, body.encode(), url)

    conn = PubMedConnector(Fake())
    await conn.search(SearchQuery("x"))
    await conn.get([ExternalId("pmid", "5"), ExternalId("pmid", "6")])
    await conn.citations(ExternalId("pmid", "5"), "in")
    await conn.citations(ExternalId("pmid", "5"), "out")
    assert pm.KEY == "pubmed:v1"
    assert calls[0][0] and calls[0][0].startswith("pubmed:v1:search:") and calls[0][1] == TTL_SEARCH
    assert calls[1] == ("pubmed:v1:pmid:5", TTL_METADATA)  # the search's efetch of one ID shares the singleton key
    assert calls[2][0] and calls[2][0].startswith("pubmed:v1:get:") and calls[2][1] == TTL_METADATA
    assert calls[3] == ("pubmed:v1:count:in:5", TTL_COUNTS)
    assert calls[4] == ("pubmed:v1:refs:pmid:5", TTL_METADATA)


# ---- cassettes (recorded live, keyless) ----------------------------------------------------------------------------


@pytest.mark.vcr
async def test_search_attention(polite):
    recs = await PubMedConnector(polite).search(SearchQuery("attention is all you need", limit=3))
    assert recs and all(r.title and r.id("pmid") and r.source == "pubmed" for r in recs)


@pytest.mark.vcr
async def test_get_by_pmid(polite):
    [rec] = await PubMedConnector(polite).get([ExternalId("pmid", "26017442")])
    assert rec.title == "Deep learning" and rec.year == 2015 and rec.venue == "Nature"
    assert rec.id("pmid") == "26017442" and rec.id("doi") == "10.1038/nature14539"
    assert rec.authors and rec.authors[0].name == "Yann LeCun"
    assert rec.abstract and rec.abstract.startswith("Deep learning allows") and rec.is_retracted is False
    assert rec.ids == [ExternalId("pmid", "26017442"), ExternalId("doi", "10.1038/nature14539")]  # no reference leak


@pytest.mark.vcr
async def test_citers_in(polite):
    citers = await PubMedConnector(polite).citations(ExternalId("pmid", "23456789"), "in")
    assert citers and all(r.scheme == "pmid" for r in citers)


@pytest.mark.vcr
async def test_references_out(polite):
    refs = await PubMedConnector(polite).citations(ExternalId("pmid", "26017442"), "out")
    assert refs and all(r.scheme == "pmid" for r in refs)


@pytest.mark.vcr
async def test_not_found(polite):
    assert await PubMedConnector(polite).get([ExternalId("pmid", "999999999")]) == []
