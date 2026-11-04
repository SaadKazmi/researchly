from urllib.parse import parse_qs

import httpx
import pytest

from researchly.connectors import arxiv as ax
from researchly.connectors.arxiv import ArxivConnector, parse_feed, search_expression
from researchly.connectors.base import ExternalId, SearchQuery, SourceUnavailable
from researchly.connectors.http import PoliteClient

pytestmark = pytest.mark.anyio

NS = (
    'xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom" '
    'xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"'
)

ENTRY = """
<entry>
  <id>http://arxiv.org/abs/1706.03762v7</id>
  <published>2017-06-12T17:57:34Z</published>
  <title>Attention  Is
    All You Need</title>
  <summary>  The dominant
   sequence   models.
  </summary>
  <author><name>Ashish Vaswani</name></author>
  <author><name>Noam Shazeer</name></author>
  <arxiv:journal_ref>NeurIPS 2017</arxiv:journal_ref>
  <arxiv:doi>10.5555/3295222.3295349</arxiv:doi>
  <link href="http://arxiv.org/abs/1706.03762v7" rel="alternate" type="text/html"/>
  <link title="pdf" href="http://arxiv.org/pdf/1706.03762v7" rel="related" type="application/pdf"/>
  <arxiv:primary_category term="cs.CL"/>
  <category term="cs.CL"/>
  <category term="cs.LG"/>
</entry>
"""


def feed(*entries: str) -> str:
    return f'<?xml version="1.0"?><feed {NS}>{"".join(entries)}</feed>'


def client_for(handler) -> PoliteClient:
    return PoliteClient(transport=httpx.MockTransport(handler), limits={"default": (0.0, 1)})


def test_parse_entry():
    [rec] = parse_feed(feed(ENTRY).encode(), "arxiv")
    assert rec.source == "arxiv"
    assert rec.title == "Attention Is All You Need"
    assert rec.abstract == "The dominant sequence models."
    assert rec.year == 2017
    assert [a.name for a in rec.authors] == ["Ashish Vaswani", "Noam Shazeer"]
    assert rec.venue is None  # journal_ref is free text ("Phys. Rev. Lett. 99, 123 (2007)"), not a venue name
    assert rec.ids == [
        ExternalId("arxiv", "1706.03762"),
        ExternalId("doi", "10.48550/arxiv.1706.03762"),
        ExternalId("doi", "10.5555/3295222.3295349"),
    ]
    assert rec.oa_url == "http://arxiv.org/pdf/1706.03762v7" and rec.is_oa is True
    assert rec.extra["categories"] == ["cs.CL", "cs.LG"]


def test_sparse_entry_and_empty_feed():
    sparse = "<entry><id>http://arxiv.org/abs/hep-th/9901001v2</id><title>T</title></entry>"
    [rec] = parse_feed(feed(sparse).encode(), "arxiv")
    assert rec.id("arxiv") == "hep-th/9901001" and rec.year is None and rec.venue is None and rec.oa_url is None
    assert [i.scheme for i in rec.ids] == ["arxiv", "doi"]
    assert parse_feed(feed().encode(), "arxiv") == []


def test_error_entry_raises_a_fixed_message():
    err = (
        "<entry><id>https://arxiv.org/api/errors#incorrect_id_format_for_abc</id>"
        "<title>Error</title><summary>incorrect id format for abc</summary></entry>"
    )
    with pytest.raises(SourceUnavailable, match="^arxiv: arXiv rejected the request$"):
        parse_feed(feed(err).encode(), "arxiv")


@pytest.mark.parametrize(
    "body",
    [
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><feed>&b;</feed>',
        b"<html>maintenance</html",
        b"<html><body>captive portal</body></html>",
        b'<feed xmlns="http://www.w3.org/2005/Atom"><entry>',
    ],
)
def test_hostile_or_unreadable_bodies_are_source_unavailable(body):
    with pytest.raises(SourceUnavailable, match="unreadable response"):
        parse_feed(body, "arxiv")


def test_search_expression():
    # "is" is dropped: arXiv returns nothing for an AND query containing it
    assert search_expression(SearchQuery("Attention, is all-you need!")) == (
        "all:Attention AND all:all AND all:you AND all:need"
    )
    assert search_expression(SearchQuery("Is this that")) is None
    assert search_expression(SearchQuery("graphs", year_from=2015, year_to=2017)) == (
        "all:graphs AND submittedDate:[201501010000 TO 201712312359]"
    )
    assert str(search_expression(SearchQuery("graphs", year_from=2015))).endswith("[201501010000 TO 210012312359]")
    assert str(search_expression(SearchQuery("graphs", year_to=2017))).endswith("[190001010000 TO 201712312359]")
    assert search_expression(SearchQuery("!!! ???")) is None


