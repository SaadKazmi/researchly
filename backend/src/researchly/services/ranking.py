"""Candidate ranking (spec §10; R-read §4): a reranker gate, then a weighted sum of 0–1 components.

`relevance` is the sigmoid of the cross-encoder logit (absolute). `impact`, `influential` and `velocity` are mid-rank
percentiles within the gated set; `venue` and `practical` are absolute priors. A null component (unknown, or too few
values to rank) is dropped and the remaining weights are renormalised, so a missing number never counts as zero.
Centrality stays null until the citation graph exists (step 5).
"""

import math
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Collection, Hashable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from researchly.connectors.base import PaperRecord

CONFIG = Path(__file__).with_name("ranking.yaml")
COMPONENTS = ("relevance", "impact", "influential", "centrality", "velocity", "venue", "practical")


@dataclass(frozen=True)
class RankConfig:
    weights: Mapping[str, float]
    tau_abstract: float  # reranker logit gate on title + abstract
    tau_chunk: float  # reranker logit gate on full-text chunks (the Reader)
    min_n: int
    velocity_min_total: int
    decision_prior: Mapping[str, float]
    venue_tiers: Mapping[str, float]


@dataclass(frozen=True)
class Ranked:
    record: PaperRecord
    logit: float
    score: float
    breakdown: dict[str, float | None]  # component -> 0–1; None = n/a (shown, not counted)


def _venue_key(name: str) -> str:
    return " ".join(name.casefold().split())


def load_config(preset: str = "balanced", path: Path = CONFIG) -> RankConfig:
    raw = yaml.safe_load(path.read_text("utf-8"))
    return RankConfig(
        weights={c: float(raw["presets"][preset].get(c, 0.0)) for c in COMPONENTS},
        tau_abstract=float(raw["tau_abstract"]),
        tau_chunk=float(raw["tau_chunk"]),
        min_n=int(raw["min_n_for_percentile"]),
        velocity_min_total=int(raw["velocity_min_total"]),
        decision_prior={k: float(v) for k, v in raw["decision_prior"].items()},
        venue_tiers={_venue_key(k): float(v) for k, v in raw["venue_tiers"].items()},
    )


def sigmoid(x: float) -> float:
    if x >= 0:
        return 1 / (1 + math.exp(-x))
    z = math.exp(x)  # no overflow for very negative logits
    return z / (1 + z)


def percentiles(values: Sequence[float | None], min_n: int) -> list[float | None]:
    present = sorted(v for v in values if v is not None)
    if len(present) < min_n:
        return [None] * len(values)
    out: list[float | None] = []
    for v in values:
        if v is None:
            out.append(None)
            continue
        below, upto = bisect_left(present, v), bisect_right(present, v)
        out.append((below + (upto - below) / 2) / len(present))
    return out


def _impact(rec: PaperRecord, now_year: int) -> float | None:
    if rec.cited_by is None or rec.year is None:
        return None
    return math.log1p(rec.cited_by / max(now_year - rec.year, 1))  # citations per year since publication


def _velocity(rec: PaperRecord, now_year: int, min_total: int) -> float | None:
    """Share of all citations from this year and last year (the spec's "last 24 months / total")."""
    total = rec.cited_by
    if rec.year is None or now_year - rec.year < 2 or total is None or total < min_total:
        return None
    counts = {int(y): int(c) for y, c in (rec.extra.get("counts_by_year") or {}).items()}
    if not counts:
        return None
    return min((counts.get(now_year, 0) + counts.get(now_year - 1, 0)) / total, 1.0)


def _in_degree(records: Sequence[PaperRecord]) -> list[float | None]:
    """How many other candidates cite each one: the keyless stand-in for S2's influential citations."""
    if not any(r.references for r in records):
        return [None] * len(records)
    owner = {i: k for k, r in enumerate(records) for i in r.ids}
    hits: Counter[int] = Counter()
    for k, r in enumerate(records):
        hits.update({owner[ref] for ref in r.references if ref in owner} - {k})
    return [float(hits[k]) for k in range(len(records))]


def _venue(rec: PaperRecord, config: RankConfig) -> float | None:
    decision = rec.extra.get("openreview_decision")
    if isinstance(decision, str) and decision in config.decision_prior:
        return config.decision_prior[decision]
    return config.venue_tiers.get(_venue_key(rec.venue)) if rec.venue else None


def _upvotes(rec: PaperRecord) -> float | None:
    upvotes = rec.extra.get("upvotes")
    return math.log1p(upvotes) if isinstance(upvotes, int) else None


def _score(breakdown: Mapping[str, float | None], weights: Mapping[str, float]) -> float:
    used = [(weights[c], v) for c, v in breakdown.items() if v is not None and weights[c] > 0]
    total = sum(w for w, _ in used)
    return sum(w * v for w, v in used) / total if total else 0.0


def rank(
    records: Sequence[PaperRecord],
    logits: Sequence[float],
    config: RankConfig,
    *,
    now_year: int,
    domains: frozenset[str] = frozenset(),
    has_code: Sequence[bool] | None = None,
) -> list[Ranked]:
    """Gate on the reranker logit, then score the survivors best first. `has_code` is aligned with `records`."""
    code = list(has_code) if has_code is not None else [False] * len(records)
    kept = [(r, lg, c) for r, lg, c in zip(records, logits, code, strict=True) if lg >= config.tau_abstract]
    recs = [r for r, _, _ in kept]
    impact = percentiles([_impact(r, now_year) for r in recs], config.min_n)
    influential = percentiles(_in_degree(recs), config.min_n)
    velocity = percentiles([_velocity(r, now_year, config.velocity_min_total) for r in recs], config.min_n)
    upvotes = percentiles([_upvotes(r) for r in recs], config.min_n)
    ranked: list[Ranked] = []
    for k, (rec, logit, coded) in enumerate(kept):
        practical = None
        if "ml" in domains:  # spec §10: code and HF upvotes matter for ML papers only
            parts = [1.0 if coded or rec.extra.get("githubRepo") else 0.0]
            if (up := upvotes[k]) is not None:
                parts.append(up)
            practical = sum(parts) / len(parts)
        breakdown: dict[str, float | None] = {
            "relevance": sigmoid(logit),
            "impact": impact[k],
            "influential": influential[k],
            "centrality": None,  # PageRank on the citation graph arrives with step 5
            "velocity": velocity[k],
            "venue": _venue(rec, config),
            "practical": practical,
        }
        ranked.append(Ranked(rec, logit, _score(breakdown, config.weights), breakdown))
    return sorted(ranked, key=lambda r: r.score, reverse=True)


def chao2(lists: Sequence[Collection[Hashable]]) -> tuple[int, float]:
    """Chao2 richness from incidence lists (one per sub-question × source): (observed, estimated).

    The classic q1²/(2·q2), and the bias-corrected q1(q1−1)/2 when q2 = 0.

    A lower bound on how many relevant papers exist (R-read §5): the sources overlap by design, which biases it down.
    """
    seen = Counter(x for items in lists for x in set(items))
    observed, m = len(seen), len(lists)
    if m < 2:
        return observed, float(observed)
    q1 = sum(1 for c in seen.values() if c == 1)
    q2 = sum(1 for c in seen.values() if c == 2)
    extra = q1 * q1 / (2 * q2) if q2 else q1 * (q1 - 1) / 2
    return observed, observed + (m - 1) / m * extra
