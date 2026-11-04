import uuid

import numpy as np
import pytest

from researchly.db import migrate
from researchly.db.engine import make_engine, make_sessionmaker
from researchly.db.models import EMBED_DIM, Chunk, ChunkEmbedding, Paper, PaperEmbedding, PaperSection
from researchly.services.retrieval import bm25_engine, rerank, search_chunks, search_papers

pytestmark = [pytest.mark.db, pytest.mark.anyio]


def unit(i: int) -> np.ndarray:
    v = np.zeros(EMBED_DIM, dtype=np.float32)
    v[i] = 1.0
    return v


def near(i: int, j: int, w: float) -> np.ndarray:  # mostly axis i, a little of axis j
    v = unit(i) + w * unit(j)
    return v / np.linalg.norm(v)


@pytest.fixture
async def sm(db_url):
    migrate.upgrade(db_url)
    engine = make_engine(db_url)
    yield make_sessionmaker(engine)
    await engine.dispose()


PAPERS = {
    "rag": (
        "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks",
        "We combine retrieval with generation.",
        unit(0),
    ),
    "dpr": (
        "Dense Passage Retrieval for Open-Domain Question Answering",
        "Dense retrieval of passages.",
        near(0, 1, 0.3),
    ),
    "cnn": (
        "ImageNet Classification with Deep Convolutional Neural Networks",
        "Convolutional nets for images.",
        unit(5),
    ),
    "bio": ("CRISPR-Cas9 genome editing in human cells", "Gene editing with guide RNA.", unit(7)),
}


async def seed(s) -> dict[str, uuid.UUID]:
    ids = {}
    for key, (title, abstract, _) in PAPERS.items():
        pid = ids[key] = uuid.uuid4()
        s.add(Paper(id=pid, title=title, title_norm=title.lower(), abstract=abstract))
    await s.flush()
    for key, (_, _, vec) in PAPERS.items():
        s.add(PaperEmbedding(paper_id=ids[key], model_id="m", vec=vec))
        s.add(PaperEmbedding(paper_id=ids[key], model_id="other", vec=unit(9)))
    await s.commit()
    return ids


async def test_engine_is_read_from_the_capability(sm):
    async with sm() as s:
        assert await bm25_engine(s) in {"tsvector", "pg_textsearch"}


async def test_natural_language_query_fuses_lexical_and_dense(sm):
    async with sm() as s:
        ids = await seed(s)
        hits = await search_papers(
            s, "how does retrieval augmented generation reduce hallucination", unit(0), model_id="m"
        )
    by_id = {h.id: h for h in hits}
    assert hits[0].id == ids["rag"]  # first in both lists
    assert by_id[ids["rag"]].lexical_rank == 1 and by_id[ids["rag"]].dense_rank == 1
    assert ids["dpr"] in by_id  # lexical "retrieval" and second densely
    assert by_id[ids["rag"]].score == pytest.approx(2 / 61)


async def test_dense_only_and_lexical_only_matches_both_surface(sm):
    async with sm() as s:
        ids = await seed(s)
        hits = await search_papers(s, "genome editing", unit(5), model_id="m", limit=10)
    by_id = {h.id: h for h in hits}
    assert by_id[ids["bio"]].lexical_rank == 1 and by_id[ids["bio"]].dense_rank != 1
    assert by_id[ids["cnn"]].dense_rank == 1 and by_id[ids["cnn"]].lexical_rank is None


async def test_scope_and_model_filters(sm):
    async with sm() as s:
        ids = await seed(s)
        hits = await search_papers(s, "retrieval", unit(0), model_id="m", within=[ids["dpr"], ids["cnn"]])
        assert {h.id for h in hits} <= {ids["dpr"], ids["cnn"]} and hits[0].id == ids["dpr"]
        other = await search_papers(s, "zzzz", unit(9), model_id="other", limit=10)
        assert len(other) == 4 and all(h.lexical_rank is None for h in other)


async def test_chunk_search_and_paper_scope(sm):
    async with sm() as s:
        ids = await seed(s)
        chunk_ids = {}
        rows = [
            ("rag", "The retriever fetches passages from Wikipedia.", unit(0)),
            ("cnn", "Dropout reduces overfitting.", unit(5)),
        ]
        sections = {}
        for key, text, _ in rows:
            sections[key] = uuid.uuid4()
            s.add(PaperSection(id=sections[key], paper_id=ids[key], ord=0, path=["Method"], text=text))
        await s.flush()
        for n, (key, text, _) in enumerate(rows):
            chunk_ids[key] = uuid.uuid4()
            s.add(Chunk(id=chunk_ids[key], paper_id=ids[key], section_id=sections[key], ord=n, text=text, tokens=6))
        await s.flush()
        for key, _, vec in rows:
            s.add(ChunkEmbedding(chunk_id=chunk_ids[key], model_id="m", vec=vec))
        await s.commit()
        hits = await search_chunks(s, "passages from wikipedia", unit(0), model_id="m")
        assert hits[0].id == chunk_ids["rag"]
        scoped = await search_chunks(s, "passages from wikipedia", unit(0), model_id="m", paper_ids=[ids["cnn"]])
        assert [h.id for h in scoped] == [chunk_ids["cnn"]]


async def test_rerank_orders_by_cross_encoder_score():
    class Fixed:
        def scores(self, query, passages):
            return [float(len(p)) for p in passages]

    a, b = uuid.uuid4(), uuid.uuid4()
    assert await rerank(Fixed(), "q", [(a, "short"), (b, "a longer passage")]) == [(b, 16.0), (a, 5.0)]
