"""baseline: extensions, settings, capabilities, providers, llm ledger

Revision ID: 0001
Revises:
"""

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    available = set(op.get_bind().scalars(sa.text("SELECT name FROM pg_available_extensions")))
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    bm25 = "pg_textsearch" if "pg_textsearch" in available else "tsvector"  # spec §16.1
    if bm25 == "pg_textsearch":
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_textsearch")

    op.create_table(
        "setting", sa.Column("key", sa.String(), primary_key=True), sa.Column("value", JSONB(), nullable=False)
    )
    op.create_table(
        "capability", sa.Column("key", sa.String(), primary_key=True), sa.Column("value", JSONB(), nullable=False)
    )
    op.create_table(
        "provider_config",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("base_url", sa.String()),
        sa.Column("models", JSONB(), nullable=False),
        sa.Column("key_ref", sa.String()),
    )
    op.create_table(
        "llm_call",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("run_id", UUID()),
        sa.Column("agent", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("in_tok", sa.Integer(), nullable=False),
        sa.Column("cached_tok", sa.Integer(), nullable=False),
        sa.Column("out_tok", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_llm_call_run_id", "llm_call", ["run_id"])
    op.execute(
        sa.text("INSERT INTO capability (key, value) VALUES ('bm25_engine', CAST(:v AS jsonb))").bindparams(
            v=json.dumps(bm25)
        )
    )


def downgrade() -> None:
    op.drop_index("ix_llm_call_run_id", table_name="llm_call")
    for table in ("llm_call", "provider_config", "capability", "setting"):
        op.drop_table(table)
    op.execute("DROP EXTENSION IF EXISTS pg_textsearch")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
    op.execute("DROP EXTENSION IF EXISTS vector")
