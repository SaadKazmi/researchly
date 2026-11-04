"""full text: sections, chunks, title/abstract and chunk vectors, BM25 by engine

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import HALFVEC
from sqlalchemy.dialects.postgresql import ARRAY, UUID

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

# retrieval.py repeats this exactly: pg_textsearch matches the expression
PAPER_DOC = "(title || ' ' || coalesce(abstract, ''))"


def upgrade() -> None:
    op.add_column("paper", sa.Column("fulltext_source", sa.Text()))
    op.add_column("paper", sa.Column("fulltext_at", sa.DateTime(timezone=True)))
    op.create_table(
        "paper_section",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("paper_id", UUID(), sa.ForeignKey("paper.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ord", sa.Integer(), nullable=False),
        sa.Column("path", ARRAY(sa.Text()), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
    )
    op.create_index("ix_paper_section_paper_id", "paper_section", ["paper_id"])
    op.create_table(
        "chunk",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("paper_id", UUID(), sa.ForeignKey("paper.id", ondelete="CASCADE"), nullable=False),
        sa.Column("section_id", UUID(), sa.ForeignKey("paper_section.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ord", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("context_prefix", sa.Text()),
        sa.Column("tokens", sa.Integer(), nullable=False),
    )
    op.create_index("ix_chunk_paper_id", "chunk", ["paper_id"])
    op.create_index("ix_chunk_section_id", "chunk", ["section_id"])
    for table, key, ref in (("paper_embedding", "paper_id", "paper.id"), ("chunk_embedding", "chunk_id", "chunk.id")):
        op.create_table(
            table,
            sa.Column(key, UUID(), sa.ForeignKey(ref, ondelete="CASCADE"), primary_key=True),
            sa.Column("model_id", sa.Text(), primary_key=True),
            sa.Column("vec", HALFVEC(1024), nullable=False),
        )
        op.execute(
            f"CREATE INDEX ix_{table}_hnsw ON {table} "
            "USING hnsw (vec halfvec_cosine_ops) WITH (m = 16, ef_construction = 64)"
        )
    engine = op.get_bind().scalar(sa.text("SELECT value #>> '{}' FROM capability WHERE key = 'bm25_engine'"))
    if engine == "pg_textsearch":
        op.execute(f"CREATE INDEX ix_paper_bm25 ON paper USING bm25 ({PAPER_DOC}) WITH (text_config = 'english')")
        op.execute("CREATE INDEX ix_chunk_bm25 ON chunk USING bm25 (text) WITH (text_config = 'english')")
    else:
        op.execute(
            "ALTER TABLE paper ADD COLUMN bm25_tsv tsvector "
            f"GENERATED ALWAYS AS (to_tsvector('english', {PAPER_DOC})) STORED"
        )
        op.execute("CREATE INDEX ix_paper_bm25 ON paper USING gin (bm25_tsv)")
        op.execute(
            "ALTER TABLE chunk ADD COLUMN bm25_tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED"
        )
        op.execute("CREATE INDEX ix_chunk_bm25 ON chunk USING gin (bm25_tsv)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_paper_bm25")
    op.execute("ALTER TABLE paper DROP COLUMN IF EXISTS bm25_tsv")
    op.drop_table("chunk_embedding")
    op.drop_table("paper_embedding")
    op.drop_table("chunk")
    op.drop_table("paper_section")
    op.drop_column("paper", "fulltext_at")
    op.drop_column("paper", "fulltext_source")
