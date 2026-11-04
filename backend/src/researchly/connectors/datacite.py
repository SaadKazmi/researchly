"""DataCite connector (research 2026-11-03 §5): DOI metadata, mainly arXiv's 10.48550 DOIs. Keyless."""

import re
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
from researchly.connectors.http import TTL_METADATA, Fetched, PoliteClient

BASE = "https://api.datacite.org"
KEY = "datacite:v1"

_ORCID = re.compile(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]")


def _orcid(creator: dict[str, Any]) -> str | None:
    for n in creator.get("nameIdentifiers") or []:
        if (n.get("nameIdentifierScheme") or "").upper() == "ORCID":
            tail = (n.get("nameIdentifier") or "").rstrip("/").rsplit("/", 1)[-1]
            if _ORCID.fullmatch(tail):
                return tail
    return None


def parse_item(attrs: dict[str, Any]) -> PaperRecord:
    doi = ids.doi(attrs.get("doi") or "")
    rec_ids = [ExternalId("doi", doi)] if doi else []
    for ident in attrs.get("identifiers") or []:
        if (ident.get("identifierType") or "").lower() == "arxiv" and (a := ids.arxiv(ident.get("identifier") or "")):
            rec_ids.append(ExternalId("arxiv", a))
            break
    abstract = next(
        (
            d["description"]
            for d in attrs.get("descriptions") or []
            if d.get("descriptionType") == "Abstract" and d.get("description")
        ),
        None,
    )
    year = attrs.get("publicationYear")
    publisher = attrs.get("publisher")
    return PaperRecord(
        source="datacite",
        ids=rec_ids,
        title=(attrs.get("titles") or [{}])[0].get("title") or "",
        abstract=abstract,
        year=year if isinstance(year, int) else None,
        authors=[Author(name=c["name"], orcid=_orcid(c)) for c in attrs.get("creators") or [] if c.get("name")],
        venue=publisher if isinstance(publisher, str) else None,
        license=(attrs.get("rightsList") or [{}])[0].get("rightsUri") or None,
    )


class DataCiteConnector:
    name = "datacite"

    def __init__(self, client: PoliteClient) -> None:
        self._client = client

    def capabilities(self) -> ConnectorCaps:
        return ConnectorCaps(search=False, get=True, citations_in=False, citations_out=False)

    async def search(self, query: SearchQuery) -> list[PaperRecord]:
        return []

    async def citations(self, id: ExternalId, direction: Literal["in", "out"]) -> list[ExternalId]:
        return []

    def _attrs(self, fetched: Fetched) -> dict[str, Any] | None:
        if fetched.status == 404:
            return None
        if fetched.status != 200:
            raise SourceUnavailable(self.name, f"HTTP {fetched.status}")
        try:
            attrs = fetched.json()["data"]["attributes"]
        except (ValueError, KeyError, TypeError) as exc:  # an HTML page, bad UTF-8, wrong envelope
            raise SourceUnavailable(self.name, "unreadable response") from exc
        if not isinstance(attrs, dict):
            raise SourceUnavailable(self.name, "unreadable response")
        return attrs

    @staticmethod
    def _dois(eids: list[ExternalId]) -> list[str]:
        out: list[str] = []
        for e in eids:
            n = ids.normalize(e)
            if n and n.scheme == "arxiv":
                out.append(ids.arxiv_doi(n.value))
            elif n and n.scheme == "doi":
                out.append(n.value)
        return list(dict.fromkeys(out))

    async def get(self, ids_: list[ExternalId]) -> list[PaperRecord]:
        recs: list[PaperRecord] = []
        for doi in self._dois(ids_):
            fetched = await self._client.get(
                f"{BASE}/dois/{quote(doi, safe='/:')}",
                source=self.name,
                cache_key=f"{KEY}:doi:{doi}",
                ttl=TTL_METADATA,
            )
            if (attrs := self._attrs(fetched)) is not None:
                try:
                    recs.append(parse_item(attrs))
                except (AttributeError, TypeError, KeyError) as exc:
                    raise SourceUnavailable(self.name, "unreadable response") from exc
        return recs
