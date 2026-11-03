"""Hugging Face Papers connector (research 2026-11-03 §8): upvotes, code and models for arXiv papers. Keyless."""

import hashlib
import json
from typing import Any, Literal
from urllib.parse import quote

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

BASE = "https://huggingface.co/api"
KEY = "hf:v1"
SEARCH_BUCKET = "huggingface.co/search"  # 50 per 5 min, a separate pool from the api's 500

_EXTRA = ("upvotes", "githubRepo", "githubStars", "ai_keywords")


def parse_paper(paper: dict[str, Any]) -> PaperRecord | None:
    """None when the row has no parseable arXiv id: HF ids are arXiv ids, nothing else identifies the paper."""
    arxiv = ids.arxiv(paper["id"]) if isinstance(paper.get("id"), str) else None
    if not arxiv:
        return None
    published = paper.get("publishedAt")
    year = int(published[:4]) if isinstance(published, str) and published[:4].isdigit() else None
    return PaperRecord(
        source="hf",
        ids=[ExternalId("arxiv", arxiv), ExternalId("doi", ids.arxiv_doi(arxiv))],
        title=paper.get("title") or "",
        abstract=paper.get("summary") or None,
        year=year,
        authors=[Author(name=a["name"]) for a in paper.get("authors") or [] if a.get("name")],
        extra={k: paper[k] for k in _EXTRA if paper.get(k) is not None},
    )


class HfConnector:
    name = "hf"

    def __init__(self, client: PoliteClient) -> None:
        self._client = client

    def capabilities(self) -> ConnectorCaps:
        return ConnectorCaps(search=True, get=True, citations_in=False, citations_out=False)

    async def citations(self, id: ExternalId, direction: Literal["in", "out"]) -> list[ExternalId]:
        return []

    def _json(self, fetched: Fetched) -> Any:
        if fetched.status == 404:
            return None
        if fetched.status != 200:
            raise SourceUnavailable(self.name, f"HTTP {fetched.status}")
        try:
            return fetched.json()
        except ValueError as exc:  # an HTML page, truncated JSON, bad UTF-8
            raise SourceUnavailable(self.name, "unreadable response") from exc

    async def search(self, query: SearchQuery) -> list[PaperRecord]:
        params: dict[str, str | int] = {"q": query.text, "limit": query.limit}
        key = f"{KEY}:search:" + hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
        fetched = await self._client.get(
            f"{BASE}/papers/search",
            params=params,
            source=self.name,
            bucket=SEARCH_BUCKET,
            cache_key=key,
            ttl=TTL_SEARCH,
        )
        rows = self._json(fetched)
        if rows is None:
            return []
        if not isinstance(rows, list):
            raise SourceUnavailable(self.name, "unreadable response")
        try:
            return [rec for row in rows if (rec := parse_paper(row.get("paper") or {})) is not None]
        except (KeyError, AttributeError, TypeError) as exc:
            raise SourceUnavailable(self.name, "unreadable response") from exc

    @staticmethod
    def _arxiv_ids(eids: list[ExternalId]) -> list[str]:
        out: list[str] = []
        for e in eids:
            n = ids.normalize(e)
            if n and n.scheme == "arxiv":
                out.append(n.value)
            elif (
                n
                and n.scheme == "doi"
                and n.value.startswith("10.48550/arxiv.")
                and (a := ids.arxiv(n.value.removeprefix("10.48550/arxiv.")))
            ):
                out.append(a)
        return list(dict.fromkeys(out))

    async def get(self, ids_: list[ExternalId]) -> list[PaperRecord]:
        recs: list[PaperRecord] = []
        for arxiv in self._arxiv_ids(ids_):
            fetched = await self._client.get(
                f"{BASE}/papers/{quote(arxiv, safe='/')}",
                source=self.name,
                cache_key=f"{KEY}:paper:{arxiv}",
                ttl=TTL_METADATA,
            )
            paper = self._json(fetched)
            if paper is None:  # not every paper is on HF
                continue
            if not isinstance(paper, dict):
                raise SourceUnavailable(self.name, "unreadable response")
            try:
                rec = parse_paper(paper)
            except (AttributeError, TypeError) as exc:
                raise SourceUnavailable(self.name, "unreadable response") from exc
            if rec:
                recs.append(rec)
        return recs
