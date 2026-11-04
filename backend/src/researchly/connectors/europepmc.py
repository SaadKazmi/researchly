"""Europe PMC connector (research 2026-11-03 §6): search, OR-query lookup, citations both ways. Keyless."""

import hashlib
import html
import json
import re
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
from researchly.connectors.http import TTL_COUNTS, TTL_METADATA, TTL_SEARCH, Fetched, PoliteClient

BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"
KEY = "europepmc:v1"
BATCH = 25  # IDs per OR query (unverified size cap upstream)
MAX_PAGE = 1000

_TAGS = re.compile(r"<[^>]+>")
_ORCID = re.compile(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]")


def _hash_key(prefix: str, params: dict[str, str | int]) -> str:
    return f"{KEY}:{prefix}:" + hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()


def _text(raw: Any, sep: str = " ") -> str | None:
    """Plain text of a markup string; `sep` replaces each tag (inline tags in titles must not add spaces)."""
    if not isinstance(raw, str):
        return None
    return " ".join(html.unescape(_TAGS.sub(sep, raw)).split()) or None


def _author(raw: Any) -> Author | None:
    if not isinstance(raw, dict):
        return None
    name = _text(raw.get("fullName")) or _text(raw.get("collectiveName"))
    if not name:
        return None
    auth = raw.get("authorId")
    orcid = None
    if isinstance(auth, dict) and auth.get("type") == "ORCID" and isinstance(auth.get("value"), str):
        orcid = m.group(0) if (m := _ORCID.search(auth["value"])) else None
    return Author(name=name, orcid=orcid)


def parse_result(raw: dict[str, Any], source: str) -> PaperRecord | None:
    found: list[ExternalId] = []
    for scheme, fn, key in (("pmid", ids.pmid, "pmid"), ("pmcid", ids.pmcid, "pmcid"), ("doi", ids.doi, "doi")):
        if isinstance(raw.get(key), str) and (value := fn(raw[key])):
            found.append(ExternalId(scheme, value))  # type: ignore[arg-type]
    if not found:
        return None
    year = str(raw.get("pubYear") or "")
    types = (raw.get("pubTypeList") or {}).get("pubType")
    journal = ((raw.get("journalInfo") or {}).get("journal") or {}).get("title")
    authors = ((raw.get("authorList") or {}).get("author")) or []
    cited = raw.get("citedByCount")
    return PaperRecord(
        source=source,
        ids=found,
        title=(_text(raw.get("title"), "") or "").removesuffix("."),
        abstract=_text(raw.get("abstractText")),
        year=int(year) if year.isdigit() else None,
        authors=[a for r in authors if (a := _author(r))],
        venue=_text(journal),
        cited_by=cited if isinstance(cited, int) else None,
        is_oa=raw["isOpenAccess"] == "Y" if raw.get("isOpenAccess") in ("Y", "N") else None,
        license=raw.get("license") if isinstance(raw.get("license"), str) else None,
        is_retracted="Retracted Publication" in types if isinstance(types, list) else None,
    )


def _dict(fetched: Fetched, source: str) -> dict[str, Any]:
    try:
        body = fetched.json()
    except ValueError as exc:
        raise SourceUnavailable(source, "unreadable response") from exc
    if not isinstance(body, dict):
        raise SourceUnavailable(source, "unreadable response")
    return body


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


class EuropePmcConnector:
    name = "europepmc"

    def __init__(self, client: PoliteClient) -> None:
        self._client = client

    def capabilities(self) -> ConnectorCaps:
        return ConnectorCaps(search=True, get=True, citations_in=True, citations_out=True)

    def _parse(self, fetched: Fetched) -> list[PaperRecord]:
        if fetched.status == 404:
            return []
        if fetched.status != 200:
            raise SourceUnavailable(self.name, f"HTTP {fetched.status}")
        try:
            rows = _dict(fetched, self.name).get("resultList", {}).get("result", [])
            return [r for row in rows if (r := parse_result(row, self.name))]
        except (AttributeError, TypeError) as exc:  # resultList not an object, a row not an object
            raise SourceUnavailable(self.name, "unreadable response") from exc

    async def search(self, query: SearchQuery) -> list[PaperRecord]:
        text = query.text.strip()
        if not text:
            return []
        if query.year_from is not None or query.year_to is not None:
            text += f" AND PUB_YEAR:[{query.year_from or 1000} TO {query.year_to or 3000}]"
        params: dict[str, str | int] = {
            "query": text,
            "format": "json",
            "resultType": "core",
            "pageSize": max(1, min(query.limit, MAX_PAGE)),
        }
        fetched = await self._client.get(
            f"{BASE}/search", params=params, source=self.name, cache_key=_hash_key("search", params), ttl=TTL_SEARCH
        )
        return self._parse(fetched)

    @staticmethod
    def _terms(eids: list[ExternalId]) -> list[tuple[ExternalId, str]]:
        out: list[tuple[ExternalId, str]] = []
        for eid in eids:
            norm = ids.normalize(eid)
            if norm is None or norm in [n for n, _ in out]:
                continue
            if norm.scheme == "doi":
                out.append((norm, f"DOI:{_quote(norm.value)}"))
            elif norm.scheme == "pmid":
                out.append((norm, f"(EXT_ID:{norm.value} AND SRC:MED)"))
            elif norm.scheme == "pmcid":
                out.append((norm, f"PMCID:{norm.value}"))
        return out

    async def get(self, ids_: list[ExternalId]) -> list[PaperRecord]:
        terms = self._terms(ids_)
        out: list[PaperRecord] = []
        for i in range(0, len(terms), BATCH):
            chunk = terms[i : i + BATCH]
            params: dict[str, str | int] = {
                "query": " OR ".join(t for _, t in chunk),
                "format": "json",
                "resultType": "core",
                "pageSize": len(chunk) * 2,  # a DOI can match both the MED record and a preprint
            }
            if len(chunk) == 1:
                eid = chunk[0][0]
                key = f"{KEY}:{eid.scheme}:{eid.value}"
            else:
                key = _hash_key("get", params)
            fetched = await self._client.get(
                f"{BASE}/search", params=params, source=self.name, cache_key=key, ttl=TTL_METADATA
            )
            out += self._parse(fetched)
        return out

    async def citations(self, id: ExternalId, direction: Literal["in", "out"]) -> list[ExternalId]:
        norm = ids.normalize(id)
        if norm is None or norm.scheme != "pmid":
            return []
        inbound = direction == "in"
        endpoint, outer, inner = (
            ("citations", "citationList", "citation")
            if inbound
            else (
                "references",
                "referenceList",
                "reference",
            )
        )
        fetched = await self._client.get(
            f"{BASE}/MED/{norm.value}/{endpoint}",
            params={"format": "json", "pageSize": MAX_PAGE},  # ponytail: first 1000 rows only, no paging
            source=self.name,
            cache_key=f"{KEY}:count:in:{norm.value}" if inbound else f"{KEY}:refs:pmid:{norm.value}",
            ttl=TTL_COUNTS if inbound else TTL_METADATA,  # citers keep growing, a reference list is fixed
        )
        if fetched.status == 404:
            return []
        if fetched.status != 200:
            raise SourceUnavailable(self.name, f"HTTP {fetched.status}")
        try:
            rows = _dict(fetched, self.name).get(outer, {}).get(inner, [])
            found = [ids.pmid(str(r.get("id"))) for r in rows if r.get("source") == "MED"]
        except (AttributeError, TypeError) as exc:
            raise SourceUnavailable(self.name, "unreadable response") from exc
        return list(dict.fromkeys(ExternalId("pmid", p) for p in found if p))
