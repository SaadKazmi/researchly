"""DBLP connector (R-conn §5): CS venue and year for a known DOI, over the QLever SPARQL endpoint. Keyless, CC0.

The dblp.org search API sits behind an Anubis bot challenge and is never called. There is no title lookup: a
case-insensitive title scan took 8.3 s (ruling 12). DBLP keeps an arXiv paper's CoRR record and its conference
record apart, so the query finds the records that share an author and the title with the DOI's record.
"""

import re
from typing import Any, Literal

from researchly.connectors import ids
from researchly.connectors.base import ConnectorCaps, ExternalId, PaperRecord, SearchQuery, SourceUnavailable
from researchly.connectors.http import TTL_METADATA, PoliteClient

BASE = "https://sparql.dblp.org/sparql"
KEY = "dblp:v1"
ACCEPT = frozenset({"application/sparql-results+json", "application/json"})
_SCHEMA = "https://dblp.org/rdf/schema#"
_REC = "https://dblp.org/rec/"
_RANK = {"Inproceedings": 0, "Article": 1, "Informal": 2}
_VENUE_TYPE = {"Inproceedings": "conference", "Article": "journal", "Informal": "repository"}
_UNSAFE_IRI = re.compile(r'[<>"{}|^`\\\s]')  # would break out of the IRI in the query
# DOIs are stored as upper-case IRIs (https://doi.org/10.48550/ARXIV.1706.03762); a mixed-case one is missed
_QUERY = """PREFIX dblp: <https://dblp.org/rdf/schema#>
SELECT DISTINCT ?p ?t ?venue ?year ?type ?doi WHERE {{
  VALUES ?d {{ <https://doi.org/{upper}> <https://doi.org/{lower}> }}
  ?corr dblp:doi ?d ; dblp:title ?ct ; dblp:authoredBy ?a .
  ?p dblp:authoredBy ?a ; dblp:title ?t ; dblp:publishedIn ?venue ; dblp:yearOfPublication ?year ; a ?type .
  OPTIONAL {{ ?p dblp:doi ?doi }}
  FILTER(LCASE(STR(?t)) = LCASE(STR(?ct)))
}}
ORDER BY ?p
LIMIT 50"""


def parse_bindings(bindings: list[Any], doi: str) -> PaperRecord | None:
    best: tuple[int, dict[str, str]] | None = None
    for b in bindings:
        if not isinstance(b, dict):
            continue
        row = {k: v["value"] for k, v in b.items() if isinstance(v, dict) and isinstance(v.get("value"), str)}
        kind = row.get("type", "").removeprefix(_SCHEMA)
        if kind not in _RANK or not row.get("p", "").startswith(_REC) or not row.get("t", "").strip():
            continue  # dblp:Publication is the super-type every record also has
        if best is None or _RANK[kind] < best[0]:
            best = (_RANK[kind], row)
    if best is None:
        return None
    row = best[1]
    kind = row["type"].removeprefix(_SCHEMA)
    year = row.get("year", "")[:4]
    return PaperRecord(
        source="dblp",
        ids=[ExternalId("doi", doi), ExternalId("dblp", row["p"].removeprefix(_REC))],
        title=" ".join(row["t"].split()).removesuffix("."),
        year=int(year) if year.isdigit() else None,
        venue=row.get("venue"),
        venue_type=_VENUE_TYPE[kind],
        extra={
            "dblp_venue": row.get("venue"),
            "dblp_type": kind,
            "dblp_doi": ids.doi(row.get("doi", "")),  # this record's own DOI: another work's, maybe
        },
    )


class DblpConnector:
    name = "dblp"

    def __init__(self, client: PoliteClient) -> None:
        self._client = client

    def capabilities(self) -> ConnectorCaps:
        return ConnectorCaps(search=False, get=True, citations_in=False, citations_out=False)

    async def search(self, query: SearchQuery) -> list[PaperRecord]:
        return []  # the search API is behind Anubis; never bypassed

    async def citations(self, id: ExternalId, direction: Literal["in", "out"]) -> list[ExternalId]:
        return []

    async def get(self, ids_: list[ExternalId]) -> list[PaperRecord]:
        dois: list[str] = []
        for e in ids_:
            n = ids.normalize(e)
            if n and n.scheme == "arxiv":
                dois.append(ids.arxiv_doi(n.value))
            elif n and n.scheme == "doi":
                dois.append(n.value)
        recs: list[PaperRecord] = []
        for doi in dict.fromkeys(dois):
            if _UNSAFE_IRI.search(doi):
                continue
            fetched = await self._client.get(
                BASE,
                source=self.name,
                params={"query": _QUERY.format(upper=doi.upper(), lower=doi)},
                cache_key=f"{KEY}:doi:{doi}",
                ttl=TTL_METADATA,
                accept=ACCEPT,
            )
            if fetched.status != 200:  # Anubis answers 202 with an HTML page
                raise SourceUnavailable(self.name, f"HTTP {fetched.status}")
            try:
                bindings = fetched.json()["results"]["bindings"]
            except (ValueError, KeyError, TypeError) as exc:
                raise SourceUnavailable(self.name, "unreadable response") from exc
            if not isinstance(bindings, list):
                raise SourceUnavailable(self.name, "unreadable response")
            if (rec := parse_bindings(bindings, doi)) is not None:
                recs.append(rec)
        return recs
