import httpx
import pytest

from researchly.connectors.base import ExternalId, SourceUnavailable
from researchly.connectors.http import HostLimit
from researchly.connectors.sidecar import SidecarClient
from researchly.services.web_search import WebResult, parse_result, web_search

pytestmark = pytest.mark.anyio

SAMPLE = {
    "query": "attention is all you need",
    "number_of_results": None,
    "results": [
        {
            "url": "https://arxiv.org/abs/1706.03762v7",
            "title": "[1706.03762] Attention Is All You Need",
            "content": "The dominant <b>sequence transduction</b> models &amp; more",
            "engine": "brave",
            "engines": ["brave", "mojeek"],
            "publishedDate": "2023-08-02T00:00:00",
            "score": 2.0,
            "category": "general",
        },
        {"url": "https://oadoi.org/10.1038/nature14539", "title": "Deep learning", "content": "", "engines": ["yahoo"]},
        {
            "url": "https://example.org/blog/transformers",
            "title": "A <i>blog</i> post",
            "content": "Plain text",
            "engines": ["mojeek"],
            "doi": "10.5555/Example.1",
            "publishedDate": None,
        },
        {"url": "javascript:alert(1)", "title": "bad", "engines": ["brave"]},
        {"title": "no url", "engines": ["brave"]},
    ],
    "answers": [],
    "corrections": [],
    "infoboxes": [],
    "suggestions": [],
    "unresponsive_engines": [["yahoo", "timeout"], ["brave", "Suspended: CAPTCHA"]],
}


def client_for(handler) -> SidecarClient:
    return SidecarClient(
        "http://127.0.0.1:8766", source="web", transport=httpx.MockTransport(handler), limit=HostLimit(0.0, 2)
    )


async def test_results_map_to_web_results_with_ids_and_engine_failures():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search"
        assert dict(request.url.params) == {"q": "attention is all you need", "format": "json", "categories": "general"}
        return httpx.Response(200, json=SAMPLE)

    client = client_for(handler)
    outcome = await web_search(client, "  attention is all you need ")
    await client.aclose()
    assert [r.url for r in outcome.results] == [
        "https://arxiv.org/abs/1706.03762v7",
        "https://oadoi.org/10.1038/nature14539",
        "https://example.org/blog/transformers",
    ]
    first, second, third = outcome.results
    assert first == WebResult(
        url="https://arxiv.org/abs/1706.03762v7",
        title="[1706.03762] Attention Is All You Need",
        snippet="The dominant sequence transduction models & more",
        engines=("brave", "mojeek"),
        published="2023-08-02T00:00:00",  # a crawl date, never a paper's year
        ids=(ExternalId("arxiv", "1706.03762"), ExternalId("doi", "10.48550/arxiv.1706.03762")),
    )
    assert second.ids == (ExternalId("doi", "10.1038/nature14539"),) and second.snippet is None
    assert third.title == "A blog post" and third.ids == (ExternalId("doi", "10.5555/example.1"),)
    assert third.published is None
    assert outcome.unresponsive == {"yahoo": "timeout", "brave": "Suspended: CAPTCHA"}


async def test_code_search_asks_the_it_category_and_limit_truncates():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["categories"] == "it"
        return httpx.Response(200, json=SAMPLE)

    client = client_for(handler)
    outcome = await web_search(client, "transformer pytorch", category="code", limit=1)
    await client.aclose()
    assert len(outcome.results) == 1


async def test_an_empty_query_makes_no_request():
    seen = []
    client = client_for(lambda r: seen.append(r) or httpx.Response(200, json=SAMPLE))
    assert (await web_search(client, "   ")).results == []
    await client.aclose()
    assert seen == []


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(403, text="Forbidden"), "HTTP 403"),
        (httpx.Response(200, text="<html>not json</html>"), "unreadable response"),
        (httpx.Response(200, json=["not", "an", "object"]), "unreadable response"),
        (httpx.Response(200, json={"results": [], "unresponsive_engines": [["only-one"]]}), "unreadable response"),
    ],
)
async def test_bad_answers_are_unavailable(response, message):
    client = client_for(lambda r: response)
    with pytest.raises(SourceUnavailable, match=message):
        await web_search(client, "x")
    await client.aclose()


def test_parse_result_rejects_rows_without_a_web_url():
    assert parse_result({"url": "javascript:alert(1)"}) is None
    assert parse_result({"url": "http://[::1"}) is None
    assert parse_result("not a row") is None
    assert parse_result({"url": "https://example.org/"}) == WebResult(
        url="https://example.org/", title="https://example.org/", snippet=None, engines=(), published=None
    )
