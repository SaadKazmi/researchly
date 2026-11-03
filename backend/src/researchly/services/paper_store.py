"""Paper store (spec §8.3): upsert a PaperRecord, resolving identity against what is already stored."""

import uuid

from sqlalchemy import func, literal, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from researchly.connectors import ids as idn
from researchly.connectors.base import Author as AuthorRecord
from researchly.connectors.base import ExternalId, PaperRecord
from researchly.db.models import Author, Paper, PaperAuthor, PaperId, Venue
from researchly.services.identity import (
    MERGED_FIELDS,
    TITLE_SIMILARITY,
    first_surname,
    normalize_title,
    source_rank,
    surname,
)

_ARXIV_DOI_PREFIX = "10.48550/arxiv."
_PAPER_FIELDS = tuple(f for f in MERGED_FIELDS if f not in ("authors", "venue", "venue_type"))


def _normalized(record: PaperRecord) -> list[ExternalId]:
    return sorted({n for i in record.ids if (n := idn.normalize(i))})


def _doi(ids: list[ExternalId]) -> str | None:
    dois = [i.value for i in ids if i.scheme == "doi"]
    return next((d for d in dois if not d.startswith(_ARXIV_DOI_PREFIX)), dois[0] if dois else None)


async def _find(session: AsyncSession, record: PaperRecord, ids: list[ExternalId]) -> uuid.UUID | None:
    if ids:
        found = await session.scalar(
            select(PaperId.paper_id).where(tuple_(PaperId.scheme, PaperId.value).in_([tuple(i) for i in ids])).limit(1)
        )  # ponytail: two stored papers sharing an ID are not merged here; a later merge job can
        if found:
            return found
    surname_ = first_surname(record)
    if record.year is None or not surname_:
        return None
    await session.execute(text(f"SET LOCAL pg_trgm.similarity_threshold = {TITLE_SIMILARITY}"))
    candidates = await session.execute(
        select(Paper.id, Author.name)
        .join(PaperAuthor, (PaperAuthor.paper_id == Paper.id) & (PaperAuthor.position == 0))
        .join(Author, Author.id == PaperAuthor.author_id)
        .where(Paper.title_norm.op("%")(normalize_title(record.title)))
        .where(Paper.year.between(record.year - 1, record.year + 1))
    )
    return next((pid for pid, name in candidates if surname(name) == surname_), None)


async def _venue_id(session: AsyncSession, name: str, venue_type: str | None) -> uuid.UUID:
    await session.execute(insert(Venue).values(name=name, type=venue_type).on_conflict_do_nothing())
    venue = (await session.scalars(select(Venue).where(func.lower(Venue.name) == func.lower(literal(name))))).one()
    if venue.type is None and venue_type:
        venue.type = venue_type
    return venue.id


async def _author_id(session: AsyncSession, author: AuthorRecord) -> uuid.UUID:
    for column, value in ((Author.orcid, author.orcid), (Author.openalex_id, author.openalex_id)):
        if value:
            found = await session.scalar(select(Author.id).where(column == value))
            if found:
                return found
    row = Author(name=author.name, orcid=author.orcid, openalex_id=author.openalex_id)
    session.add(row)
    await session.flush()
    return row.id


async def upsert(session: AsyncSession, record: PaperRecord) -> uuid.UUID:
    """Create or enrich the paper for `record`; the caller commits."""
    ids = _normalized(record)
    paper_id = await _find(session, record, ids)
    paper = await session.get(Paper, paper_id) if paper_id else None
    if paper is None:
        paper = Paper(title=record.title, title_norm=normalize_title(record.title), field_sources={})
        session.add(paper)
        await session.flush()

    sources = dict(paper.field_sources)

    def wins(field: str, has_value: bool) -> bool:
        return has_value and (field not in sources or source_rank(record.source) < source_rank(sources[field]))

    for name in _PAPER_FIELDS:
        value = getattr(record, name)
        if value not in (None, "") and (getattr(paper, name) is None or wins(name, True)):
            setattr(paper, name, value)
            sources[name] = record.source
    paper.title_norm = normalize_title(paper.title)
    if record.venue and wins("venue", True):
        paper.venue_id = await _venue_id(session, record.venue, record.venue_type)
        sources["venue"] = record.source
    paper.field_sources = sources
    paper.license_source = sources.get("license")
    doi = _doi(ids)
    if doi and (
        paper.doi is None or (paper.doi.startswith(_ARXIV_DOI_PREFIX) and not doi.startswith(_ARXIV_DOI_PREFIX))
    ):
        paper.doi = doi
    paper.updated_at = func.now()

    if ids:
        await session.execute(
            insert(PaperId)
            .values([{"paper_id": paper.id, "scheme": i.scheme, "value": i.value} for i in ids])
            .on_conflict_do_nothing()
        )
    has_authors = await session.scalar(select(PaperAuthor.paper_id).where(PaperAuthor.paper_id == paper.id).limit(1))
    if not has_authors:
        for position, author in enumerate(record.authors):
            session.add(PaperAuthor(paper_id=paper.id, author_id=await _author_id(session, author), position=position))
    await session.flush()
    return paper.id
