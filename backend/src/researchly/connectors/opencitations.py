"""OpenCitations Index v2 connector (research 2026-11-03 §4): DOI-to-DOI citation lists only. Keyless."""

from typing import Any, Literal, cast
from urllib.parse import quote

from researchly.connectors import ids
from researchly.connectors.base import (
    ConnectorCaps,
    ExternalId,
    PaperRecord,
    Scheme,
    SearchQuery,
    SourceUnavailable,
    TooManyResults,
)
from researchly.connectors.http import TTL_COUNTS, TTL_METADATA, Fetched, PoliteClient

BASE = "https://api.opencitations.net/index/v2"
KEY = "opencitations:v1"
MAX_ROWS = 5000  # the API has no paging: a famous paper's list streams megabytes
MAX_BYTES = 20_000_000

_PREFERENCE = ("doi", "pmid", "openalex")  # the schemes a row is reduced to, best first


def parse_multi_id(raw: str) -> ExternalId | None:
    """One ExternalId from `"omid:br/1 pmid:26017442 doi:10.1038/x openalex:W1"`: DOI, then PMID, then OpenAlex."""
    found: dict[str, ExternalId] = {}
    for token in (raw or "").split():
        scheme, _, value = token.partition(":")
        if (
            scheme in _PREFERENCE
            and scheme not in found
            and (n := ids.normalize(ExternalId(cast(Scheme, scheme), value)))
        ):
            found[scheme] = n
    return next((found[s] for s in _PREFERENCE if s in found), None)


class OpenCitationsConnector:
    name = "opencitations"

    def __init__(self, client: PoliteClient) -> None:
        self._client = client

    def capabilities(self) -> ConnectorCaps:
        return ConnectorCaps(search=False, get=False, citations_in=True, citations_out=True)

    async def search(self, query: SearchQuery) -> list[PaperRecord]:
        return []

    async def get(self, ids_: list[ExternalId]) -> list[PaperRecord]:
        return []

    def _rows(self, fetched: Fetched) -> list[Any]:
        if fetched.status != 200:
            raise SourceUnavailable(self.name, f"HTTP {fetched.status}")
        try:
            rows = fetched.json()
        except ValueError as exc:
            raise SourceUnavailable(self.name, "unreadable response") from exc
        if not isinstance(rows, list):
            raise SourceUnavailable(self.name, "unreadable response")
        return rows

    async def citations(self, id: ExternalId, direction: Literal["in", "out"]) -> list[ExternalId]:
        norm = ids.normalize(id)
        if norm is None or norm.scheme not in ("doi", "pmid"):
            return []
        ref = f"{norm.scheme}:{norm.value}"
        path = quote(ref, safe="/:")
        inbound = direction == "in"
        count_ep, list_ep, field = (
            ("citation-count", "citations", "citing") if inbound else ("reference-count", "references", "cited")
        )
        counted = await self._client.get(
            f"{BASE}/{count_ep}/{path}", source=self.name, cache_key=f"{KEY}:{count_ep}:{ref}", ttl=TTL_COUNTS
        )
        try:
            count = int(self._rows(counted)[0]["count"])
        except (IndexError, KeyError, TypeError, ValueError) as exc:  # [], a row without count, "abc"
            raise SourceUnavailable(self.name, "unreadable response") from exc
        if count > MAX_ROWS:
            raise TooManyResults(self.name, f"{count} {'citations' if inbound else 'references'}")
        if count <= 0:
            return []
        fetched = await self._client.get(
            f"{BASE}/{list_ep}/{path}",
            source=self.name,
            cache_key=f"{KEY}:{list_ep}:{ref}",
            ttl=TTL_COUNTS if inbound else TTL_METADATA,  # citers keep growing, a reference list is fixed
            max_bytes=MAX_BYTES,
        )
        rows = self._rows(fetched)
        if len(rows) > MAX_ROWS:
            raise TooManyResults(self.name, f"{len(rows)} rows")
        try:
            found = (parse_multi_id(r[field]) for r in rows)
            return list(dict.fromkeys(e for e in found if e))
        except (KeyError, TypeError, AttributeError) as exc:
            raise SourceUnavailable(self.name, "unreadable response") from exc
