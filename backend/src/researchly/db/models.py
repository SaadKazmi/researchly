"""Core tables for step 1 (spec §16). Later steps add tables via new Alembic revisions."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import (
    Boolean,
    DateTime,
    Double,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from researchly.db.base import Base

EMBED_DIM = 1024  # Qwen3-Embedding-0.6B, ADR 0007


class Setting(Base):
    __tablename__ = "setting"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)


class Capability(Base):
    __tablename__ = "capability"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)


class ProviderConfig(Base):
    __tablename__ = "provider_config"
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String)
    base_url: Mapped[str | None] = mapped_column(String)
    models: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)  # role -> model override
    key_ref: Mapped[str | None] = mapped_column(String)  # keychain entry name; never the key itself


class LlmCall(Base):
    __tablename__ = "llm_call"
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID, index=True)
    agent: Mapped[str] = mapped_column(String)
    role: Mapped[str] = mapped_column(String)
    provider: Mapped[str] = mapped_column(String)
    model: Mapped[str] = mapped_column(String)
    in_tok: Mapped[int] = mapped_column(Integer)
    cached_tok: Mapped[int] = mapped_column(Integer)
    out_tok: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    latency_ms: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Venue(Base):
    __tablename__ = "venue"
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text)
    type: Mapped[str | None] = mapped_column(Text)
    tier: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (Index("uq_venue_lower_name", func.lower(name), unique=True),)


class Paper(Base):
    __tablename__ = "paper"
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(Text)
    title_norm: Mapped[str] = mapped_column(Text)
    abstract: Mapped[str | None] = mapped_column(Text)
    year: Mapped[int | None] = mapped_column(Integer)
    venue_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("venue.id"))
    doi: Mapped[str | None] = mapped_column(Text, index=True)
    license: Mapped[str | None] = mapped_column(Text)
    license_source: Mapped[str | None] = mapped_column(Text)
    oa_url: Mapped[str | None] = mapped_column(Text)
    is_oa: Mapped[bool | None] = mapped_column(Boolean)
    cited_by: Mapped[int | None] = mapped_column(Integer)
    fwci: Mapped[float | None] = mapped_column(Double)
    pct: Mapped[float | None] = mapped_column(Double)
    is_retracted: Mapped[bool | None] = mapped_column(Boolean)
    field_sources: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    fulltext_source: Mapped[str | None] = mapped_column(Text)  # arxiv_html | jats | pdf | none (tried, nothing usable)
    fulltext_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        Index(
            "ix_paper_title_norm_trgm",
            "title_norm",
            postgresql_using="gin",
            postgresql_ops={"title_norm": "gin_trgm_ops"},
        ),
    )


class PaperId(Base):
    __tablename__ = "paper_ids"
    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("paper.id", ondelete="CASCADE"), index=True)
    scheme: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, primary_key=True)


class Author(Base):
    __tablename__ = "author"
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text)
    orcid: Mapped[str | None] = mapped_column(Text, unique=True)
    openalex_id: Mapped[str | None] = mapped_column(Text, unique=True)


class PaperAuthor(Base):
    __tablename__ = "paper_author"
    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("paper.id", ondelete="CASCADE"), primary_key=True)
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("author.id"))
    position: Mapped[int] = mapped_column(Integer, primary_key=True)


class PaperSection(Base):
    __tablename__ = "paper_section"
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("paper.id", ondelete="CASCADE"), index=True)
    ord: Mapped[int] = mapped_column(Integer)
    path: Mapped[list[str]] = mapped_column(ARRAY(Text))
    text: Mapped[str] = mapped_column(Text)


# BM25 objects (ix_*_bm25, bm25_tsv) depend on the engine (spec §9.3), so they live in migration 0003 only.
class Chunk(Base):
    __tablename__ = "chunk"
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("paper.id", ondelete="CASCADE"), index=True)
    section_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("paper_section.id", ondelete="CASCADE"), index=True)
    ord: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    context_prefix: Mapped[str | None] = mapped_column(Text)
    tokens: Mapped[int] = mapped_column(Integer)


def _hnsw(name: str) -> Index:
    return Index(
        name,
        "vec",
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"vec": "halfvec_cosine_ops"},
    )


class PaperEmbedding(Base):
    """Title + abstract (spec §9.2)."""

    __tablename__ = "paper_embedding"
    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("paper.id", ondelete="CASCADE"), primary_key=True)
    model_id: Mapped[str] = mapped_column(Text, primary_key=True)
    vec: Mapped[Any] = mapped_column(HALFVEC(EMBED_DIM))
    __table_args__ = (_hnsw("ix_paper_embedding_hnsw"),)


class ChunkEmbedding(Base):
    __tablename__ = "chunk_embedding"
    chunk_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chunk.id", ondelete="CASCADE"), primary_key=True)
    model_id: Mapped[str] = mapped_column(Text, primary_key=True)
    vec: Mapped[Any] = mapped_column(HALFVEC(EMBED_DIM))
    __table_args__ = (_hnsw("ix_chunk_embedding_hnsw"),)


class PaperCode(Base):
    """Papers with Code archive links (CC BY-SA 4.0): a local lookup, never exported or redistributed (spec §8.4)."""

    __tablename__ = "paper_code"
    paper_url: Mapped[str] = mapped_column(Text, primary_key=True)
    repo_url: Mapped[str] = mapped_column(Text, primary_key=True)
    arxiv_id: Mapped[str | None] = mapped_column(Text, index=True)
    title_norm: Mapped[str | None] = mapped_column(Text, index=True)
    is_official: Mapped[bool] = mapped_column(Boolean)
    framework: Mapped[str | None] = mapped_column(Text)
    mentioned_in_paper: Mapped[bool] = mapped_column(Boolean)


# ponytail: expired rows are never pruned; a startup sweep arrives with step 9 maintenance
class HttpCacheRow(Base):
    """Response cache (spec §8.2). UNLOGGED: lost after a crash, by design."""

    __tablename__ = "http_cache"
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    url: Mapped[str] = mapped_column(Text)
    status: Mapped[int] = mapped_column(Integer)
    headers: Mapped[dict[str, str]] = mapped_column(JSONB)
    body: Mapped[bytes] = mapped_column(LargeBinary)
    etag: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = {"prefixes": ["UNLOGGED"]}
