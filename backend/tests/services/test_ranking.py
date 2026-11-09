import pytest

from researchly.connectors.base import ExternalId, PaperRecord
from researchly.services import ranking
from researchly.services.ranking import chao2, load_config, percentiles, rank, sigmoid

CFG = load_config()
NOW = 2026


def rec(n: int, *, year: int | None = 2020, cited: int | None = 10, refs=(), **extra) -> PaperRecord:
    return PaperRecord(
        source="openalex",
        ids=[ExternalId("openalex", f"W{n}")],
        title=f"Paper {n}",
        year=year,
        cited_by=cited,
        references=[ExternalId("openalex", f"W{r}") for r in refs],
        extra=dict(extra),
    )


def test_every_preset_has_weights_that_sum_to_one():
    for preset in ("balanced", "seminal", "recent"):
        weights = load_config(preset).weights
        assert set(weights) == set(ranking.COMPONENTS)
        assert sum(weights.values()) == pytest.approx(1.0)


def test_percentiles_are_mid_ranks_and_null_below_min_n():
    assert percentiles([1, 2, 2, None, 3], min_n=3) == [0.125, 0.5, 0.5, None, 0.875]
    assert percentiles([1, 2, None], min_n=3) == [None, None, None]


def test_sigmoid_is_stable_for_large_logits():
    assert sigmoid(0) == 0.5
    assert sigmoid(-1000) == pytest.approx(0.0) and sigmoid(1000) == pytest.approx(1.0)


def test_the_gate_drops_candidates_below_tau():
    out = rank([rec(1), rec(2)], [0.5, CFG.tau_abstract - 0.1], CFG, now_year=NOW)
    assert [r.record.title for r in out] == ["Paper 1"]


def test_null_components_are_dropped_and_weights_renormalised():
    [only] = rank([rec(1, cited=None)], [0.0], CFG, now_year=NOW)  # one paper: nothing to rank against
    assert only.breakdown["impact"] is None and only.breakdown["centrality"] is None
    assert only.score == pytest.approx(0.5)  # relevance alone, not relevance diluted by zeros


def test_impact_orders_equally_relevant_papers():
    papers = [rec(n, cited=c) for n, c in enumerate([5, 50, 500, 5000, 50000])]
    out = rank(papers, [0.0] * 5, CFG, now_year=NOW)
    assert [r.record.cited_by for r in out] == [50000, 5000, 500, 50, 5]
    assert out[0].breakdown["impact"] == pytest.approx(0.9)


def test_influential_counts_citations_from_inside_the_set():
    papers = [rec(1, refs=[3]), rec(2, refs=[3]), rec(3), rec(4, refs=[1]), rec(5)]
    out = {r.record.title: r.breakdown["influential"] for r in rank(papers, [0.0] * 5, CFG, now_year=NOW)}
    assert out["Paper 3"] == max(v for v in out.values() if v is not None)


def test_velocity_needs_age_and_enough_citations():
    young = rec(1, year=NOW - 1, cited=100, counts_by_year={NOW: 60})
    few = rec(2, year=2015, cited=5, counts_by_year={NOW: 5})
    assert ranking._velocity(young, NOW, CFG.velocity_min_total) is None
    assert ranking._velocity(few, NOW, CFG.velocity_min_total) is None
    hot = rec(3, year=2015, cited=100, counts_by_year={str(NOW): 30, str(NOW - 1): 20})  # keys after a JSON trip
    assert ranking._velocity(hot, NOW, CFG.velocity_min_total) == pytest.approx(0.5)


def test_venue_prefers_the_openreview_decision_then_the_tier():
    oral = rec(1, openreview_decision="oral")
    tiered = PaperRecord(source="openalex", ids=[], title="t", venue="  NeurIPS ")
    assert ranking._venue(oral, CFG) == 1.0
    assert ranking._venue(tiered, CFG) == CFG.venue_tiers["neurips"]
    assert ranking._venue(rec(3), CFG) is None


def test_practical_counts_only_for_ml_projects():
    coded = rec(1, githubRepo="https://github.com/a/b")
    [plain] = rank([coded], [0.0], CFG, now_year=NOW)
    [ml] = rank([coded], [0.0], CFG, now_year=NOW, domains=frozenset({"ml"}))
    [table] = rank([rec(2)], [0.0], CFG, now_year=NOW, domains=frozenset({"ml"}), has_code=[True])
    assert plain.breakdown["practical"] is None
    assert ml.breakdown["practical"] == 1.0 and table.breakdown["practical"] == 1.0


def test_chao2_is_bias_corrected_and_reports_what_was_observed():
    assert chao2([{"a", "b", "c"}, {"a", "b", "d"}, {"a", "e"}]) == (5, pytest.approx(8.0))
    assert chao2([{"a", "b"}, {"c"}]) == (3, pytest.approx(4.5))  # q2 = 0: the q1(q1-1)/2 form
    assert chao2([{"a"}]) == (1, 1.0)
