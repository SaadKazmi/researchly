"""OpenAlex connector (spec §8.1; research 2026-11-03 §1): works, references, citers and the keyless budget guard."""

import hashlib
import json
import re
from datetime import UTC, date, datetime
from typing import Any, Literal
from urllib.parse import quote

from researchly.connectors import ids
from researchly.connectors.base import (
    Author,
    ConnectorCaps,
    ExternalId,
    PaperRecord,
    QuotaExhausted,
    SearchQuery,
    SourceUnavailable,
)
from researchly.connectors.http import TTL_METADATA, TTL_SEARCH, Fetched, PoliteClient

BASE = "https://api.openalex.org"
SEARCHES_PER_DAY = 50  # a search costs 10 credits of the 1000-credit ($0.10) keyless day
LOW_BUDGET = 0.2  # remaining/limit at or below this: paid calls stop
CITERS_CAP = 1000
BATCH = 100  # max OR values in one filter

# Changing SELECT changes what the cache holds: bump KEY ("openalex:v1" -> "openalex:v2") with it.
SELECT = (
    "id,doi,title,publication_year,abstract_inverted_index,authorships,primary_location,best_oa_location,"
    "open_access,locations,ids,cited_by_count,fwci,citation_normalized_percentile,is_retracted,"
    "referenced_works,topics"
)
KEY = "openalex:v1"

_ORCID = re.compile(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]")
_AUTHOR = re.compile(r"A\d+")


def _today() -> date:
    return datetime.now(UTC).date()


def _last_segment(url: str | None, pattern: re.Pattern[str]) -> str | None:
    tail = (url or "").rstrip("/").rsplit("/", 1)[-1]
    return tail if pattern.fullmatch(tail) else None


def singleton_key(kind: str, value: str) -> str:
    return f"{KEY}:{kind}:{value}"


def rebuild_abstract(index: dict[str, list[int]] | None) -> str | None:
    if not index:
        return None
    words = {pos: word for word, positions in index.items() for pos in positions}
    return " ".join(words[i] for i in sorted(words))


def _work_ids(work: dict[str, Any]) -> list[ExternalId]:
    found: list[ExternalId | None] = []
    if oa_id := ids.openalex(work.get("id") or ""):
        found.append(ExternalId("openalex", oa_id))
    if doi := ids.doi(work.get("doi") or ""):
        found.append(ExternalId("doi", doi))
    ext = work.get("ids") or {}
    if pmid := ids.pmid(ext.get("pmid") or ""):
        found.append(ExternalId("pmid", pmid))
    if pmcid := ids.pmcid(ext.get("pmcid") or ""):
        found.append(ExternalId("pmcid", pmcid))
    for loc in work.get("locations") or []:
        url = loc.get("landing_page_url") or ""
        if "arxiv.org/abs/" in url and (arxiv := ids.arxiv(url)):
            found.append(ExternalId("arxiv", arxiv))
            found.append(ExternalId("doi", ids.arxiv_doi(arxiv)))
            break
    out: list[ExternalId] = []
    for eid in found:
        if eid and eid not in out:
            out.append(eid)
    return out


def _refs(work: dict[str, Any]) -> list[ExternalId]:
    parsed = (ids.openalex(url) for url in work.get("referenced_works") or [])
    return [ExternalId("openalex", w) for w in parsed if w]


def parse_work(work: dict[str, Any]) -> PaperRecord:
    primary = work.get("primary_location") or {}
    best = work.get("best_oa_location") or {}
    oa = work.get("open_access") or {}
    source = primary.get("source") or {}
    authors = [
        Author(
            name=a.get("display_name") or "",
            orcid=_last_segment(a.get("orcid"), _ORCID),
            openalex_id=_last_segment(a.get("id"), _AUTHOR),
        )
        for authorship in work.get("authorships") or []
        if (a := authorship.get("author") or {})
    ]
    return PaperRecord(
        source="openalex",
        ids=_work_ids(work),
        title=work.get("title") or "",
        abstract=rebuild_abstract(work.get("abstract_inverted_index")),
        year=work.get("publication_year"),
        authors=authors,
        venue=source.get("display_name"),
        venue_type=source.get("type"),
        cited_by=work.get("cited_by_count"),
        fwci=work.get("fwci"),
        pct=(work.get("citation_normalized_percentile") or {}).get("value"),
        oa_url=best.get("pdf_url") or oa.get("oa_url"),
        is_oa=oa.get("is_oa"),
        license=best.get("license") or primary.get("license"),
        is_retracted=work.get("is_retracted"),
        references=_refs(work),
        extra={"topics": [t["display_name"] for t in work.get("topics") or [] if t.get("display_name")]},
    )


def _cache_key(prefix: str, params: dict[str, str | int]) -> str:
    return f"{KEY}:{prefix}:" + hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()


