import httpx
import pytest

from researchly.connectors.http import PoliteClient
from researchly.connectors.robots import Robots
from researchly.services.fulltext import FullTextFetcher, Target

pytestmark = pytest.mark.anyio

ARXIV_PAGE = (
    b"<html><body><article class='ltx_document'><section class='ltx_section'><h2 class='ltx_title'>1 Intro</h2>"
    b"<div class='ltx_para'><p class='ltx_p'>From HTML.</p></div></section></article></body></html>"
)
JATS = b"<article><body><sec><title>Intro</title><p>From JATS.</p></sec></body></article>"


def fetcher_for(routes: dict[str, httpx.Response], seen: list[str], markdown="## Intro\n\nFrom PDF.\n"):
    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return routes.get(str(request.url), httpx.Response(404))
        return routes.get(str(request.url), httpx.Response(404, headers={"content-type": "text/html"}))

    async def sleep(seconds: float) -> None:
        return None

    client = PoliteClient(
        transport=httpx.MockTransport(handler), sleep=sleep, rand=lambda: 0.0, limits={"default": (0.0, 1)}
    )
    converted: list[bytes] = []

    def convert(path):
        converted.append(path.read_bytes())
        return markdown

    return client, FullTextFetcher(client, Robots(client), convert=convert), converted


def html(body: bytes) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=body)


async def test_arxiv_html_is_the_first_choice():
    seen: list[str] = []
    client, fetcher, _ = fetcher_for({"https://arxiv.org/html/1706.03762": html(ARXIV_PAGE)}, seen)
    async with client:
        full = await fetcher.fetch(Target(arxiv="1706.03762", pmcid="PMC1", pdf_urls=("https://x.org/p.pdf",)))
    assert full is not None
    assert full.source == "arxiv_html" and full.sections[0].text == "From HTML."
    assert not any("europepmc" in u or "x.org" in u for u in seen)


async def test_old_style_arxiv_ids_keep_their_slash():
    seen: list[str] = []
    client, fetcher, _ = fetcher_for({"https://arxiv.org/html/hep-th/9901001": html(ARXIV_PAGE)}, seen)
    async with client:
        full = await fetcher.fetch(Target(arxiv="hep-th/9901001"))
    assert full is not None and full.source == "arxiv_html"


async def test_missing_html_falls_back_to_jats():
    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC5334499/fullTextXML"
    routes = {url: httpx.Response(200, headers={"content-type": "application/xml"}, content=JATS)}
    client, fetcher, _ = fetcher_for(routes, [])
    async with client:
        full = await fetcher.fetch(Target(arxiv="1706.03762", pmcid="PMC5334499"))
    assert full is not None
    assert full.source == "jats" and full.sections[0].text == "From JATS."


async def test_pdf_tier_needs_a_pdf_type_and_magic_bytes():
    routes = {
        "https://landing.org/paper": html(b"<html>a landing page</html>"),
        "https://fake.org/p.pdf": httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"<html>"),
        "https://real.org/p.pdf": httpx.Response(
            200, headers={"content-type": "application/octet-stream"}, content=b"%PDF-1.7 body"
        ),
    }
    client, fetcher, converted = fetcher_for(routes, [])
    async with client:
        full = await fetcher.fetch(
            Target(pdf_urls=("https://landing.org/paper", "https://fake.org/p.pdf", "https://real.org/p.pdf"))
        )
    assert full is not None
    assert full.source == "pdf" and full.sections[0].text == "From PDF."
    assert converted == [b"%PDF-1.7 body"]  # only the real PDF reached Docling


async def test_robots_disallow_skips_the_fetch():
    routes = {"https://arxiv.org/robots.txt": httpx.Response(200, text="User-agent: *\nDisallow: /html\n")}
    seen: list[str] = []
    client, fetcher, _ = fetcher_for(routes, seen)
    async with client:
        assert await fetcher.fetch(Target(arxiv="1706.03762")) is None
    assert "https://arxiv.org/html/1706.03762" not in seen


async def test_a_pdf_that_docling_cannot_read_is_no_full_text():
    routes = {
        "https://real.org/p.pdf": httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF-")
    }
    client, fetcher, _ = fetcher_for(routes, [])

    def broken(path):
        raise RuntimeError("corrupt xref table")

    fetcher._convert = broken  # an untrusted PDF may crash the converter
    async with client:
        assert await fetcher.fetch(Target(pdf_urls=("https://real.org/p.pdf",))) is None


async def test_nothing_available_gives_none():
    client, fetcher, _ = fetcher_for({}, [])
    async with client:
        assert await fetcher.fetch(Target()) is None


@pytest.mark.vcr
async def test_live_arxiv_html_and_jats(polite):
    fetcher = FullTextFetcher(polite, Robots(polite))
    html_full = await fetcher.fetch(Target(arxiv="1706.03762"))
    assert html_full is not None
    assert html_full.source == "arxiv_html" and len(html_full.sections) >= 10
    assert any(s.path[-1] == "3.2.1 Scaled Dot-Product Attention" for s in html_full.sections)
    jats_full = await fetcher.fetch(Target(pmcid="PMC5334499"))
    assert jats_full is not None
    assert jats_full.source == "jats" and jats_full.license and len(jats_full.sections) >= 3
