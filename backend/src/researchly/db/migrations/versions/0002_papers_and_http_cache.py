"""papers, authors, venues and the UNLOGGED http_cache

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "venue",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("type", sa.Text()),
        sa.Column("tier", sa.Text()),
    )
    op.create_index("uq_venue_lower_name", "venue", [sa.text("lower(name)")], unique=True)
    op.create_table(
        "paper",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("title_norm", sa.Text(), nullable=False),
        sa.Column("abstract", sa.Text()),
        sa.Column("year", sa.Integer()),
        sa.Column("venue_id", UUID(), sa.ForeignKey("venue.id")),
        sa.Column("doi", sa.Text()),
        sa.Column("license", sa.Text()),
        sa.Column("license_source", sa.Text()),
        sa.Column("oa_url", sa.Text()),
        sa.Column("is_oa", sa.Boolean()),
        sa.Column("cited_by", sa.Integer()),
        sa.Column("fwci", sa.Double()),
        sa.Column("pct", sa.Double()),
        sa.Column("is_retracted", sa.Boolean()),
        sa.Column("field_sources", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_paper_doi", "paper", ["doi"])
    op.create_index(
        "ix_paper_title_norm_trgm",
        "paper",
        ["title_norm"],
        postgresql_using="gin",
        postgresql_ops={"title_norm": "gin_trgm_ops"},
    )
    op.create_table(
        "paper_ids",
        sa.Column("paper_id", UUID(), sa.ForeignKey("paper.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scheme", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("scheme", "value"),
    )
    op.create_index("ix_paper_ids_paper_id", "paper_ids", ["paper_id"])
    op.create_table(
        "author",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("orcid", sa.Text(), unique=True),
        sa.Column("openalex_id", sa.Text(), unique=True),
    )
    op.create_table(
        "paper_author",
        sa.Column("paper_id", UUID(), sa.ForeignKey("paper.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_id", UUID(), sa.ForeignKey("author.id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("paper_id", "position"),
    )
    op.create_table(
        "http_cache",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("headers", JSONB(), nullable=False),
        sa.Column("body", sa.LargeBinary(), nullable=False),
        sa.Column("etag", sa.Text()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        prefixes=["UNLOGGED"],
    )


def downgrade() -> None:
    for table in ("http_cache", "paper_author", "author", "paper_ids", "paper", "venue"):
        op.drop_table(table)
