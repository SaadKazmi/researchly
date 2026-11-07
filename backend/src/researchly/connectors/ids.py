"""Identifier normalization shared by connectors and identity resolution (spec §8.3)."""

import re
from urllib.parse import unquote, urlsplit

from researchly.connectors.base import ExternalId

_DOI = re.compile(r"^10\.\d{4,9}/\S+$")
_ARXIV_NEW = re.compile(r"^(\d{4}\.\d{4,5})(v\d+)?$", re.I)
_ARXIV_OLD = re.compile(r"^([a-z-]+)(?:\.[a-z]{2})?/(\d{7})(v\d+)?$", re.I)
_ARXIV_PREFIX = re.compile(r"^(?:arxiv:|https?://(?:www\.|export\.)?arxiv\.org/(?:abs|pdf)/)", re.I)
_ARXIV_SUFFIX = re.compile(r"(?:\s*\[[^\]]*\]|\.pdf)$", re.I)
_DOI_URL = ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/")
_INVISIBLE = " \t\r\n\u200b\u200c\u200d\u2060\ufeff"
_OPENALEX = re.compile(r"^W\d+$")
_PMID = re.compile(r"^\d{1,9}$")
_PMCID = re.compile(r"^PMC\d+$")


def _strip(raw: str, *prefixes: str) -> str:
    value = raw.strip()
    for prefix in prefixes:
        if value.lower().startswith(prefix):
            return value[len(prefix) :].strip()
    return value


def doi(raw: str) -> str | None:
    value = raw.strip(_INVISIBLE)
    is_url = value.lower().startswith(_DOI_URL)
    value = _strip(value, *_DOI_URL, "doi:")
    if is_url:  # a bare DOI may legally hold ? and #, a URL's query and fragment are not part of it
        value = value.split("?")[0].split("#")[0]
    while value and (value[-1] in ".,;" or (value[-1] == ")" and value.count(")") > value.count("("))):
        value = value[:-1]
    value = value.lower()  # DOIs are case-insensitive; OpenAlex and DataCite store arXiv DOIs lowercased
    if not _DOI.match(value) or {".", ".."} & set(value.split("/")):  # dot segments would reach another API path
        return None
    return value


def arxiv(raw: str) -> str | None:
    value = _ARXIV_PREFIX.sub("", raw.strip())
    value = _ARXIV_SUFFIX.sub("", value).strip()
    if match := _ARXIV_NEW.match(value):
        return match.group(1)  # versionless: versioned DataCite DOIs do not exist
    if match := _ARXIV_OLD.match(value):
        return f"{match.group(1).lower()}/{match.group(2)}"  # canonical archive/NNNNNNN, no subject class
    return None


def arxiv_doi(arxiv_id: str) -> str:
    return f"10.48550/arxiv.{arxiv_id}".lower()


def openalex(raw: str) -> str | None:
    value = _strip(
        raw, "https://openalex.org/works/", "https://openalex.org/", "http://openalex.org/", "openalex:"
    ).upper()
    return value if _OPENALEX.match(value) else None


def pmid(raw: str) -> str | None:
    value = _strip(raw, "https://pubmed.ncbi.nlm.nih.gov/", "pmid:").rstrip("/")
    value = value.lstrip("0")  # all zeros leaves nothing, which is not a PMID
    return value if _PMID.match(value) else None


def pmcid(raw: str) -> str | None:
    value = (
        _strip(raw, "https://www.ncbi.nlm.nih.gov/pmc/articles/", "https://pmc.ncbi.nlm.nih.gov/articles/", "pmcid:")
        .rstrip("/")
        .upper()
    )
    return value if _PMCID.match(value) else None


_NORMALIZERS = {"doi": doi, "arxiv": arxiv, "openalex": openalex, "pmid": pmid, "pmcid": pmcid}


def normalize(eid: ExternalId) -> ExternalId | None:
    fn = _NORMALIZERS.get(eid.scheme)
    if fn is None:
        return eid
    value = fn(eid.value)
    return ExternalId(eid.scheme, value) if value else None


def from_url(url: str) -> ExternalId | None:
    """The scholarly ID a landing-page URL names (web search results, OpenReview links), or None. Never raises."""
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").removeprefix("www.")
    except ValueError:  # e.g. an unbalanced IPv6 bracket
        return None
    if parts.scheme.lower() not in ("http", "https"):
        return None
    path = unquote(parts.path)
    segs = [s for s in path.split("/") if s]
    value: str | None
    if host in ("doi.org", "dx.doi.org", "oadoi.org"):
        return ExternalId("doi", value) if (value := doi(path.lstrip("/"))) else None
    if host in ("arxiv.org", "export.arxiv.org") and len(segs) >= 2 and segs[0] in ("abs", "pdf", "html"):
        return ExternalId("arxiv", value) if (value := arxiv("/".join(segs[1:]))) else None
    if host == "pubmed.ncbi.nlm.nih.gov" and len(segs) == 1:
        return ExternalId("pmid", value) if (value := pmid(segs[0])) else None
    if host == "ncbi.nlm.nih.gov" and len(segs) == 2 and segs[0] == "pubmed":
        return ExternalId("pmid", value) if (value := pmid(segs[1])) else None
    if host == "ncbi.nlm.nih.gov" and len(segs) >= 3 and segs[:2] == ["pmc", "articles"]:
        return ExternalId("pmcid", value) if (value := pmcid(segs[2])) else None
    if host == "pmc.ncbi.nlm.nih.gov" and len(segs) >= 2 and segs[0] == "articles":
        return ExternalId("pmcid", value) if (value := pmcid(segs[1])) else None
    if host == "europepmc.org" and len(segs) == 3 and segs[0] in ("article", "abstract"):
        if segs[1].upper() == "MED":
            return ExternalId("pmid", value) if (value := pmid(segs[2])) else None
        if segs[1].upper() == "PMC":
            return ExternalId("pmcid", value) if (value := pmcid(segs[2])) else None
    return None
