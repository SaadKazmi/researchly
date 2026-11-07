"""General and code web search through the local SearXNG sidecar (spec F2.1, §8.1; ADR 0010).

Titles and snippets are untrusted page text (spec §19.1): shown and stored, never put into a prompt directly.
"""

import html
import re
from dataclasses import dataclass, field
from typing import Any, Literal, TypeGuard
from urllib.parse import urlsplit

from researchly.connectors import ids
from researchly.connectors.base import ExternalId, SourceUnavailable
from researchly.connectors.sidecar import SidecarClient

CATEGORIES = {"general": "general", "code": "it"}  # SearXNG categories: brave, mojeek, yahoo; then github
_TAGS = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class WebResult:
    url: str
    title: str
    snippet: str | None
    engines: tuple[str, ...]
    published: str | None  # the engine's page date (Brave: the crawl date); never a paper's year
    ids: tuple[ExternalId, ...] = ()  # scholarly IDs named by the URL or a `doi` field, for identity resolution


@dataclass
class WebSearchOutcome:
    results: list[WebResult]
    unresponsive: dict[str, str] = field(default_factory=dict)  # engine -> reason ("timeout", "CAPTCHA", ...)


def _plain(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    return " ".join(html.unescape(_TAGS.sub(" ", raw)).split()) or None


def _web_url(url: Any) -> TypeGuard[str]:  # narrows `url` for pyright
    try:
        return isinstance(url, str) and urlsplit(url).scheme in ("http", "https")
    except ValueError:
        return False


def parse_result(row: Any) -> WebResult | None:
    if not isinstance(row, dict) or not _web_url(url := row.get("url")):
        return None
    found: list[ExternalId] = []
    if (eid := ids.from_url(url)) is not None:
        found.append(eid)
    if isinstance(row.get("doi"), str) and (doi := ids.doi(row["doi"])):
        found.append(ExternalId("doi", doi))
    if arxiv_id := next((e.value for e in found if e.scheme == "arxiv"), None):
        found.append(ExternalId("doi", ids.arxiv_doi(arxiv_id)))
    engines = row.get("engines")
    published = row.get("publishedDate")
    return WebResult(
        url=url,
        title=_plain(row.get("title")) or url,
        snippet=_plain(row.get("content")),
        engines=tuple(e for e in engines if isinstance(e, str)) if isinstance(engines, list) else (),
        published=published if isinstance(published, str) else None,
        ids=tuple(dict.fromkeys(found)),
    )


async def web_search(
    client: SidecarClient,
    query: str,
    *,
    category: Literal["general", "code"] = "general",
    limit: int = 20,
) -> WebSearchOutcome:
    text = query.strip()
    if not text:
        return WebSearchOutcome([])
    fetched = await client.get("/search", {"q": text, "format": "json", "categories": CATEGORIES[category]})
    if fetched.status != 200:
        raise SourceUnavailable(client.source, f"HTTP {fetched.status}")
    try:
        body = fetched.json()
        results = [r for row in body.get("results") or [] if (r := parse_result(row))]
        unresponsive = {str(engine): str(reason) for engine, reason in body.get("unresponsive_engines") or []}
    except (ValueError, AttributeError, TypeError) as exc:
        raise SourceUnavailable(client.source, "unreadable response") from exc
    return WebSearchOutcome(results[:limit], unresponsive)
