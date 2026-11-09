"""Projects, threads, messages, runs and their events, evidence cards and the tool cache (spec §16)

LangGraph's checkpoint and store tables are not here: provisioning creates them with their own setup() (ADR 0011).

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

STATUSES = "'pending','planning','awaiting_user','running','paused','partial','completed','cancelled','failed'"


def _created() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "project",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("settings", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        _created(),
    )
    op.create_table(
        "thread",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("project_id", UUID(), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.Text()),
        _created(),
    )
    op.create_index("ix_thread_project_id", "thread", ["project_id"])
    op.create_table(
        "run",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("project_id", UUID(), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("thread_id", UUID(), sa.ForeignKey("thread.id", ondelete="CASCADE"), nullable=False),
        sa.Column("effort", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("plan", JSONB()),
        sa.Column("budget_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("spent_usd", sa.Numeric(12, 6), server_default="0", nullable=False),
        sa.Column("checkpoint_thread_id", sa.Text(), nullable=False),
        sa.Column("parent_run_id", UUID(), sa.ForeignKey("run.id", ondelete="SET NULL")),
        sa.Column("branch_from_checkpoint", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(f"status IN ({STATUSES})", name="ck_run_status"),
        sa.CheckConstraint("effort IN ('quick','standard','deep')", name="ck_run_effort"),
    )
    op.create_index("ix_run_project_id", "run", ["project_id"])
    op.create_index("ix_run_thread_id", "run", ["thread_id"])
    op.create_index("ix_run_checkpoint_thread_id", "run", ["checkpoint_thread_id"])
    op.create_table(
        "message",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("thread_id", UUID(), sa.ForeignKey("thread.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", JSONB(), nullable=False),
        sa.Column("run_id", UUID(), sa.ForeignKey("run.id", ondelete="SET NULL")),
        _created(),
    )
    op.create_index("ix_message_thread_id", "message", ["thread_id"])
    op.create_index("ix_message_run_id", "message", ["run_id"])
    op.create_table(
        "run_event",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("run_id", UUID(), sa.ForeignKey("run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("agent", sa.Text()),
        sa.Column("payload", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("run_id", "seq", name="uq_run_event_run_id"),
    )
    op.create_table(
        "evidence",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("run_id", UUID(), sa.ForeignKey("run.id", ondelete="SET NULL")),
        sa.Column("project_id", UUID(), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("paper_id", UUID(), sa.ForeignKey("paper.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_id", UUID(), sa.ForeignKey("chunk.id", ondelete="SET NULL")),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("claim", sa.Text(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("quote_start", sa.Integer(), nullable=False),
        sa.Column("quote_end", sa.Integer(), nullable=False),
        sa.Column("relevance", sa.SmallInteger(), nullable=False),
        _created(),
        sa.CheckConstraint("relevance BETWEEN 0 AND 10", name="ck_evidence_relevance"),
    )
    op.create_index("ix_evidence_run_id", "evidence", ["run_id"])
    op.create_index("ix_evidence_project_id", "evidence", ["project_id"])
    op.create_index("ix_evidence_paper_id", "evidence", ["paper_id"])
    op.create_table(
        "tool_cache",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("tool", sa.Text(), nullable=False),
        sa.Column("result", JSONB(), nullable=False),
        _created(),
        sa.Column("ttl", sa.Integer()),  # seconds; null never expires
    )


def downgrade() -> None:
    for table in ("tool_cache", "evidence", "run_event", "message", "run", "thread", "project"):
        op.drop_table(table)
