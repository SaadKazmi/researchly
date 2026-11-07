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
from researchly.services.search import default_sources, enrichment_sources, federated_search

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


def test_deep_runs_add_the_low_rate_extras():
    names: dict[str, Any] = {n: object() for n in ("arxiv", "crossref", "openalex", "openaire")}
    tier1 = [names["arxiv"], names["crossref"], names["openalex"]]
    assert default_sources(names) == tier1
    assert default_sources(names, deep=True) == [*tier1, names["openaire"]]


@pytest.mark.parametrize(
    ("record", "domains", "expected"),
    [
        (
            PaperRecord("crossref", [ExternalId("doi", "10.1101/2020.03.22.002386")], "T"),
            frozenset(),
            ["openalex", "crossref", "biorxiv"],
        ),
        (
            PaperRecord("arxiv", [ExternalId("arxiv", "1706.03762")], "T", venue_type="repository"),
            frozenset({"ml"}),
            ["openalex", "crossref", "dblp", "openreview"],
        ),
        (
            PaperRecord("openalex", [ExternalId("doi", "10.1109/cvpr.2016.90")], "T", venue_type="conference"),
            frozenset({"cs"}),
            ["openalex", "crossref"],  # the venue is already known
        ),
        (PaperRecord("openalex", [ExternalId("pmid", "1")], "T"), frozenset({"cs"}), ["openalex", "crossref"]),
    ],
)
def test_enrichment_sources(record, domains, expected):
    assert enrichment_sources(record, domains) == expected


def test_cold_spring_harbor_journal_dois_skip_biorxiv():
    rec = PaperRecord("openalex", [ExternalId("doi", "10.1101/gad.123456.120")], "T")
    assert "biorxiv" not in enrichment_sources(rec)
    rec = PaperRecord("openalex", [ExternalId("doi", "10.1101/2020.03.22.002386")], "T")
    assert "biorxiv" in enrichment_sources(rec)
