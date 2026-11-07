"""bioRxiv/medRxiv connector (R-conn §2): get-only by 10.1101 DOI; the API has no search. Keyless.

The API answers HTTP 200 even for errors ({"messages": [{"status": "no posts found"}]}). Full text on
www.biorxiv.org is behind a Cloudflare challenge: `jatsxml` is kept as data, never fetched.
"""

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
from researchly.connectors.http import TTL_METADATA, PoliteClient

BASE = "https://api.biorxiv.org"
KEY = "biorxiv:v1"
SERVERS = ("biorxiv", "medrxiv")  # both use the 10.1101 prefix, so the server is found by asking
PREFIX = "10.1101/"
_VENUES = {"biorxiv": "bioRxiv", "medrxiv": "medRxiv"}


def _version(row: dict[str, Any]) -> int:
    try:
        return int(row.get("version") or 0)
    except (TypeError, ValueError):
        return 0


def _license(raw: Any) -> str | None:
    if not isinstance(raw, str) or raw in ("", "cc_no"):  # cc_no: no CC licence, all rights reserved
        return None
    return raw.replace("_", "-")  # cc_by_nc_nd -> cc-by-nc-nd, the form OpenAlex uses


def _str(raw: Any) -> str | None:
    return raw.strip() or None if isinstance(raw, str) else None


def parse_details(rows: list[Any], server: str) -> PaperRecord | None:
    versions = [r for r in rows if isinstance(r, dict) and ids.doi(str(r.get("doi") or ""))]
    if not versions:
        return None
    latest, first = max(versions, key=_version), min(versions, key=_version)
    doi = ids.doi(str(latest["doi"]))
    title = _str(latest.get("title"))
    if doi is None or title is None:
        return None
    year = str(first.get("date") or "")[:4]
    authors = str(latest.get("authors") or "").split(";")
    return PaperRecord(
        source="biorxiv",
        ids=[ExternalId("doi", doi)],
        title=title,
        abstract=_str(latest.get("abstract")),
        year=int(year) if year.isdigit() else None,
        authors=[Author(name=n.strip()) for n in authors if n.strip()],
        venue=_VENUES[server],
        venue_type="repository",
        is_oa=True,
        license=_license(latest.get("license")),
        extra={
            "server": server,
            "version": _version(latest),
            "category": _str(latest.get("category")),
            "jatsxml": _str(latest.get("jatsxml")),
            "published_doi": ids.doi(str(latest.get("published") or "")),  # "NA" until published
        },
    )


class BiorxivConnector:
    name = "biorxiv"

    def __init__(self, client: PoliteClient) -> None:
        self._client = client

    def capabilities(self) -> ConnectorCaps:
        return ConnectorCaps(search=False, get=True, citations_in=False, citations_out=False)

    async def search(self, query: SearchQuery) -> list[PaperRecord]:
        return []

    async def citations(self, id: ExternalId, direction: Literal["in", "out"]) -> list[ExternalId]:
        return []

    async def _rows(self, server: str, doi: str) -> list[Any]:
        fetched = await self._client.get(
            f"{BASE}/details/{server}/{quote(doi, safe='/:')}",
            source=self.name,
            cache_key=f"{KEY}:{server}:{doi}",
            ttl=TTL_METADATA,
        )
        if fetched.status == 404:
            return []
        if fetched.status != 200:
            raise SourceUnavailable(self.name, f"HTTP {fetched.status}")
        try:
            body = fetched.json()
            status = body["messages"][0]["status"]
            rows = body.get("collection") or []
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            raise SourceUnavailable(self.name, "unreadable response") from exc
        if status != "ok" or not isinstance(rows, list):
            return []
        return rows

    async def get(self, ids_: list[ExternalId]) -> list[PaperRecord]:
        dois = [n.value for e in ids_ if (n := ids.normalize(e)) and n.scheme == "doi" and n.value.startswith(PREFIX)]
        recs: list[PaperRecord] = []
        for doi in dict.fromkeys(dois):
            for server in SERVERS:
                if (rec := parse_details(await self._rows(server, doi), server)) is not None:
                    recs.append(rec)
                    break
        return recs
