"""Search fan-out (spec §8.1.1): query the chosen sources concurrently, then dedupe locally."""

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from researchly.connectors.base import Connector, ConnectorError, PaperRecord, SearchQuery
from researchly.connectors.biorxiv import is_biorxiv_doi
from researchly.services.identity import dedupe

ALWAYS = ("arxiv", "crossref", "openalex")
BY_DOMAIN = {"biomedical": ("europepmc", "pubmed"), "ml": ("hf",)}


@dataclass
class SearchOutcome:
    papers: list[PaperRecord]
    errors: dict[str, str] = field(default_factory=dict)  # source -> why it gave nothing (quota, outage)


DEEP_EXTRAS = ("openaire",)  # 60 calls an hour keyless: Deep runs only (§8.1.1)
VENUE_DOMAINS = frozenset({"ml", "cs"})


def default_sources(
    connectors: Mapping[str, Connector], domains: frozenset[str] = frozenset(), *, deep: bool = False
) -> list[Connector]:
    names = list(ALWAYS) + [n for d in sorted(domains) for n in BY_DOMAIN.get(d, ())]
    names += list(DEEP_EXTRAS) if deep else []
    return [connectors[n] for n in names if n in connectors]


def enrichment_sources(record: PaperRecord, domains: frozenset[str] = frozenset()) -> list[str]:
    """Sources to ask about a found paper, in order (§8.1.1). OpenReview is asked through `by_title`."""
    names = ["openalex", "crossref"]
    doi = record.id("doi")
    if doi and is_biorxiv_doi(doi):
        names.append("biorxiv")
    if domains & VENUE_DOMAINS and (doi or record.id("arxiv")) and record.venue_type in (None, "repository"):
        names.append("dblp")  # a preprint's conference or journal record
    if "ml" in domains:
        names.append("openreview")
    return names


async def federated_search(query: SearchQuery, connectors: Sequence[Connector]) -> SearchOutcome:
    results = await asyncio.gather(*(c.search(query) for c in connectors), return_exceptions=True)
    papers: list[PaperRecord] = []
    errors: dict[str, str] = {}
    for connector, result in zip(connectors, results, strict=True):
        if isinstance(result, ConnectorError):
            errors[connector.name] = str(result)
        elif isinstance(result, BaseException):
            raise result
        else:
            papers += result
    return SearchOutcome(dedupe(papers), errors)
