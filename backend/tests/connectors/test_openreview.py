import httpx
import pytest

from researchly.connectors import openreview as orv
from researchly.connectors.base import ExternalId, SearchQuery, SourceUnavailable
from researchly.connectors.http import PoliteClient
from researchly.connectors.openreview import OpenReviewConnector

pytestmark = pytest.mark.anyio


def note(title="Dataflow Is All You Need", venue="MLSys 2026", domain="MLSys/2026/Conference", **content):
    return {
        "id": "tjNf0L8QjR",
        "forum": "tjNf0L8QjR",
        "domain": domain,
        "pdate": 1767225600000,  # 2026-01-01
        "license": "CC BY 4.0",
        "content": {
            "title": {"value": title},
            "venue": {"value": venue},
            "venueid": {"value": domain},
            "authors": {"value": ["Ada Lovelace", "Alan Turing"]},
            "authorids": {"value": ["ada@example.org", "~Alan_Turing1"]},
            "abstract": {"value": " An abstract. "},
        }
        | content,
    }


def client_for(handler) -> PoliteClient:
    return PoliteClient(transport=httpx.MockTransport(handler), limits={"default": (0.0, 1)})


@pytest.mark.parametrize(
    ("venue", "expected"),
    [
        ("ICLR 2025 Oral", "oral"),
        ("NeurIPS 2024 spotlight", "spotlight"),
        ("ICLR 2025 Poster", "poster"),
        ("MLSys 2026", "accepted"),
        ("Submitted to ICLR 2025", "not_accepted"),
        ("ICLR 2024 Conference Withdrawn Submission", "not_accepted"),
        ("Rejected by TMLR", "not_accepted"),
        ("Postering 2025", "accepted"),  # whole words only
    ],
)
def test_decision(venue, expected):
    assert orv.decision(venue) == expected


def test_parse_note():
    rec = orv.parse_note(note(html={"value": "https://doi.org/10.48550/arXiv.2407.04708"}))
    assert rec is not None
    assert rec.source == "openreview" and rec.title == "Dataflow Is All You Need" and rec.year == 2026
    assert rec.ids == [ExternalId("doi", "10.48550/arxiv.2407.04708")]
    assert [a.name for a in rec.authors] == ["Ada Lovelace", "Alan Turing"]
    assert rec.abstract == "An abstract." and rec.venue is None and rec.license == "CC BY 4.0"
    assert rec.extra == {
        "openreview_id": "tjNf0L8QjR",
        "openreview_venue": "MLSys 2026",
        "openreview_venueid": "MLSys/2026/Conference",
        "openreview_decision": "accepted",
    }
    assert "ada@example.org" not in repr(rec)  # authorids are never read


def test_parse_note_arxiv_link_and_fallbacks():
    rec = orv.parse_note(note(venue="Some Workshop", html={"value": "https://arxiv.org/abs/2407.04708v2"}))
    assert rec is not None
    assert rec.ids == [ExternalId("arxiv", "2407.04708"), ExternalId("doi", "10.48550/arxiv.2407.04708")]
    assert rec.year == 2026  # no year in the venue: pdate
    assert orv.parse_note(note(domain="DBLP.org")) is None  # an import, not a venue decision
    assert orv.parse_note(note(domain="OpenReview.net/Archive")) is None
    assert orv.parse_note(note(venue="")) is None
    assert orv.parse_note("junk") is None


async def test_by_title_matches_exactly_and_checks_the_year():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/notes/search"
        assert dict(request.url.params) == {
            "term": "Dataflow is all you need",
            "content": "title",
            "source": "forum",
            "limit": "5",
        }
        notes = [note(title="Dataflow Is All You Need (Extended)"), note(domain="DBLP.org"), note()]
        return httpx.Response(200, json={"notes": notes, "count": 3})

    async with client_for(handler) as client:
        conn = OpenReviewConnector(client)
        rec = await conn.by_title("  Dataflow is all you need ", 2025)
        assert rec is not None and rec.extra["openreview_decision"] == "accepted"
        assert await conn.by_title("Dataflow is all you need", 2020) is None  # 2026 is too far from 2020


async def test_weak_titles_make_no_request_and_the_fan_out_gets_nothing():
    seen = []
    async with client_for(lambda r: seen.append(r) or httpx.Response(500)) as client:
        conn = OpenReviewConnector(client)
        assert await conn.by_title("Transformers") is None
        assert await conn.search(SearchQuery("attention")) == []
        assert await conn.get([ExternalId("arxiv", "1706.03762")]) == []
        assert not any(vars(conn.capabilities()).values())
    assert seen == []


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(403, json={"name": "ChallengeRequiredError", "status": 403}), "HTTP 403"),
        (httpx.Response(200, json={"count": 0}), "unreadable response"),
        (httpx.Response(200, text="<html>"), "unreadable response"),
    ],
)
async def test_errors(response, message):
    async with client_for(lambda r: response) as client:
        with pytest.raises(SourceUnavailable, match=message):
            await OpenReviewConnector(client).by_title("Dataflow Is All You Need")


@pytest.mark.vcr
async def test_by_title_finds_a_v2_venue_decision(polite):
    rec = await OpenReviewConnector(polite).by_title("Dataflow Is All You Need")
    assert rec is not None and rec.extra["openreview_venue"].startswith("MLSys")
    assert rec.extra["openreview_decision"] == "accepted" and rec.year == 2026


@pytest.mark.vcr
async def test_by_title_ignores_imports_and_near_titles(polite):
    assert await OpenReviewConnector(polite).by_title("Attention Is All You Need", 2017) is None
