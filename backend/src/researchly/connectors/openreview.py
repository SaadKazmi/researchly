"""OpenReview connector (R-conn §1): venue decisions for ML papers through /notes/search. Keyless.

Only search answers anonymously; note and forum lookups (`/notes`) return a 403 challenge, so reviews are out
of reach and never attempted. `content.authorids` holds raw author emails: it is never read.
"""

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any, Literal

from researchly.connectors import ids
from researchly.connectors.base import (
    Author,
    ConnectorCaps,
    ExternalId,
    PaperRecord,
    SearchQuery,
    SourceUnavailable,
)
from researchly.connectors.http import TTL_COUNTS, PoliteClient

BASE = "https://api2.openreview.net"
KEY = "openreview:v1"
Decision = Literal["oral", "spotlight", "poster", "accepted", "not_accepted"]
_TIER: dict[str, Decision] = {"oral": "oral", "spotlight": "spotlight", "poster": "poster"}
_TIER_WORD = re.compile(r"\b(oral|spotlight|poster)\b", re.I)
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_MAX_MS = 4_102_444_800_000  # 2100-01-01: anything later is junk


def _hash_key(prefix: str, params: dict[str, str | int]) -> str:
    return f"{KEY}:{prefix}:" + hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()


def decision(venue: str) -> Decision:
    low = venue.lower()
    if low.startswith("submitted to") or "withdrawn" in low or "reject" in low:
        return "not_accepted"
    if match := _TIER_WORD.search(venue):
        return _TIER[match.group(1).lower()]
    return "accepted"


def _value(content: dict[str, Any], key: str) -> Any:
    entry = content.get(key)
    return entry.get("value") if isinstance(entry, dict) else None


def parse_note(raw: Any) -> PaperRecord | None:
    if not isinstance(raw, dict) or not isinstance(content := raw.get("content"), dict):
        return None
    domain = raw.get("domain")
    if not isinstance(domain, str) or domain == "DBLP.org" or domain.startswith("OpenReview.net/"):
        return None  # imports and archive copies carry no venue decision
    title, venue = _value(content, "title"), _value(content, "venue")
    if not isinstance(title, str) or not title.strip() or not isinstance(venue, str) or not venue.strip():
        return None
    year = None
    if match := _YEAR.search(venue):
        year = int(match.group(0))
    elif isinstance(pdate := raw.get("pdate"), int) and 0 < pdate < _MAX_MS:
        year = datetime.fromtimestamp(pdate / 1000, UTC).year
    found: list[ExternalId] = []
    if isinstance(link := _value(content, "html"), str) and (eid := ids.from_url(link)) is not None:
        found.append(eid)
        if eid.scheme == "arxiv":
            found.append(ExternalId("doi", ids.arxiv_doi(eid.value)))
    authors, abstract, venueid = _value(content, "authors"), _value(content, "abstract"), _value(content, "venueid")
    license_ = raw.get("license")
    return PaperRecord(
        source="openreview",
        ids=found,
        title=" ".join(title.split()),
        abstract=abstract.strip() or None if isinstance(abstract, str) else None,
        year=year,
        authors=[Author(name=a.strip()) for a in authors if isinstance(a, str) and a.strip()]
        if isinstance(authors, list)
        else [],
        license=license_ if isinstance(license_, str) else None,
        extra={
            "openreview_id": raw.get("forum") or raw.get("id"),
            "openreview_venue": venue.strip(),
            "openreview_venueid": venueid if isinstance(venueid, str) else None,
            "openreview_decision": decision(venue),
        },
    )


class OpenReviewConnector:
    name = "openreview"

    def __init__(self, client: PoliteClient) -> None:
        self._client = client

    def capabilities(self) -> ConnectorCaps:
        return ConnectorCaps(search=False, get=False, citations_in=False, citations_out=False)

    async def search(self, query: SearchQuery) -> list[PaperRecord]:
        return []  # term search is loose (R-conn §1): decisions come from by_title only

    async def get(self, ids_: list[ExternalId]) -> list[PaperRecord]:
        return []  # /notes?id= answers a 403 challenge

    async def citations(self, id: ExternalId, direction: Literal["in", "out"]) -> list[ExternalId]:
        return []

    async def by_title(self, title: str, year: int | None = None) -> PaperRecord | None:
        from researchly.services.identity import fuzzy_title_ok, normalize_title  # lazy: identity imports connectors

        norm = normalize_title(title)
        if not fuzzy_title_ok(norm):
            return None
        params: dict[str, str | int] = {
            "term": " ".join(title.split()),
            "content": "title",
            "source": "forum",
            "limit": 5,
        }
        fetched = await self._client.get(
            f"{BASE}/notes/search",
            source=self.name,
            params=params,
            cache_key=_hash_key("title", params),
            ttl=TTL_COUNTS,  # decisions land after submission: re-check weekly
        )
        if fetched.status != 200:
            raise SourceUnavailable(self.name, f"HTTP {fetched.status}")
        try:
            notes = fetched.json()["notes"]
        except (ValueError, KeyError, TypeError) as exc:
            raise SourceUnavailable(self.name, "unreadable response") from exc
        if not isinstance(notes, list):
            raise SourceUnavailable(self.name, "unreadable response")
        for raw in notes:
            rec = parse_note(raw)
            if rec is None or normalize_title(rec.title) != norm:
                continue
            if year is not None and rec.year is not None and not year - 1 <= rec.year <= year + 2:
                continue  # a venue trails its preprint by up to two years
            return rec
        return None
