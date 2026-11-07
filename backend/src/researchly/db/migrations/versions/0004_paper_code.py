"""paper_code: the Papers with Code archive's paper-to-repository links

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "paper_code",
        sa.Column("paper_url", sa.Text(), primary_key=True),
        sa.Column("repo_url", sa.Text(), primary_key=True),
        sa.Column("arxiv_id", sa.Text()),
        sa.Column("title_norm", sa.Text()),
        sa.Column("is_official", sa.Boolean(), nullable=False),
        sa.Column("framework", sa.Text()),
        sa.Column("mentioned_in_paper", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_paper_code_arxiv_id", "paper_code", ["arxiv_id"])
    op.create_index("ix_paper_code_title_norm", "paper_code", ["title_norm"])


def downgrade() -> None:
    op.drop_table("paper_code")
