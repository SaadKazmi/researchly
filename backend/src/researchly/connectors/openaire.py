"""OpenAIRE Graph v1 connector (R-conn §4): secondary search and PID lookup for EU repositories. Keyless.

60 calls an hour anonymously (docs; the header claims 7,199). Deduplicated records merge unrelated reposts, so
IDs, year, licence and open access come from the one instance that holds the paper's ID; title, abstract, authors
and the citation count stay at the top level, and a merged record's count can include reposts (ruling 13).
"""

import hashlib
import html
import json
import logging
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
from researchly.connectors.http import TTL_METADATA, TTL_SEARCH, Fetched, PoliteClient

BASE = "https://api.openaire.eu/graph/v1"
KEY = "openaire:v1"
_TAGS = re.compile(r"<[^>]+>")
_ORCID = re.compile(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]")
logger = logging.getLogger(__name__)


def _hash_key(prefix: str, params: dict[str, str | int]) -> str:
    return f"{KEY}:{prefix}:" + hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()


def _text(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    return " ".join(html.unescape(_TAGS.sub(" ", raw)).split()) or None


def _pids(raw: Any) -> list[ExternalId]:
    out: list[ExternalId] = []
    for p in raw if isinstance(raw, list) else []:
        if not isinstance(p, dict) or not isinstance(value := p.get("value"), str):
            continue
        scheme = str(p.get("scheme") or "").lower()
        if scheme == "doi" and (d := ids.doi(value)):
            out.append(ExternalId("doi", d))
        elif scheme == "arxiv" and (a := ids.arxiv(value)):
            out += [ExternalId("arxiv", a), ExternalId("doi", ids.arxiv_doi(a))]
        elif scheme == "pmid" and (m := ids.pmid(value)):
            out.append(ExternalId("pmid", m))
        elif scheme == "pmc" and (c := ids.pmcid(value)):
            out.append(ExternalId("pmcid", c))
    return out


def _instance_ids(instance: dict[str, Any]) -> list[ExternalId]:
    return list(dict.fromkeys(_pids(instance.get("pids")) + _pids(instance.get("alternateIdentifiers"))))


def _author(raw: Any) -> tuple[int, Author] | None:
    if not isinstance(raw, dict) or not (name := _text(raw.get("fullName"))):
        return None
    pid = raw.get("pid")
    ident = pid.get("id") if isinstance(pid, dict) else None
    orcid = None
    if isinstance(ident, dict) and ident.get("scheme") == "orcid" and isinstance(ident.get("value"), str):
        orcid = m.group(0) if (m := _ORCID.search(ident["value"])) else None  # orcid_pending is ignored
    rank = raw.get("rank")
    return (rank if isinstance(rank, int) else 1_000_000, Author(name=name, orcid=orcid))


def parse_result(raw: Any, want: set[ExternalId] | None = None) -> PaperRecord | None:
    if not isinstance(raw, dict) or not (title := _text(raw.get("mainTitle"))):
        return None
    merged = "dedup" in str(raw.get("id") or "")
    instances = [(i, found) for i in raw.get("instances") or [] if isinstance(i, dict) and (found := _instance_ids(i))]
    if want is not None:
        chosen = next(((i, found) for i, found in instances if want & set(found)), None)
    else:
        chosen = min(instances, key=lambda pair: str(pair[0].get("publicationDate") or "9999"), default=None)
    if chosen is None:
        return None  # no instance carries the paper's ID: nothing here can be trusted for it
    instance, found = chosen
    date = str(instance.get("publicationDate") or "")[:4]
    access = instance.get("accessRight")
    label = access.get("label") if isinstance(access, dict) else None
    is_oa = label == "OPEN" if isinstance(label, str) else None
    urls = [u for u in instance.get("urls") or [] if isinstance(u, str) and u.startswith(("https://", "http://"))]
    container = raw.get("container")
    descriptions = raw.get("descriptions")
    indicators = raw.get("indicators")
    impact = indicators.get("citationImpact") if isinstance(indicators, dict) else None
    cited = impact.get("citationCount") if isinstance(impact, dict) else None
    licence = instance.get("license")
    authors = sorted((a for a in map(_author, raw.get("authors") or []) if a), key=lambda pair: pair[0])
    return PaperRecord(
        source="openaire",
        ids=sorted(found),
        title=title,
        abstract=_text(descriptions[0]) if isinstance(descriptions, list) and descriptions else None,
        year=int(date) if date.isdigit() else None,
        authors=[a for _, a in authors],
        venue=None if merged or not isinstance(container, dict) else _text(container.get("name")),
        cited_by=cited if isinstance(cited, int) else None,
        oa_url=urls[0] if is_oa and urls else None,
        is_oa=is_oa,
        license=licence if isinstance(licence, str) and licence else None,
        extra={"openaire_id": raw.get("id")},
    )


class OpenAireConnector:
    name = "openaire"

    def __init__(self, client: PoliteClient) -> None:
        self._client = client

    def capabilities(self) -> ConnectorCaps:
        return ConnectorCaps(search=True, get=True, citations_in=False, citations_out=False)

    async def citations(self, id: ExternalId, direction: Literal["in", "out"]) -> list[ExternalId]:
        return []

    def _results(self, fetched: Fetched) -> list[Any]:
        if (used := fetched.headers.get("x-ratelimit-used")) is not None:
            logger.info("openaire x-ratelimit-used=%s", used)  # to check the real keyless limit later
        if fetched.status == 404:
            return []
        if fetched.status != 200:
            raise SourceUnavailable(self.name, f"HTTP {fetched.status}")
        try:
            rows = fetched.json()["results"]
        except (ValueError, KeyError, TypeError) as exc:
            raise SourceUnavailable(self.name, "unreadable response") from exc
        if rows is None:
            return []
        if not isinstance(rows, list):
            raise SourceUnavailable(self.name, "unreadable response")
        return rows

    async def search(self, query: SearchQuery) -> list[PaperRecord]:
        params: dict[str, str | int] = {
            "search": query.text,
            "type": "publication",
            "pageSize": max(1, min(query.limit, 100)),
        }
        if query.year_from:
            params["fromPublicationDate"] = str(query.year_from)
        if query.year_to:
            params["toPublicationDate"] = str(query.year_to)
        fetched = await self._client.get(
            f"{BASE}/researchProducts",
            source=self.name,
            params=params,
            cache_key=_hash_key("search", params),
            ttl=TTL_SEARCH,
        )
        return [r for raw in self._results(fetched) if (r := parse_result(raw))]

    async def get(self, ids_: list[ExternalId]) -> list[PaperRecord]:
        wanted: dict[str, set[ExternalId]] = {}
        for e in ids_:
            n = ids.normalize(e)
            if n and n.scheme == "arxiv":
                wanted.setdefault(ids.arxiv_doi(n.value), set()).update({n, ExternalId("doi", ids.arxiv_doi(n.value))})
            elif n and n.scheme == "doi" and "," not in n.value:  # a comma may be read as a list of PIDs
                wanted.setdefault(n.value, set()).add(n)
        recs: list[PaperRecord] = []
        for doi, want in wanted.items():
            fetched = await self._client.get(
                f"{BASE}/researchProducts",
                source=self.name,
                params={"pid": doi, "pageSize": 5},
                cache_key=f"{KEY}:pid:{doi}",
                ttl=TTL_METADATA,
            )
            if (rec := next((r for raw in self._results(fetched) if (r := parse_result(raw, want))), None)) is not None:
                recs.append(rec)
        return recs
