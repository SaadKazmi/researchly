"""Hybrid search (spec §9.3): BM25 + dense in one statement, fused by RRF (k = 60); cross-encoder rerank after.

BM25 is pg_textsearch where the server has it (Docker) and a tsvector fallback otherwise (native); migration 0003
built the matching objects. Research: docs/research/2026-11-04-step2b-search.md §3–5.
"""

# ruff: noqa: S608 (SQL is assembled only from module constants; user text is bound)
import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np
from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.ext.asyncio import AsyncSession

from researchly.db.models import EMBED_DIM

RRF_K = 60
CANDIDATES = 100  # per list, before fusion


@dataclass(frozen=True)
class Hit:
    id: uuid.UUID
    score: float
    lexical_rank: int | None
    dense_rank: int | None


@dataclass(frozen=True)
class _Kind:
    table: str
    doc: str  # must repeat the BM25 index expression exactly (pg_textsearch matches on it)
    index: str
    scope: str  # column the caller's id filter applies to
    vectors: str
    vector_key: str


_PAPERS = _Kind(
    "paper", "(title || ' ' || coalesce(abstract, ''))", "ix_paper_bm25", "id", "paper_embedding", "paper_id"
)
_CHUNKS = _Kind("chunk", "text", "ix_chunk_bm25", "paper_id", "chunk_embedding", "chunk_id")


async def bm25_engine(session: AsyncSession) -> str:
    engine = await session.scalar(text("SELECT value #>> '{}' FROM capability WHERE key = 'bm25_engine'"))
    return engine or "tsvector"


def _lexical(kind: _Kind, engine: str, scoped: bool) -> str:
    scope = f"AND {kind.scope} = ANY(:scope)" if scoped else ""
    if engine == "pg_textsearch":
        score = f"{kind.doc} <@> to_bm25query(:q, '{kind.index}')"  # negative; lower is better; non-matches score 0
        return (
            f"SELECT id, row_number() OVER (ORDER BY s) AS r FROM ("
            f"SELECT id, {score} AS s FROM {kind.table} WHERE TRUE {scope} ORDER BY {score} LIMIT :n) b WHERE s < 0"
        )
    # websearch_to_tsquery ANDs every word (0 hits on questions); OR the stemmed lexemes instead (research §3)
    query = "to_tsquery('english', replace(plainto_tsquery('english', :q)::text, ' & ', ' | '))"
    return (
        f"SELECT id, row_number() OVER (ORDER BY s DESC) AS r FROM ("
        f"SELECT id, ts_rank_cd(bm25_tsv, query, 32) AS s FROM {kind.table}, {query} AS query "
        f"WHERE bm25_tsv @@ query {scope} ORDER BY s DESC LIMIT :n) b"
    )


def _dense(kind: _Kind, scoped: bool) -> str:
    join = scope = ""
    if scoped:
        if kind is _CHUNKS:
            join, scope = "JOIN chunk c ON c.id = e.chunk_id", "AND c.paper_id = ANY(:scope)"
        else:
            scope = "AND e.paper_id = ANY(:scope)"
    return (
        f"SELECT id, row_number() OVER (ORDER BY d) AS r FROM ("  # re-sort: relaxed_order is approximate
        f"SELECT e.{kind.vector_key} AS id, e.vec <=> :v AS d FROM {kind.vectors} e {join} "
        f"WHERE e.model_id = :model {scope} ORDER BY e.vec <=> :v LIMIT :n) x"
    )


async def _search(
    session: AsyncSession,
    kind: _Kind,
    query: str,
    vector: np.ndarray,
    model_id: str,
    limit: int,
    scope: Sequence[uuid.UUID] | None,
) -> list[Hit]:
    scoped = scope is not None
    sql = (
        f"WITH lex AS MATERIALIZED ({_lexical(kind, await bm25_engine(session), scoped)}), "
        f"dense AS MATERIALIZED ({_dense(kind, scoped)}) "
        f"SELECT id, coalesce(1.0 / ({RRF_K} + lex.r), 0) + coalesce(1.0 / ({RRF_K} + dense.r), 0) AS score, "
        f"lex.r AS lexical_rank, dense.r AS dense_rank "
        f"FROM lex FULL JOIN dense USING (id) ORDER BY score DESC, id LIMIT :limit"
    )
    stmt = text(sql).bindparams(bindparam("v", type_=HALFVEC(EMBED_DIM)))
    params: dict[str, object] = {"q": query, "v": vector, "model": model_id, "n": CANDIDATES, "limit": limit}
    if scoped:
        stmt = stmt.bindparams(bindparam("scope", type_=ARRAY(UUID(as_uuid=True))))
        params["scope"] = list(scope)
    await session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))  # filters would starve plain HNSW
    await session.execute(text("SET LOCAL hnsw.ef_search = 100"))
    rows = (await session.execute(stmt, params)).all()
    return [Hit(r.id, float(r.score), r.lexical_rank, r.dense_rank) for r in rows]


async def search_papers(
    session: AsyncSession,
    query: str,
    vector: np.ndarray,
    *,
    model_id: str,
    limit: int = 50,
    within: Sequence[uuid.UUID] | None = None,
) -> list[Hit]:
    """Title + abstract search over stored papers."""
    return await _search(session, _PAPERS, query, vector, model_id, limit, within)


async def search_chunks(
    session: AsyncSession,
    query: str,
    vector: np.ndarray,
    *,
    model_id: str,
    limit: int = 50,
    paper_ids: Sequence[uuid.UUID] | None = None,
) -> list[Hit]:
    """Full-text passage search, optionally inside a set of papers."""
    return await _search(session, _CHUNKS, query, vector, model_id, limit, paper_ids)


class Scorer(Protocol):
    def scores(self, query: str, passages: Sequence[str]) -> list[float]: ...


async def rerank(reranker: Scorer, query: str, items: Sequence[tuple[uuid.UUID, str]]) -> list[tuple[uuid.UUID, float]]:
    """Cross-encoder scores, best first. Depth and truncation are the caller's (set at step 4, ADR 0007)."""
    scores = await asyncio.to_thread(reranker.scores, query, [t for _, t in items])
    return sorted(((i, float(s)) for (i, _), s in zip(items, scores, strict=True)), key=lambda p: p[1], reverse=True)
