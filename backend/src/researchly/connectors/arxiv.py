"""arXiv connector (spec §8.1; research 2026-11-03 §2, §9, §11): Atom search and ID lookup, no citations."""

import hashlib
import json
import re
from typing import Literal
from xml.etree.ElementTree import Element, ParseError  # exception and type only; parsing is defusedxml's

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

from researchly.connectors import ids
from researchly.connectors.base import (
    Author,
    ConnectorCaps,
    ExternalId,
    PaperRecord,
    SearchQuery,
    SourceUnavailable,
)
from researchly.connectors.http import TTL_METADATA, TTL_SEARCH, Fetched, PoliteClient

BASE = "https://export.arxiv.org/api/query"
BATCH = 50  # IDs per id_list call
MAX_RESULTS = 100
KEY = "arxiv:v1"

_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV = "{http://arxiv.org/schemas/atom}"
_WORDS = re.compile(r"[^\W_]+")
# Probed live 2026-11-03: `all:attention AND all:<w>` returns 0 results for exactly these words, so one of them
# in an AND query empties it (arXiv's index appears to drop them).
_UNSEARCHABLE = frozenset({"is", "are", "this", "that"})
_DOI_PREFIX = "10.48550/arxiv."


def singleton_key(arxiv_id: str) -> str:
    return f"{KEY}:arxiv:{arxiv_id}"


def _hash_key(prefix: str, params: dict[str, str | int]) -> str:
    return f"{KEY}:{prefix}:" + hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()


def search_expression(query: SearchQuery) -> str | None:
    """arXiv `search_query`: every word must match anywhere; None when the text holds no words."""
    words = [w for w in _WORDS.findall(query.text) if w.lower() not in _UNSEARCHABLE]
    if not words:
        return None
    expr = " AND ".join(f"all:{w}" for w in words)
    if query.year_from is not None or query.year_to is not None:
        lo, hi = query.year_from or 1900, query.year_to or 2100
        expr += f" AND submittedDate:[{lo}01010000 TO {hi}12312359]"
    return expr


def _text(entry: Element, path: str) -> str | None:
    node = entry.find(path)
    return " ".join("".join(node.itertext()).split()) or None if node is not None else None


def _parse_entry(entry: Element, source: str) -> PaperRecord | None:
    arxiv_id = ids.arxiv(_text(entry, f"{_ATOM}id") or "")
    if arxiv_id is None:
        return None
    found = [ExternalId("arxiv", arxiv_id), ExternalId("doi", ids.arxiv_doi(arxiv_id))]
    if (journal_doi := ids.doi(_text(entry, f"{_ARXIV}doi") or "")) and ExternalId("doi", journal_doi) not in found:
        found.append(ExternalId("doi", journal_doi))
    published = _text(entry, f"{_ATOM}published") or ""
    pdf = next((link.get("href") for link in entry.findall(f"{_ATOM}link") if link.get("title") == "pdf"), None)
    return PaperRecord(
        source=source,
        ids=found,
        title=_text(entry, f"{_ATOM}title") or "",
        abstract=_text(entry, f"{_ATOM}summary"),
        year=int(published[:4]) if published[:4].isdigit() else None,
        authors=[Author(name=n) for a in entry.findall(f"{_ATOM}author") if (n := _text(a, f"{_ATOM}name"))],
        oa_url=pdf,
        is_oa=True,
        extra={"categories": [t for c in entry.findall(f"{_ATOM}category") if (t := c.get("term"))]},
    )


def parse_feed(body: bytes, source: str) -> list[PaperRecord]:
    try:
        root = fromstring(body)
    except (DefusedXmlException, ParseError, ValueError) as exc:  # entity bombs, DTDs, HTML, truncation
        raise SourceUnavailable(source, "unreadable response") from exc
    if root.tag != f"{_ATOM}feed":
        raise SourceUnavailable(source, "unreadable response")
    out: list[PaperRecord] = []
    for entry in root.findall(f"{_ATOM}entry"):
        if "/api/errors" in (_text(entry, f"{_ATOM}id") or ""):  # arXiv reports errors as HTTP 200
            raise SourceUnavailable(source, "arXiv rejected the request")  # fixed text: errors reach prompts
        if rec := _parse_entry(entry, source):
            out.append(rec)
    return out


class ArxivConnector:
    name = "arxiv"

    def __init__(self, client: PoliteClient) -> None:
        self._client = client

    def capabilities(self) -> ConnectorCaps:
        return ConnectorCaps(search=True, get=True, citations_in=False, citations_out=False)

    def _records(self, fetched: Fetched) -> list[PaperRecord]:
        if fetched.status == 404:
            return []
        if fetched.status != 200:
            raise SourceUnavailable(self.name, f"HTTP {fetched.status}")
        return parse_feed(fetched.body, self.name)

    async def search(self, query: SearchQuery) -> list[PaperRecord]:
        if (expr := search_expression(query)) is None:
            return []
        params: dict[str, str | int] = {
            "search_query": expr,
            "sortBy": "relevance",
            "max_results": min(query.limit, MAX_RESULTS),
        }
        fetched = await self._client.get(
            BASE, params=params, source=self.name, cache_key=_hash_key("search", params), ttl=TTL_SEARCH
        )
        return self._records(fetched)

    @staticmethod
    def _arxiv_ids(eids: list[ExternalId]) -> list[str]:
        out: list[str] = []
        for eid in eids:
            value = eid.value
            if eid.scheme == "doi":
                value = ids.doi(value) or ""
                if not value.startswith(_DOI_PREFIX):
                    continue
                value = value[len(_DOI_PREFIX) :]
            elif eid.scheme != "arxiv":
                continue
            if (arxiv_id := ids.arxiv(value)) and arxiv_id not in out:
                out.append(arxiv_id)
        return out

    async def get(self, ids_: list[ExternalId]) -> list[PaperRecord]:
        wanted = self._arxiv_ids(ids_)
        out: list[PaperRecord] = []
        for i in range(0, len(wanted), BATCH):
            chunk = wanted[i : i + BATCH]
            params: dict[str, str | int] = {"id_list": ",".join(chunk), "max_results": len(chunk)}
            key = singleton_key(chunk[0]) if len(chunk) == 1 else _hash_key("get", params)
            fetched = await self._client.get(BASE, params=params, source=self.name, cache_key=key, ttl=TTL_METADATA)
            out += self._records(fetched)
        return out

    async def citations(self, id: ExternalId, direction: Literal["in", "out"]) -> list[ExternalId]:
        return []  # arXiv has no citation data
