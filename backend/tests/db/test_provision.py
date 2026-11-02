import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ProgrammingError

from researchly.db import provision

pytestmark = pytest.mark.db
APP_PW, RO_PW = "a" * 20, "r" * 20


@pytest.fixture
def target(db_url):
    name = f"p_{uuid.uuid4().hex[:12]}"
    yield name
    admin = create_engine(db_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def test_setup_creates_database_owned_and_migrated_by_app_role(db_url, target):
    app_url = provision.setup(db_url, APP_PW, RO_PW, database=target)
    url = make_url(app_url)
    assert (url.username, url.password, url.database) == ("researchly_app", APP_PW, target)
    engine = create_engine(app_url)
    with engine.connect() as conn:
        owner = conn.scalar(text("SELECT tableowner FROM pg_tables WHERE tablename = 'capability'"))
        exts = set(conn.scalars(text("SELECT extname FROM pg_extension")))
    engine.dispose()
    assert owner == "researchly_app"
    assert {"vector", "pg_trgm"} <= exts


def test_setup_is_idempotent(db_url, target):
    provision.setup(db_url, APP_PW, RO_PW, database=target)
    provision.setup(db_url, APP_PW, RO_PW, database=target)


def test_read_only_role_reads_but_cannot_write(db_url, target):
    provision.setup(db_url, APP_PW, RO_PW, database=target)
    ro = create_engine(make_url(db_url).set(username="researchly_ro", password=RO_PW, database=target))
    with ro.connect() as conn:
        assert conn.scalar(text("SELECT value FROM capability WHERE key = 'bm25_engine'")) in (
            "tsvector",
            "pg_textsearch",
        )
        with pytest.raises(ProgrammingError):
            conn.execute(text("INSERT INTO setting (key, value) VALUES ('x', '1')"))
    ro.dispose()


@pytest.mark.parametrize("bad", ["short", "x'; DROP ROLE postgres; --" + "a" * 16])
def test_rejects_unsafe_passwords(db_url, target, bad):
    with pytest.raises(ValueError):
        provision.provision(db_url, bad, RO_PW, database=target)


def test_rejects_unsafe_database_name(db_url):
    with pytest.raises(ValueError):
        provision.provision(db_url, APP_PW, RO_PW, database='x"; DROP DATABASE postgres; --')
