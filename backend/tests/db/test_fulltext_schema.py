import os
import uuid

import numpy as np
import pytest
from sqlalchemy import create_engine, delete, func, select, text

from researchly.db import migrate
from researchly.db.engine import make_engine, make_sessionmaker
from researchly.db.models import EMBED_DIM, Chunk, ChunkEmbedding, Paper, PaperEmbedding, PaperSection

pytestmark = [pytest.mark.db, pytest.mark.anyio]


@pytest.fixture
async def sm(db_url):
    migrate.upgrade(db_url)
    engine = make_engine(db_url)
    yield make_sessionmaker(engine)
    await engine.dispose()


def unit(i: int) -> np.ndarray:
    v = np.zeros(EMBED_DIM, dtype=np.float32)
    v[i] = 1.0
    return v


async def test_rows_round_trip_and_cascade_from_the_paper(sm):
    pid, sid, cid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with sm() as s:
        s.add(Paper(id=pid, title="Attention Is All You Need", title_norm="attention is all you need"))
        await s.flush()  # no relationship() between the models, so the unit of work does not order the inserts
        s.add(PaperSection(id=sid, paper_id=pid, ord=0, path=["3 Model Architecture", "3.2 Attention"], text="Scaled."))
        await s.flush()
        s.add(Chunk(id=cid, paper_id=pid, section_id=sid, ord=0, text="Scaled dot-product attention.", tokens=5))
        await s.flush()
        s.add(PaperEmbedding(paper_id=pid, model_id="m", vec=unit(0)))
        s.add(ChunkEmbedding(chunk_id=cid, model_id="m", vec=unit(1)))
        await s.commit()
        nearest = await s.scalar(
            select(ChunkEmbedding.chunk_id).order_by(ChunkEmbedding.vec.cosine_distance(unit(1))).limit(1)
        )
        assert nearest == cid
        assert (await s.get(PaperSection, sid)).path == ["3 Model Architecture", "3.2 Attention"]
        await s.execute(delete(Paper).where(Paper.id == pid))
        await s.commit()
        for model in (PaperSection, Chunk, PaperEmbedding, ChunkEmbedding):
            assert await s.scalar(select(func.count()).select_from(model)) == 0


async def test_bm25_objects_match_the_recorded_engine(db_url, sm):
    expected = os.environ.get("RESEARCHLY_EXPECT_BM25", "tsvector")  # the backend-bm25 CI job sets pg_textsearch
    engine = create_engine(db_url)
    with engine.connect() as conn:
        recorded = conn.scalar(text("SELECT value #>> '{}' FROM capability WHERE key = 'bm25_engine'"))
        indexes = dict(
            conn.execute(text("SELECT indexname, indexdef FROM pg_indexes WHERE indexname LIKE '%bm25'")).all()
        )
        tsv_columns = set(
            conn.scalars(text("SELECT table_name FROM information_schema.columns WHERE column_name = 'bm25_tsv'"))
        )
    engine.dispose()
    assert recorded == expected
    assert set(indexes) == {"ix_paper_bm25", "ix_chunk_bm25"}
    if expected == "pg_textsearch":
        assert all("USING bm25" in d for d in indexes.values()) and tsv_columns == set()
    else:
        assert all("USING gin" in d for d in indexes.values()) and tsv_columns == {"paper", "chunk"}


async def test_vector_indexes_are_hnsw_cosine(db_url, sm):
    engine = create_engine(db_url)
    with engine.connect() as conn:
        defs = list(conn.scalars(text("SELECT indexdef FROM pg_indexes WHERE indexname LIKE '%hnsw'")))
    engine.dispose()
    assert len(defs) == 2 and all("hnsw" in d and "halfvec_cosine_ops" in d for d in defs)
