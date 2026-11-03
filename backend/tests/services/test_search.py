from typing import Any, cast

import pytest

from researchly.connectors.base import (
    Author,
    Connector,
    ExternalId,
    PaperRecord,
    QuotaExhausted,
    SearchQuery,
    SourceUnavailable,
)
from researchly.services.search import default_sources, federated_search

pytestmark = pytest.mark.anyio


class _Fake:
    def __init__(self, name, result):
        self.name, self._result = name, result

    async def search(self, query):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def paper(source, arxiv_id):
    return PaperRecord(
        source,
        [ExternalId("arxiv", arxiv_id)],
        "Attention Is All You Need",
        year=2017,
        authors=[Author("Ashish Vaswani")],
    )


def Fake(name, result) -> Connector:  # noqa: N802
    return cast(Connector, _Fake(name, result))


async def test_results_are_deduped_and_quota_errors_reported():
    outcome = await federated_search(
        SearchQuery("attention"),
        [
            Fake("arxiv", [paper("arxiv", "1706.03762")]),
            Fake("hf", [paper("hf", "1706.03762")]),
            Fake("openalex", QuotaExhausted("openalex", "OpenAlex daily budget low")),
        ],
    )
    assert len(outcome.papers) == 1
    assert outcome.errors == {"openalex": "openalex: OpenAlex daily budget low"}


async def test_source_unavailable_is_reported_not_raised():
    outcome = await federated_search(
        SearchQuery("x"),
        [
            Fake("arxiv", [paper("arxiv", "1706.03762")]),
            Fake("crossref", SourceUnavailable("crossref", "circuit open")),
        ],
    )
    assert len(outcome.papers) == 1
    assert outcome.errors == {"crossref": "crossref: circuit open"}


async def test_unexpected_errors_propagate():
    with pytest.raises(KeyError):
        await federated_search(SearchQuery("x"), [Fake("arxiv", KeyError("bug"))])


def test_default_sources_follow_the_routing_rules():
    names: dict[str, Any] = {
        n: object() for n in ("arxiv", "crossref", "openalex", "europepmc", "pubmed", "hf", "datacite")
    }
    pick = lambda d: [n for n, c in names.items() if c in default_sources(names, frozenset(d))]  # noqa: E731
    assert pick([]) == ["arxiv", "crossref", "openalex"]
    assert pick(["biomedical"]) == ["arxiv", "crossref", "openalex", "europepmc", "pubmed"]
    assert pick(["ml"]) == ["arxiv", "crossref", "openalex", "hf"]