async def test_search_params_and_no_words_means_no_request():
    seen: list[dict[str, list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(parse_qs(request.url.query.decode()))
        return httpx.Response(200, text=feed(ENTRY))

    async with client_for(handler) as client:
        conn = ArxivConnector(client)
        assert await conn.search(SearchQuery("?!")) == []
        out = await conn.search(SearchQuery("graph nets", limit=3))
    assert len(out) == 1 and len(seen) == 1
    assert seen[0] == {"search_query": ["all:graph AND all:nets"], "sortBy": ["relevance"], "max_results": ["3"]}


async def test_get_maps_ids_and_batches_in_one_call():
    queries: list[dict[str, list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        queries.append(parse_qs(request.url.query.decode()))
        return httpx.Response(200, text=feed(ENTRY))

    ids_ = [
        ExternalId("arxiv", "http://arxiv.org/abs/1706.03762v7"),
        ExternalId("doi", "10.48550/arXiv.1706.03762"),  # same paper again: deduplicated
        ExternalId("doi", "10.48550/arXiv.hep-th/9901001"),
        ExternalId("arxiv", "hep-th/9901001v1"),
        ExternalId("doi", "10.1038/nature14539"),  # not an arXiv DOI: skipped
        ExternalId("pmid", "5"),
        ExternalId("arxiv", "nonsense"),
    ]
    async with client_for(handler) as client:
        await ArxivConnector(client).get(ids_)
        assert await ArxivConnector(client).get([ExternalId("pmid", "5")]) == []
    assert len(queries) == 1  # nothing to look up means no request
    assert queries[0]["id_list"] == ["1706.03762,hep-th/9901001"] and queries[0]["max_results"] == ["2"]


async def test_get_chunks_of_fifty():
    sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sizes.append(len(parse_qs(request.url.query.decode())["id_list"][0].split(",")))
        return httpx.Response(200, text=feed())

    ids_ = [ExternalId("arxiv", f"2001.{i:05d}") for i in range(1, 121)]
    async with client_for(handler) as client:
        assert await ArxivConnector(client).get(ids_) == []
    assert sizes == [50, 50, 20]


async def test_errors_and_not_found():
    def gone(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="<html>nope</html>")

    def bad(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad")

    def html(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>portal</html>")

    async with client_for(gone) as client:
        assert await ArxivConnector(client).get([ExternalId("arxiv", "1706.03762")]) == []
        assert await ArxivConnector(client).search(SearchQuery("x")) == []
    async with client_for(bad) as client:
        with pytest.raises(SourceUnavailable, match="HTTP 400"):
            await ArxivConnector(client).search(SearchQuery("x"))
    async with client_for(html) as client:
        with pytest.raises(SourceUnavailable, match="unreadable response"):
            await ArxivConnector(client).get([ExternalId("arxiv", "1706.03762")])


async def test_no_citations_and_capabilities():
    async with client_for(lambda r: httpx.Response(500)) as client:
        conn = ArxivConnector(client)
        assert await conn.citations(ExternalId("arxiv", "1706.03762"), "in") == []
        assert await conn.citations(ExternalId("arxiv", "1706.03762"), "out") == []
    caps = conn.capabilities()
    assert (caps.search, caps.get, caps.citations_in, caps.citations_out) == (True, True, False, False)


async def test_query_text_is_tokenized_never_interpolated():
    raw: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        raw.append(request.url.raw_path.decode())
        return httpx.Response(200, text=feed())

    async with client_for(handler) as client:
        await ArxivConnector(client).search(SearchQuery('a&b=c "d" OR e'))
    assert "&b=c" not in raw[0] and "all%3Aa+AND+all%3Ab+AND+all%3Ac" in raw[0].replace("%20", "+")


def test_cache_keys_are_versioned():
    assert ax.singleton_key("1706.03762") == "arxiv:v1:arxiv:1706.03762"


# ---- cassettes (recorded live, keyless) ----------------------------------------------------------------------------


@pytest.mark.vcr
async def test_search_attention(polite):
    recs = await ArxivConnector(polite).search(SearchQuery("attention is all you need", limit=3))
    # arXiv's relevance sort over an AND of `all:` terms does not put the 2017 paper in the top 3 (live, 2026-11-03)
    assert len(recs) == 3
    assert [r.id("arxiv") for r in recs] == ["2512.19700", "2112.05993", "2410.05767"]
    assert all(r.authors and r.abstract and r.year and r.id("doi") and r.is_oa for r in recs)
    assert recs[2].title.startswith("Grounding is All You Need") and recs[2].year == 2024


@pytest.mark.vcr
async def test_get_by_arxiv_id(polite):
    [rec] = await ArxivConnector(polite).get([ExternalId("arxiv", "1706.03762")])
    assert rec.title == "Attention Is All You Need" and rec.year == 2017
    assert rec.id("arxiv") == "1706.03762" and rec.id("doi") == "10.48550/arxiv.1706.03762"
    assert rec.authors[0].name == "Ashish Vaswani" and len(rec.authors) == 8
    assert rec.abstract and rec.abstract.startswith("The dominant sequence transduction models")
    assert rec.oa_url and "1706.03762" in rec.oa_url and rec.is_oa is True
    assert "cs.CL" in rec.extra["categories"]


@pytest.mark.vcr
async def test_get_old_style_id(polite):
    [rec] = await ArxivConnector(polite).get([ExternalId("arxiv", "hep-th/9901001")])
    assert rec.id("arxiv") == "hep-th/9901001" and rec.id("doi") == "10.48550/arxiv.hep-th/9901001"
    assert rec.year == 1999 and rec.title


@pytest.mark.vcr
async def test_not_found(polite):
    assert await ArxivConnector(polite).get([ExternalId("arxiv", "9999.99999")]) == []
