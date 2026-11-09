import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from researchly.db import migrate, provision
from researchly.db.base import Base

pytestmark = pytest.mark.db
TABLES = {
    "paper_code",
    "setting",
    "capability",
    "provider_config",
    "llm_call",
    "venue",
    "paper",
    "paper_ids",
    "author",
    "paper_author",
    "http_cache",
    "project",
    "thread",
    "message",
    "run",
    "run_event",
    "evidence",
    "tool_cache",
}


def _ours(obj, name, type_, reflected, compare_to):
    # engine-specific BM25 objects live in migration 0003 only (spec §9.3); LangGraph owns its tables (ADR 0011)
    table = name if type_ == "table" else getattr(getattr(obj, "table", None), "name", None)
    return not (name and "bm25" in name) and table not in provision.AGENT_TABLES


def test_upgrade_downgrade_upgrade(db_url):
    migrate.upgrade(db_url)
    migrate.downgrade(db_url)
    engine = create_engine(db_url)
    assert TABLES.isdisjoint(inspect(engine).get_table_names())
    migrate.upgrade(db_url)
    assert set(inspect(engine).get_table_names()) >= TABLES
    engine.dispose()


def test_baseline_extensions_and_bm25_capability(db_url):
    migrate.upgrade(db_url)
    engine = create_engine(db_url)
    with engine.connect() as conn:
        exts = set(conn.scalars(text("SELECT extname FROM pg_extension")))
        bm25 = conn.scalar(text("SELECT value FROM capability WHERE key = 'bm25_engine'"))
    engine.dispose()
    assert {"vector", "pg_trgm"} <= exts
    assert bm25 == ("pg_textsearch" if "pg_textsearch" in exts else "tsvector")


def test_models_match_migrations(db_url):
    migrate.upgrade(db_url)
    provision.setup_agent_tables(db_url)  # present in every real database; must not show up as drift
    engine = create_engine(db_url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"include_object": _ours}), Base.metadata)
    engine.dispose()
    assert diff == []


def test_migrations_run_as_non_superuser_owner(db_url):
    # spec §16.1: researchly_app owns the schema; provisioning creates the untrusted `vector` as superuser
    role = f"owner_{make_url(db_url).database}"  # unique per test database; never collides across runs
    admin = create_engine(db_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(text(f"CREATE ROLE {role} LOGIN PASSWORD 'app'"))
        conn.execute(text(f'ALTER DATABASE "{make_url(db_url).database}" OWNER TO {role}'))
        conn.execute(text(f"ALTER SCHEMA public OWNER TO {role}"))
    admin.dispose()
    owner_url = make_url(db_url).set(username=role, password="app").render_as_string(hide_password=False)  # noqa: S106
    migrate.upgrade(owner_url)
    migrate.downgrade(owner_url)
    migrate.upgrade(owner_url)


RUN_SQL = (
    "INSERT INTO run (id, project_id, thread_id, effort, status, budget_usd, checkpoint_thread_id) "
    "VALUES (gen_random_uuid(), :p, :t, :effort, :status, 1, 'x')"
)


def test_run_status_effort_and_evidence_relevance_are_checked(db_url):
    migrate.upgrade(db_url)
    engine = create_engine(db_url)
    with engine.connect() as conn:
        p = conn.scalar(text("INSERT INTO project (id, name) VALUES (gen_random_uuid(), 'p') RETURNING id"))
        t = conn.scalar(
            text("INSERT INTO thread (id, project_id) VALUES (gen_random_uuid(), :p) RETURNING id"), {"p": p}
        )
        conn.execute(text(RUN_SQL), {"p": p, "t": t, "effort": "standard", "status": "pending"})
        for effort, status in (("standard", "done"), ("huge", "pending")):
            with pytest.raises(IntegrityError), conn.begin_nested():
                conn.execute(text(RUN_SQL), {"p": p, "t": t, "effort": effort, "status": status})
        paper = conn.scalar(
            text("INSERT INTO paper (id, title, title_norm) VALUES (gen_random_uuid(), 'a', 'a') RETURNING id")
        )
        evidence = (
            "INSERT INTO evidence (id, project_id, paper_id, question, claim, quote, "
            "quote_start, quote_end, relevance) "
            "VALUES (gen_random_uuid(), :p, :paper, 'q', 'c', 'quote', 0, 5, :r)"
        )
        conn.execute(text(evidence), {"p": p, "paper": paper, "r": 10})
        with pytest.raises(IntegrityError), conn.begin_nested():
            conn.execute(text(evidence), {"p": p, "paper": paper, "r": 11})
    engine.dispose()
