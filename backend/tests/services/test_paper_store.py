import pytest
from sqlalchemy import func, select, text

from researchly.connectors.base import Author, ExternalId, PaperRecord
from researchly.db import migrate
from researchly.db.engine import make_engine, make_sessionmaker
from researchly.db.models import Author as AuthorRow
from researchly.db.models import Paper, PaperAuthor, PaperId, Venue
from researchly.services.identity import merge, normalize_title, trigram_similarity
from researchly.services.paper_store import upsert

pytestmark = [pytest.mark.db, pytest.mark.anyio]


@pytest.fixture
async def sm(db_url):
    migrate.upgrade(db_url)
    engine = make_engine(db_url)
    yield make_sessionmaker(engine)
    await engine.dispose()


def rec(source="openalex", title="Attention Is All You Need", *, ids=(), year: int | None = 2017, authors=None, **kw):
    authors = [Author("Ashish Vaswani", orcid="0000-0001-0000-0001")] if authors is None else authors
    return PaperRecord(source=source, ids=list(ids), title=title, year=year, authors=authors, **kw)


async def count(session, model):
    return await session.scalar(select(func.count()).select_from(model))


async def test_new_record_creates_all_rows(sm):
    async with sm() as s:
        pid = await upsert(
            s,
            rec(
                ids=[ExternalId("doi", "https://doi.org/10.1234/ABC"), ExternalId("openalex", "W1")],
                venue="NeurIPS",
                venue_type="conference",
                cited_by=5,
            ),
        )
        await s.commit()
    async with sm() as s:
        paper = await s.get(Paper, pid)
        assert paper and paper.title_norm == "attention is all you need" and paper.doi == "10.1234/abc"
        assert paper.cited_by == 5 and paper.field_sources["title"] == "openalex"
        ids = {(i.scheme, i.value) for i in (await s.scalars(select(PaperId))).all()}
        assert ids == {("doi", "10.1234/abc"), ("openalex", "W1")}
        assert await count(s, AuthorRow) == 1 and await count(s, PaperAuthor) == 1
        venue = (await s.scalars(select(Venue))).one()
        assert venue.name == "NeurIPS" and venue.type == "conference" and paper.venue_id == venue.id


async def test_shared_id_returns_same_paper_and_adds_ids(sm):
    async with sm() as s:
        a = await upsert(s, rec(ids=[ExternalId("openalex", "W1"), ExternalId("arxiv", "1706.03762")]))
        other = rec("arxiv", "Other title", ids=[ExternalId("arxiv", "1706.03762"), ExternalId("openalex", "W2")])
        b = await upsert(s, other)
        await s.commit()
        assert a == b
        values = {i.value for i in (await s.scalars(select(PaperId))).all()}
        assert values == {"W1", "W2", "1706.03762"}


async def test_fuzzy_match_by_title_year_and_first_author(sm):
    async with sm() as s:
        a = await upsert(s, rec(ids=[ExternalId("openalex", "W1")]))
        b = await upsert(
            s, rec("crossref", "Attention is all you need.", year=2018, ids=[ExternalId("doi", "10.1234/x")])
        )
        c = await upsert(s, rec("crossref", "Attention is all you need", year=2018, authors=[Author("Noam Shazeer")]))
        await s.commit()
        assert a == b and c != a
        assert await count(s, Paper) == 2


async def test_lower_priority_fills_nulls_but_never_overwrites(sm):
    async with sm() as s:
        pid = await upsert(s, rec("openalex", ids=[ExternalId("arxiv", "1706.03762")], cited_by=9))
        low = rec(
            "arxiv",
            "Attention Is All You Need v2",
            ids=[ExternalId("arxiv", "1706.03762")],
            cited_by=1,
            abstract="abs",
            license="cc-by",
        )
        await upsert(s, low)
        await s.commit()
        paper = await s.get(Paper, pid)
        assert paper
        await s.refresh(paper)
        assert paper.title == "Attention Is All You Need" and paper.cited_by == 9
        assert paper.abstract == "abs" and paper.license == "cc-by" and paper.license_source == "arxiv"
        assert paper.field_sources["cited_by"] == "openalex" and paper.field_sources["abstract"] == "arxiv"
        high = rec("crossref", ids=[ExternalId("arxiv", "1706.03762")], abstract="better", license="cc0")
        await upsert(s, high)
        await s.refresh(paper)
        assert paper.abstract == "better" and paper.license_source == "crossref"