class OpenAlexConnector:
    name = "openalex"

    def __init__(self, client: PoliteClient) -> None:
        self._client = client
        self._remaining: float | None = None
        self._limit: float | None = None
        self._budget_day = _today()  # the UTC day the budget headers were last read
        self._search_day = _today()
        self._searches = 0

    @property
    def budget_low(self) -> bool:
        if self._budget_day != _today():
            self._remaining = None  # the budget reset at midnight UTC: the old reading says nothing now
        if self._remaining is None or not self._limit:
            return False  # no budget headers seen yet (or none sent): the server enforces the hard cap
        return self._remaining / self._limit <= LOW_BUDGET

    def capabilities(self) -> ConnectorCaps:
        return ConnectorCaps(search=True, get=True, citations_in=True, citations_out=True)

    async def _fetch(self, path: str, **kwargs: Any) -> Fetched:
        fetched = await self._client.get(f"{BASE}{path}", source=self.name, **kwargs)
        try:
            self._remaining = float(fetched.headers["x-ratelimit-remaining-usd"])
            self._limit = float(fetched.headers["x-ratelimit-limit-usd"])
            self._budget_day = _today()
        except (KeyError, ValueError):
            pass  # keep the last known budget; a cached or header-less answer says nothing new
        return fetched

    def _guard(self) -> None:
        if self.budget_low:
            raise QuotaExhausted(self.name, "OpenAlex daily budget low")

    def _json(self, fetched: Fetched) -> Any:
        if fetched.status == 404:
            return None
        if fetched.status != 200:
            raise SourceUnavailable(self.name, f"HTTP {fetched.status}")
        try:
            return fetched.json()
        except ValueError as exc:  # JSONDecodeError (an HTML maintenance page) and bad UTF-8
            raise SourceUnavailable(self.name, "unreadable response") from exc

    async def _list(self, params: dict[str, str | int], *, prefix: str, ttl: Any) -> list[dict[str, Any]]:
        fetched = await self._fetch("/works", params=params, cache_key=_cache_key(prefix, params), ttl=ttl)
        body = self._json(fetched)
        return (body or {}).get("results") or []

    async def search(self, query: SearchQuery) -> list[PaperRecord]:
        self._guard()
        today = _today()
        if today != self._search_day:
            self._search_day, self._searches = today, 0
        if (
            self._searches >= SEARCHES_PER_DAY
        ):  # ponytail: in-memory counter, resets on restart; the server-side budget is the hard cap
            raise QuotaExhausted(self.name, "OpenAlex daily budget low")
        self._searches += 1
        params: dict[str, str | int] = {
            "search.title_and_abstract": query.text,
            "per-page": min(query.limit, 100),
            "select": SELECT,
        }
        if query.year_from is not None and query.year_to is not None:
            params["filter"] = f"publication_year:{query.year_from}-{query.year_to}"
        elif query.year_from is not None:
            params["filter"] = f"publication_year:>{query.year_from - 1}"
        elif query.year_to is not None:
            params["filter"] = f"publication_year:<{query.year_to + 1}"
        return [parse_work(w) for w in await self._list(params, prefix="search", ttl=TTL_SEARCH)]

    @staticmethod
    def _lookups(eids: list[ExternalId]) -> list[tuple[str, str]]:
        """(kind, value) pairs OpenAlex can look up, deduplicated; arXiv goes through its DataCite DOI."""
        out: list[tuple[str, str]] = []
        for eid in eids:
            norm = ids.normalize(eid)
            if norm is None:
                continue
            pair = (
                ("doi", ids.arxiv_doi(norm.value))
                if norm.scheme == "arxiv"
                else (norm.scheme, norm.value)
                if norm.scheme in ("doi", "pmid", "openalex")
                else None
            )
            if pair and pair not in out:
                out.append(pair)
        return out

    async def _singleton(self, kind: str, value: str, select: str = SELECT) -> dict[str, Any] | None:
        ref = quote(value if kind == "openalex" else f"{kind}:{value}", safe="/:")
        key = singleton_key(kind, value) if select == SELECT else f"{KEY}:select={select}:{kind}:{value}"
        fetched = await self._fetch(f"/works/{ref}", params={"select": select}, cache_key=key, ttl=TTL_METADATA)
        return self._json(fetched)

    async def get(self, ids_: list[ExternalId]) -> list[PaperRecord]:
        lookups = self._lookups(ids_)
        works: list[dict[str, Any]] = []
        for kind in ("doi", "pmid", "openalex"):
            values = [v for k, v in lookups if k == kind]
            # a "," or "|" would split a filter value; a lone ID is a free singleton, a filter costs a credit
            batchable = [v for v in values if "," not in v and "|" not in v]
            if len(batchable) < 2 or self.budget_low:
                batchable = []
            for i in range(0, len(batchable), BATCH):
                chunk = batchable[i : i + BATCH]
                params: dict[str, str | int] = {
                    "filter": f"{kind}:{'|'.join(chunk)}",
                    "per-page": BATCH,
                    "select": SELECT,
                }
                works += await self._list(params, prefix="batch", ttl=TTL_METADATA)
            for value in values:
                if value not in batchable and (work := await self._singleton(kind, value)):
                    works.append(work)
        return [parse_work(w) for w in works]

    async def _work_id(self, eid: ExternalId) -> str | None:
        if not (lookups := self._lookups([eid])):
            return None
        kind, value = lookups[0]
        if kind == "openalex":
            return value
        work = await self._singleton(kind, value, select="id")
        return ids.openalex(work.get("id") or "") if work else None

    async def citations(self, id: ExternalId, direction: Literal["in", "out"]) -> list[ExternalId]:
        if direction == "out":
            if not (lookups := self._lookups([id])):
                return []
            kind, value = lookups[0]
            work = await self._singleton(kind, value)  # the full record already has referenced_works
            return _refs(work) if work else []
        self._guard()
        work_id = await self._work_id(id)
        if work_id is None:
            return []
        out: list[ExternalId] = []
        page = 1
        while len(out) < CITERS_CAP:
            if page > 1:
                self._guard()  # the budget may have dropped while paging
            params: dict[str, str | int] = {
                "filter": f"cites:{work_id}",
                "per-page": 200,
                "page": page,
                "select": "id,doi",
            }
            rows = await self._list(params, prefix="cites", ttl=TTL_SEARCH)
            out += [ExternalId("openalex", w) for r in rows if (w := ids.openalex(r.get("id") or ""))]
            if len(rows) < 200:
                break
            page += 1
        return out[:CITERS_CAP]
