"""Crossref connector (spec §8.1; research 2026-11-03 §3): DOI metadata, search and outgoing references. Keyless."""

import hashlib
import json
import re
from typing import Any, Literal
from urllib.parse import quote

from researchly.connectors import ids
from researchly.connectors.base import Author, ConnectorCaps, ExternalId, PaperRecord, SearchQuery, SourceUnavailable
from researchly.connectors.http import TTL_METADATA, TTL_SEARCH, Fetched, PoliteClient

BASE = "https://api.crossref.org"
LIST_BUCKET = "api.crossref.org/list"  # the list pool allows 1 rps; singletons 5 rps
# Changing SELECT changes what the cache holds: bump KEY ("crossref:v1" -> "crossref:v2") with it.
SELECT = "DOI,title,abstract,issued,author,container-title,type,is-referenced-by-count,license,updated-by,link"
KEY = "crossref:v1"

_ORCID = re.compile(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]")
_TAG = re.compile(r"<[^>]+>")


def singleton_key(doi: str) -> str:
    return f"{KEY}:doi:{doi}"


def strip_jats(abstract: str | None) -> str | None:
    text = " ".join(_TAG.sub(" ", abstract or "").split())
    return text or None


def _orcid(url: str | None) -> str | None:
    tail = (url or "").rstrip("/").rsplit("/", 1)[-1]
    return tail if _ORCID.fullmatch(tail) else None


def _year(item: dict[str, Any]) -> int | None:
    parts = ((item.get("issued") or {}).get("date-parts") or [[]])[0]
    return parts[0] if parts and isinstance(parts[0], int) else None


def _refs(item: dict[str, Any]) -> list[ExternalId]:
    parsed = (ids.doi(r.get("DOI") or "") for r in item.get("reference") or [])
    return [ExternalId("doi", d) for d in parsed if d]


def parse_item(item: dict[str, Any]) -> PaperRecord:
    authors = []
    for a in item.get("author") or []:
        name = a.get("name") or " ".join(p for p in (a.get("given"), a.get("family")) if p)
        if name:
            authors.append(Author(name=name, orcid=_orcid(a.get("ORCID"))))
    doi = ids.doi(item.get("DOI") or "")
    return PaperRecord(
        source="crossref",
        ids=[ExternalId("doi", doi)] if doi else [],
        title=(item.get("title") or [""])[0] or "",
        abstract=strip_jats(item.get("abstract")),
        year=_year(item),
        authors=authors,
        venue=(item.get("container-title") or [None])[0],
        venue_type=item.get("type"),
        cited_by=item.get("is-referenced-by-count"),
        license=next((lic["URL"] for lic in item.get("license") or [] if lic.get("URL")), None),
        is_retracted=any(u.get("type") == "retraction" for u in item.get("updated-by") or []),
        references=_refs(item),
    )


class CrossrefConnector:
    name = "crossref"

    def __init__(self, client: PoliteClient) -> None:
        self._client = client

    def capabilities(self) -> ConnectorCaps:
        return ConnectorCaps(search=True, get=True, citations_in=False, citations_out=True)  # in: counts only

    def _message(self, fetched: Fetched) -> Any:
        if fetched.status == 404:
            return None
        if fetched.status != 200:
            raise SourceUnavailable(self.name, f"HTTP {fetched.status}")
        try:
            message = fetched.json()["message"]
        except (ValueError, KeyError, TypeError) as exc:  # an HTML maintenance page, bad UTF-8, wrong envelope
            raise SourceUnavailable(self.name, "unreadable response") from exc
        if not isinstance(message, dict):
            raise SourceUnavailable(self.name, "unreadable response")
        return message

    async def search(self, query: SearchQuery) -> list[PaperRecord]:
        params: dict[str, str | int] = {
            "query.bibliographic": query.text,
            "rows": query.limit,
            "select": SELECT,
        }
        bounds = [f"{k}:{v}" for k, v in (("from-pub-date", query.year_from), ("until-pub-date", query.year_to)) if v]
        if bounds:
            params["filter"] = ",".join(bounds)
        key = f"{KEY}:search:" + hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
        fetched = await self._client.get(
            f"{BASE}/works", params=params, source=self.name, bucket=LIST_BUCKET, cache_key=key, ttl=TTL_SEARCH
        )
        message = self._message(fetched)
        try:
            return [parse_item(i) for i in (message or {}).get("items") or []]
        except (AttributeError, TypeError) as exc:  # a 200 whose items are not works
            raise SourceUnavailable(self.name, "unreadable response") from exc

    async def _singleton(self, doi: str) -> dict[str, Any] | None:
        fetched = await self._client.get(
            f"{BASE}/works/{quote(doi, safe='/:')}",
            source=self.name,
            cache_key=singleton_key(doi),
            ttl=TTL_METADATA,
        )
        return self._message(fetched)

    @staticmethod
    def _dois(eids: list[ExternalId]) -> list[str]:
        norm = (ids.normalize(e) for e in eids)
        return list(dict.fromkeys(n.value for n in norm if n and n.scheme == "doi"))

    async def get(self, ids_: list[ExternalId]) -> list[PaperRecord]:
        # singletons only: a filter batch is a list call at 1 rps, slower than N singletons at 5 rps
        items = [item for doi in self._dois(ids_) if (item := await self._singleton(doi))]
        try:
            return [parse_item(i) for i in items]
        except (AttributeError, TypeError) as exc:
            raise SourceUnavailable(self.name, "unreadable response") from exc

    async def citations(self, id: ExternalId, direction: Literal["in", "out"]) -> list[ExternalId]:
        if direction == "in" or not (dois := self._dois([id])):
            return []  # Crossref has citation counts only, no citing list
        item = await self._singleton(dois[0])  # the same request (and cache entry) as get()
        return _refs(item) if item else []