async def test_same_orcid_is_one_author_row(sm):
    async with sm() as s:
        await upsert(s, rec(ids=[ExternalId("openalex", "W1")]))
        await upsert(s, rec(title="Another paper", year=2020, ids=[ExternalId("openalex", "W2")]))
        await s.commit()
        assert await count(s, Paper) == 2 and await count(s, AuthorRow) == 1 and await count(s, PaperAuthor) == 2


async def test_pg_trgm_similarity_matches_python(sm):
    pairs = [
        ("word", "two words"),
        ("Attention Is All You Need", "Attention is not all you need"),
        ("Réseaux de neurones profonds", "Reseaux de neurones profond!"),
        ("Deep learning: a review", "Deep-learning, a Review?"),
        ("Квантовая механика 2020", "Тепловые потоки 2020"),
    ]
    async with sm() as s:
        for a, b in pairs:
            na, nb = normalize_title(a), normalize_title(b)
            pg = await s.scalar(text("SELECT similarity(:a, :b)"), {"a": na, "b": nb})
            assert round(pg, 6) == round(trigram_similarity(na, nb), 6), (a, b)


async def test_merged_record_keeps_per_field_provenance(sm):
    async with sm() as s:
        ids = [ExternalId("arxiv", "2001.00001")]
        pid = await upsert(s, rec("crossref", ids=ids, abstract="crossref abstract"))
        lead = rec("openalex", ids=ids)
        hf = rec("hf", ids=ids, abstract="hf abstract", license="mit")
        await upsert(s, merge([lead, hf]))
        paper = await s.get(Paper, pid)
        assert paper
        await s.refresh(paper)
        assert paper.abstract == "crossref abstract" and paper.field_sources["abstract"] == "crossref"
        assert paper.license == "mit" and paper.license_source == "hf"


async def test_same_source_refreshes_and_null_never_overwrites(sm):
    async with sm() as s:
        ids = [ExternalId("openalex", "W9")]
        pid = await upsert(s, rec("openalex", ids=ids, cited_by=10, abstract="a"))
        await upsert(s, rec("openalex", ids=ids, cited_by=12))
        paper = await s.get(Paper, pid)
        assert paper
        await s.refresh(paper)
        assert paper.cited_by == 12 and paper.abstract == "a"
        await upsert(s, rec("openalex", ids=ids, cited_by=0, is_oa=False))
        await s.refresh(paper)
        assert paper.cited_by == 0 and paper.is_oa is False
        assert paper.field_sources["authors"] == "openalex"


async def test_fuzzy_misses_and_non_latin_pair(sm):
    ivan = [Author("Иван Петров")]
    async with sm() as s:
        a = await upsert(s, rec(authors=ivan, title="Квантовая механика 2020", year=2020))
        b = await upsert(s, rec(authors=ivan, title="Тепловые потоки 2020", year=2020))
        c = await upsert(s, rec(authors=ivan, title="Квантовая механика 2020", year=2020))
        d = await upsert(s, rec(title="Attention Is All You Need", year=None))
        e = await upsert(s, rec(title="Attention Is All You Need", year=None))
        f = await upsert(s, rec(title="", year=2017))
        g = await upsert(s, rec(title="", year=2017))
        assert a != b and a == c
        assert d != e and f != g


async def test_doi_column_prefers_non_arxiv_doi(sm):
    async with sm() as s:
        ids = [ExternalId("doi", "10.48550/arXiv.1706.03762"), ExternalId("doi", "10.1234/real")]
        pid = await upsert(s, rec(ids=ids))
        paper = await s.get(Paper, pid)
        assert paper and paper.doi == "10.1234/real"


async def test_falsy_values_stored(sm):
    async with sm() as s:
        pid = await upsert(s, rec(cited_by=0, is_oa=False))
        paper = await s.get(Paper, pid)
        assert paper and paper.cited_by == 0 and paper.is_oa is False
