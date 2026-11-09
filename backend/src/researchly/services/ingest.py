"""Paper → sections, chunks and vectors (spec §9.1–9.2). The caller commits; step 3 runs this as a procrastinate job."""

import asyncio
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol

import numpy as np
from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from researchly.db.models import Chunk, ChunkEmbedding, Paper, PaperEmbedding, PaperId, PaperSection
from researchly.ingest.chunking import chunk_text
from researchly.ingest.document import FullText
from researchly.services.fulltext import Target


class TextEmbedder(Protocol):
    model_id: str

    def documents(self, texts: Sequence[str]) -> np.ndarray: ...

    def count_tokens(self, text: str) -> int: ...


class Fetcher(Protocol):
    async def fetch(self, target: Target) -> FullText | None: ...


async def target_for(session: AsyncSession, paper_id: uuid.UUID) -> Target:
    paper = await session.get_one(Paper, paper_id)
    ids = dict((await session.execute(select(PaperId.scheme, PaperId.value).where(PaperId.paper_id == paper_id))).all())
    arxiv_pdf = f"https://arxiv.org/pdf/{ids['arxiv']}" if "arxiv" in ids else None
    pdfs = tuple(dict.fromkeys(u for u in (paper.oa_url, arxiv_pdf) if u))
    return Target(arxiv=ids.get("arxiv"), pmcid=ids.get("pmcid"), pdf_urls=pdfs)


async def ingest_paper(session: AsyncSession, paper_id: uuid.UUID, *, fetcher: Fetcher, embedder: TextEmbedder) -> str:
    full = await fetcher.fetch(await target_for(session, paper_id))
    return await store_fulltext(session, paper_id, full, embedder=embedder)


async def store_fulltext(
    session: AsyncSession, paper_id: uuid.UUID, full: FullText | None, *, embedder: TextEmbedder
) -> str:
    """Everything after the fetch, so a caller can fetch without holding a connection."""
    paper = await session.get_one(Paper, paper_id)
    paper.fulltext_at = datetime.now(UTC)
    if full is None:  # an outage, block or quota looks the same: keep what an earlier ingest stored
        paper.fulltext_source = paper.fulltext_source or "none"
        await session.flush()
        return "none"
    paper.fulltext_source = full.source
    await session.flush()  # the caller's pending rows reach the database before the bulk delete
    await session.execute(
        delete(PaperSection).where(PaperSection.paper_id == paper_id).execution_options(synchronize_session=False)
    )  # cascades to chunks, vectors in the database; new rows get new ids, so stale objects never collide
    if full.license and paper.license is None:
        paper.license, paper.license_source = full.license, full.source
    sections: list[PaperSection] = []
    chunks: list[Chunk] = []
    texts: list[str] = []
    for ord_, sec in enumerate(full.sections):
        section = PaperSection(id=uuid.uuid4(), paper_id=paper_id, ord=ord_, path=list(sec.path), text=sec.text)
        sections.append(section)
        for piece in await asyncio.to_thread(chunk_text, sec.text, embedder.count_tokens):
            chunks.append(
                Chunk(
                    id=uuid.uuid4(),
                    paper_id=paper_id,
                    section_id=section.id,
                    ord=len(chunks),
                    text=piece.text,
                    tokens=piece.tokens,
                )
            )
            texts.append(f"{' > '.join(sec.path)}\n{piece.text}")  # the heading path is cheap context for the vector
    # no relationship() orders the unit of work, so flush parents before children
    session.add_all(sections)
    await session.flush()
    session.add_all(chunks)
    await session.flush()
    if texts:
        vectors = await asyncio.to_thread(embedder.documents, texts)
        session.add_all(
            ChunkEmbedding(chunk_id=c.id, model_id=embedder.model_id, vec=v)
            for c, v in zip(chunks, vectors, strict=True)
        )
    await session.flush()
    return full.source


async def embed_papers(session: AsyncSession, paper_ids: Sequence[uuid.UUID], *, embedder: TextEmbedder) -> int:
    """Title + abstract vectors for every paper (spec §9.2); papers that already have one for this model are skipped."""
    done = exists().where(PaperEmbedding.paper_id == Paper.id, PaperEmbedding.model_id == embedder.model_id)
    rows = (
        await session.execute(select(Paper.id, Paper.title, Paper.abstract).where(Paper.id.in_(paper_ids), ~done))
    ).all()
    if not rows:
        return 0
    vectors = await asyncio.to_thread(embedder.documents, [f"{t}\n{a}" if a else t for _, t, a in rows])
    session.add_all(
        PaperEmbedding(paper_id=pid, model_id=embedder.model_id, vec=v)
        for (pid, _, _), v in zip(rows, vectors, strict=True)
    )
    await session.flush()
    return len(rows)
