import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

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
