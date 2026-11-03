"""Search fan-out (spec §8.1.1): query the chosen sources concurrently, then dedupe locally."""

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from researchly.connectors.base import Connector, ConnectorError, PaperRecord, SearchQuery
from researchly.services.identity import dedupe

ALWAYS = ("arxiv", "crossref", "openalex")
BY_DOMAIN = {"biomedical": ("europepmc", "pubmed"), "ml": ("hf",)}


@dataclass
class SearchOutcome:
    papers: list[PaperRecord]
    errors: dict[str, str] = field(default_factory=dict)  # source -> why it gave nothing (quota, outage)


def default_sources(connectors: Mapping[str, Connector], domains: frozenset[str] = frozenset()) -> list[Connector]:
    names = list(ALWAYS) + [n for d in sorted(domains) for n in BY_DOMAIN.get(d, ())]
    return [connectors[n] for n in names if n in connectors]


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
