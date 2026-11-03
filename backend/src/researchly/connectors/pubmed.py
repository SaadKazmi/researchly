"""PubMed E-utilities connector (research 2026-11-03 §7, §11): esearch, efetch XML, elink citations. Keyless."""

import hashlib
import json
import re
from typing import Any, Literal
from xml.etree.ElementTree import Element, ParseError  # exception and type only; parsing is defusedxml's

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

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

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
KEY = "pubmed:v1"
BATCH = 200  # IDs per efetch GET (NCBI advises POST beyond this)
MAX_RESULTS = 200

_ORCID = re.compile(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]")
_LINKNAME = {"in": "pubmed_pubmed_citedin", "out": "pubmed_pubmed_refs"}


def _hash_key(prefix: str, params: dict[str, str | int]) -> str:
    return f"{KEY}:{prefix}:" + hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()


def _text(node: Element | None) -> str | None:
    """Text with inline markup (`<i>`, `<sub>`) flattened and whitespace collapsed."""
    return " ".join("".join(node.itertext()).split()) or None if node is not None else None


def _author(node: Element) -> Author | None:
    if collective := _text(node.find("CollectiveName")):
        return Author(name=collective)
    last, fore = _text(node.find("LastName")), _text(node.find("ForeName"))
    name = " ".join(p for p in (fore, last) if p)
    if not name:
        return None
    orcid_text = _text(node.find("Identifier[@Source='ORCID']")) or ""
    return Author(name=name, orcid=m.group(0) if (m := _ORCID.search(orcid_text)) else None)


def _abstract(article: Element) -> str | None:
    parts = []
    for section in article.findall("Abstract/AbstractText"):
        if text := _text(section):
            label = section.get("Label")
            parts.append(f"{label}: {text}" if label else text)
    return "\n".join(parts) or None


def _year(article: Element) -> int | None:
    pub = article.find("Journal/JournalIssue/PubDate")
    if pub is None:
        return None
    raw = _text(pub.find("Year")) or (_text(pub.find("MedlineDate")) or "")[:4]
    return int(raw) if len(raw) == 4 and raw.isdigit() else None


def _parse_article(node: Element, source: str) -> PaperRecord | None:
    pmid = ids.pmid(_text(node.find("MedlineCitation/PMID")) or "")
    article = node.find("MedlineCitation/Article")
    if pmid is None or article is None:
        return None
    found = [ExternalId("pmid", pmid)]
    # Only PubmedData/ArticleIdList: `.//ArticleId` would also match every ReferenceList entry
    doi = _text(node.find("PubmedData/ArticleIdList/ArticleId[@IdType='doi']")) or _text(
        article.find("ELocationID[@EIdType='doi']")
    )
    if doi and (value := ids.doi(doi)):
        found.append(ExternalId("doi", value))
    pmc = _text(node.find("PubmedData/ArticleIdList/ArticleId[@IdType='pmc']"))
    if pmc and (value := ids.pmcid(pmc)):
        found.append(ExternalId("pmcid", value))
    types = [_text(t) for t in article.findall("PublicationTypeList/PublicationType")]
    return PaperRecord(
        source=source,
        ids=found,
        title=(_text(article.find("ArticleTitle")) or "").removesuffix("."),
        abstract=_abstract(article),
        year=_year(article),
        authors=[a for n in article.findall("AuthorList/Author") if (a := _author(n))],
        venue=_text(article.find("Journal/Title")),
        is_retracted="Retracted Publication" in types,
    )


def parse_articles(body: bytes, source: str) -> list[PaperRecord]:
    try:
        root = fromstring(body)
    except (DefusedXmlException, ParseError, ValueError) as exc:  # entity bombs, HTML, truncation
        raise SourceUnavailable(source, "unreadable response") from exc
    if root.tag != "PubmedArticleSet":
        raise SourceUnavailable(source, "unreadable response")
    return [r for n in root.findall("PubmedArticle") if (r := _parse_article(n, source))]


def _json(fetched: Fetched, source: str) -> dict[str, Any]:
    try:
        body = fetched.json()
    except ValueError as exc:
        raise SourceUnavailable(source, "unreadable response") from exc
    if not isinstance(body, dict):
        raise SourceUnavailable(source, "unreadable response")
    return body


