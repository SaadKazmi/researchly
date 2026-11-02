"""Identifier normalization shared by connectors and identity resolution (spec §8.3)."""

import re

from researchly.connectors.base import ExternalId

_DOI = re.compile(r"^10\.\d{4,9}/\S+$")
_ARXIV_NEW = re.compile(r"^(\d{4}\.\d{4,5})(v\d+)?$")
_ARXIV_OLD = re.compile(r"^([a-z-]+(?:\.[A-Za-z]{2})?/\d{7})(v\d+)?$")
_OPENALEX = re.compile(r"^W\d+$")
_PMID = re.compile(r"^\d{1,9}$")
_PMCID = re.compile(r"^PMC\d+$")


def _strip(raw: str, *prefixes: str) -> str:
    value = raw.strip()
    for prefix in prefixes:
        if value.lower().startswith(prefix):
            return value[len(prefix) :]
    return value


def doi(raw: str) -> str | None:
    value = _strip(raw, "https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/", "doi:")
    value = value.lower()  # DOIs are case-insensitive; OpenAlex and DataCite store arXiv DOIs lowercased
    return value if _DOI.match(value) else None


def arxiv(raw: str) -> str | None:
    value = _strip(raw, "arxiv:", "https://arxiv.org/abs/", "http://arxiv.org/abs/")
    match = _ARXIV_NEW.match(value) or _ARXIV_OLD.match(value)
    return match.group(1) if match else None  # versionless: versioned DataCite DOIs do not exist


def arxiv_doi(arxiv_id: str) -> str:
    return f"10.48550/arxiv.{arxiv_id}".lower()


def openalex(raw: str) -> str | None:
    value = _strip(raw, "https://openalex.org/", "http://openalex.org/", "openalex:").upper()
    return value if _OPENALEX.match(value) else None


def pmid(raw: str) -> str | None:
    value = _strip(raw, "https://pubmed.ncbi.nlm.nih.gov/", "pmid:").rstrip("/")
    return value if _PMID.match(value) else None


def pmcid(raw: str) -> str | None:
    value = _strip(raw, "pmcid:").upper()
    return value if _PMCID.match(value) else None


_NORMALIZERS = {"doi": doi, "arxiv": arxiv, "openalex": openalex, "pmid": pmid, "pmcid": pmcid}


def normalize(eid: ExternalId) -> ExternalId | None:
    fn = _NORMALIZERS.get(eid.scheme)
    if fn is None:
        return eid
    value = fn(eid.value)
    return ExternalId(eid.scheme, value) if value else None
