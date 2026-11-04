"""Full text for one paper, best source first (spec §9.1): arXiv HTML, Europe PMC JATS, then a PDF through Docling."""

import asyncio
import logging
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

from researchly.connectors.base import ConnectorError
from researchly.connectors.http import Fetched, PoliteClient
from researchly.connectors.robots import Robots
from researchly.ingest.arxiv_html import parse_arxiv_html
from researchly.ingest.document import FullText
from researchly.ingest.jats import parse_jats
from researchly.ingest.pdf import convert_pdf, markdown_sections

log = logging.getLogger(__name__)

HTML_TYPES = frozenset({"text/html"})
XML_TYPES = frozenset({"application/xml", "text/xml"})
PDF_TYPES = frozenset({"application/pdf", "application/octet-stream"})  # some repositories send octet-stream
MAX_HTML = 5_000_000  # spec §19.3
MAX_PDF = 50_000_000
EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"


@dataclass(frozen=True)
class Target:
    arxiv: str | None = None
    pmcid: str | None = None
    pdf_urls: tuple[str, ...] = ()


def _markdown(path: Path) -> str:
    return convert_pdf(path).markdown


class FullTextFetcher:
    def __init__(self, client: PoliteClient, robots: Robots, convert: Callable[[Path], str] = _markdown) -> None:
        self._client, self._robots, self._convert = client, robots, convert

    async def fetch(self, target: Target) -> FullText | None:
        if target.arxiv and (full := await self._arxiv_html(target.arxiv)):
            return full
        if target.pmcid and (full := await self._jats(target.pmcid)):
            return full
        for url in target.pdf_urls:
            if full := await self._pdf(url):
                return full
        return None

    async def _get(self, url: str, *, source: str, accept: frozenset[str], max_bytes: int) -> Fetched | None:
        try:
            fetched = await self._client.get(url, source=source, accept=accept, max_bytes=max_bytes)
        except ConnectorError as exc:  # outage, quota, too big, a landing page instead of a paper
            log.info("full text skipped: %s", exc)
            return None
        return fetched if fetched.status == 200 else None

    async def _arxiv_html(self, arxiv_id: str) -> FullText | None:
        url = f"https://arxiv.org/html/{quote(arxiv_id, safe='/')}"  # old-style ids keep their slash
        if not await self._robots.allowed(url):
            return None
        fetched = await self._get(url, source="arxiv.org", accept=HTML_TYPES, max_bytes=MAX_HTML)
        return parse_arxiv_html(fetched.body) if fetched else None

    async def _jats(self, pmcid: str) -> FullText | None:
        url = f"{EPMC}/{quote(pmcid, safe='')}/fullTextXML"  # a REST API: not crawled, no robots.txt
        fetched = await self._get(url, source="europepmc", accept=XML_TYPES, max_bytes=MAX_HTML)
        return parse_jats(fetched.body) if fetched else None

    async def _pdf(self, url: str) -> FullText | None:
        host = urlsplit(url).hostname or ""
        if not host or not await self._robots.allowed(url):
            return None
        fetched = await self._get(url, source=host, accept=PDF_TYPES, max_bytes=MAX_PDF)
        if fetched is None or b"%PDF-" not in fetched.body[:1024]:
            return None
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "paper.pdf"
            path.write_bytes(fetched.body)
            try:
                markdown = await asyncio.to_thread(self._convert, path)
            except Exception:  # an untrusted PDF may crash the converter: no full text, never a failed ingest
                log.warning("PDF conversion failed for %s", url, exc_info=True)
                return None
        sections = markdown_sections(markdown)
        return FullText("pdf", sections) if sections else None