class PubMedConnector:
    name = "pubmed"

    def __init__(self, client: PoliteClient) -> None:
        self._client = client

    def capabilities(self) -> ConnectorCaps:
        return ConnectorCaps(search=True, get=True, citations_in=True, citations_out=True)

    def _ok(self, fetched: Fetched) -> bool:
        """False for an answer with no data (404, or NCBI's HTTP 400 `<ERROR>` for IDs it cannot resolve)."""
        if fetched.status == 404 or (fetched.status == 400 and b"<ERROR>" in fetched.body):
            return False
        if fetched.status != 200:
            raise SourceUnavailable(self.name, f"HTTP {fetched.status}")
        return True

    async def _efetch(self, pmids: list[str]) -> list[PaperRecord]:
        out: list[PaperRecord] = []
        for i in range(0, len(pmids), BATCH):
            chunk = pmids[i : i + BATCH]
            params: dict[str, str | int] = {"db": "pubmed", "id": ",".join(chunk), "retmode": "xml"}
            key = f"{KEY}:pmid:{chunk[0]}" if len(chunk) == 1 else _hash_key("get", params)
            fetched = await self._client.get(
                f"{BASE}/efetch.fcgi", params=params, source=self.name, cache_key=key, ttl=TTL_METADATA
            )
            if self._ok(fetched):
                out += parse_articles(fetched.body, self.name)
        return out

    async def get(self, ids_: list[ExternalId]) -> list[PaperRecord]:
        pmids = [p for e in ids_ if e.scheme == "pmid" and (p := ids.pmid(e.value))]
        return await self._efetch(list(dict.fromkeys(pmids)))

    async def search(self, query: SearchQuery) -> list[PaperRecord]:
        text = query.text.strip()
        if not text:
            return []
        params: dict[str, str | int] = {
            "db": "pubmed",
            "term": text,
            "retmode": "json",
            "retmax": max(1, min(query.limit, MAX_RESULTS)),
            "sort": "relevance",
        }
        if query.year_from is not None or query.year_to is not None:
            params |= {"datetype": "pdat", "mindate": query.year_from or 1000, "maxdate": query.year_to or 3000}
        fetched = await self._client.get(
            f"{BASE}/esearch.fcgi",
            params=params,
            source=self.name,
            cache_key=_hash_key("search", params),
            ttl=TTL_SEARCH,
        )
        if not self._ok(fetched):
            return []
        try:
            idlist = _json(fetched, self.name).get("esearchresult", {}).get("idlist", [])
            pmids = [p for raw in idlist if (p := ids.pmid(str(raw)))]
        except (AttributeError, TypeError) as exc:
            raise SourceUnavailable(self.name, "unreadable response") from exc
        return await self._efetch(list(dict.fromkeys(pmids)))

    async def citations(self, id: ExternalId, direction: Literal["in", "out"]) -> list[ExternalId]:
        norm = ids.normalize(id)
        if norm is None or norm.scheme != "pmid":
            return []
        inbound = direction == "in"
        params: dict[str, str | int] = {
            "dbfrom": "pubmed",
            "db": "pubmed",
            "id": norm.value,
            "linkname": _LINKNAME[direction],
            "retmode": "json",
        }
        fetched = await self._client.get(
            f"{BASE}/elink.fcgi",
            params=params,
            source=self.name,
            cache_key=f"{KEY}:count:in:{norm.value}" if inbound else f"{KEY}:refs:pmid:{norm.value}",
            ttl=TTL_COUNTS if inbound else TTL_METADATA,  # citers keep growing, a reference list is fixed
        )
        if not self._ok(fetched):
            return []
        try:
            linksets = _json(fetched, self.name).get("linksets", [])
            dbs = linksets[0].get("linksetdbs", []) if linksets else []
            links = [x for db in dbs if db.get("linkname") == _LINKNAME[direction] for x in db.get("links", [])]
            found = [ids.pmid(str(x)) for x in links]
        except (AttributeError, TypeError, IndexError) as exc:
            raise SourceUnavailable(self.name, "unreadable response") from exc
        return list(dict.fromkeys(ExternalId("pmid", p) for p in found if p))
