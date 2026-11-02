import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from researchly.db import migrate
from researchly.db.base import Base

pytestmark = pytest.mark.db
TABLES = {"setting", "capability", "provider_config", "llm_call"}


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
    engine = create_engine(db_url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
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
