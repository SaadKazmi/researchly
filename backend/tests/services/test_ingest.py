import hashlib
import uuid

import numpy as np
import pytest
from sqlalchemy import func, select

from researchly.db import migrate
from researchly.db.engine import make_engine, make_sessionmaker
from researchly.db.models import EMBED_DIM, Chunk, ChunkEmbedding, Paper, PaperEmbedding, PaperId, PaperSection
from researchly.ingest.document import FullText, Section
from researchly.services.ingest import embed_papers, ingest_paper, target_for

pytestmark = [pytest.mark.db, pytest.mark.anyio]


class FakeEmbedder:
    model_id = "fake"

    def __init__(self):
        self.seen: list[str] = []

    def count_tokens(self, text: str) -> int:
        return len(text.split())

    def documents(self, texts):
        self.seen.extend(texts)
        out = []
        for t in texts:
            seed = int.from_bytes(hashlib.sha256(t.encode()).digest()[:4], "little")
            v = np.random.default_rng(seed).standard_normal(EMBED_DIM).astype(np.float32)
            out.append(v / np.linalg.norm(v))
        return np.array(out)


class StubFetcher:
    def __init__(self, full):
        self.full, self.targets = full, []

    async def fetch(self, target):
        self.targets.append(target)
        return self.full


FULL = FullText(
    "arxiv_html",
    [
        Section(("Abstract",), "We propose the Transformer."),
        Section(("3 Model Architecture", "3.2 Attention"), " ".join(["Attention maps queries to outputs."] * 200)),
    ],
)


@pytest.fixture
async def sm(db_url):
    migrate.upgrade(db_url)
    engine = make_engine(db_url)
    yield make_sessionmaker(engine)
    await engine.dispose()


async def paper(session, **kw) -> uuid.UUID:
    pid = uuid.uuid4()
    session.add(Paper(id=pid, title="Attention Is All You Need", title_norm="attention is all you need", **kw))
    await session.flush()
    return pid


async def count(session, model):
    return await session.scalar(select(func.count()).select_from(model))


async def test_target_uses_ids_oa_url_and_the_arxiv_pdf(sm):
    async with sm() as s:
        pid = await paper(s, oa_url="https://arxiv.org/pdf/1706.03762")
        s.add_all(
            [
                PaperId(paper_id=pid, scheme="arxiv", value="1706.03762"),
                PaperId(paper_id=pid, scheme="pmcid", value="PMC1"),
            ]
        )
        await s.flush()
        target = await target_for(s, pid)
    assert target.arxiv == "1706.03762" and target.pmcid == "PMC1"
    assert target.pdf_urls == ("https://arxiv.org/pdf/1706.03762",)  # the oa_url and the arXiv PDF deduplicated


async def test_ingest_stores_sections_chunks_and_vectors(sm):
    embedder = FakeEmbedder()
    async with sm() as s:
        pid = await paper(s)
        assert await ingest_paper(s, pid, fetcher=StubFetcher(FULL), embedder=embedder) == "arxiv_html"
        await s.commit()
        assert await count(s, PaperSection) == 2
        chunks = (await s.scalars(select(Chunk).order_by(Chunk.ord))).all()
        assert len(chunks) >= 3 and [c.ord for c in chunks] == list(range(len(chunks)))
        assert all(c.tokens <= 512 for c in chunks)
        assert await count(s, ChunkEmbedding) == len(chunks)
        assert embedder.seen[1].startswith("3 Model Architecture > 3.2 Attention\n")  # path prepended for embedding
        row = await s.get(Paper, pid)
        assert row.fulltext_source == "arxiv_html" and row.fulltext_at is not None


async def test_reingest_replaces_rather_than_duplicates(sm):
    async with sm() as s:
        pid = await paper(s)
        await ingest_paper(s, pid, fetcher=StubFetcher(FULL), embedder=FakeEmbedder())
        first = await count(s, Chunk)
        await ingest_paper(s, pid, fetcher=StubFetcher(FULL), embedder=FakeEmbedder())
        assert await count(s, Chunk) == first and await count(s, PaperSection) == 2


async def test_no_full_text_is_recorded_as_none(sm):
    async with sm() as s:
        pid = await paper(s)
        assert await ingest_paper(s, pid, fetcher=StubFetcher(None), embedder=FakeEmbedder()) == "none"
        assert (await s.get(Paper, pid)).fulltext_source == "none" and await count(s, Chunk) == 0


async def test_jats_licence_fills_an_empty_licence_only(sm):
    full = FullText("jats", FULL.sections, license="https://creativecommons.org/licenses/by/4.0/")
    async with sm() as s:
        empty = await paper(s)
        known = await paper(s, license="cc-by-nc")
        for pid in (empty, known):
            await ingest_paper(s, pid, fetcher=StubFetcher(full), embedder=FakeEmbedder())
        assert (await s.get(Paper, empty)).license == "https://creativecommons.org/licenses/by/4.0/"
        assert (await s.get(Paper, known)).license == "cc-by-nc"


async def test_embed_papers_embeds_title_and_abstract_once(sm):
    embedder = FakeEmbedder()
    async with sm() as s:
        a = await paper(s, abstract="We propose the Transformer.")
        b = await paper(s)
        assert await embed_papers(s, [a, b], embedder=embedder) == 2
        assert await embed_papers(s, [a, b], embedder=embedder) == 0
        assert await count(s, PaperEmbedding) == 2
    assert embedder.seen[0] == "Attention Is All You Need\nWe propose the Transformer."
    assert embedder.seen[1] == "Attention Is All You Need"
