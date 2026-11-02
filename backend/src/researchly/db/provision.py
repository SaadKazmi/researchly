"""Database provisioning (spec §16.1): roles, database and untrusted extensions, created as the superuser.

The CLI calls setup() before starting the app (native mode); Docker mode runs `python -m researchly.db.provision`
as a one-shot service with RESEARCHLY_DB_SUPERUSER_URL / _APP_PASSWORD / _RO_PASSWORD in the environment.
"""

import os
import re

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from researchly.db import migrate

APP_ROLE = "researchly_app"
RO_ROLE = "researchly_ro"
_PASSWORD = re.compile(r"[A-Za-z0-9_-]{16,}")  # token_urlsafe output; DDL cannot take bind parameters
_DATABASE = re.compile(r"[a-z_][a-z0-9_]{0,62}")


def provision(superuser_url: str, app_password: str, ro_password: str, database: str = "researchly") -> str:
    """Idempotent. Returns the app-role URL for `database`."""
    if not all(_PASSWORD.fullmatch(pw) for pw in (app_password, ro_password)):
        raise ValueError("database passwords must be 16+ URL-safe characters")
    if not _DATABASE.fullmatch(database):
        raise ValueError(f"invalid database name {database!r}")
    admin = create_engine(superuser_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            for role, password in ((APP_ROLE, app_password), (RO_ROLE, ro_password)):
                exists = conn.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role})
                conn.execute(text(f"{'ALTER' if exists else 'CREATE'} ROLE {role} LOGIN PASSWORD '{password}'"))
            conn.execute(text(f"GRANT pg_read_all_data TO {RO_ROLE}"))
            if not conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :d"), {"d": database}):
                conn.execute(text(f'CREATE DATABASE "{database}" OWNER {APP_ROLE}'))
    finally:
        admin.dispose()
    # Untrusted extensions need the superuser, inside the target database; the baseline migration's
    # CREATE EXTENSION IF NOT EXISTS is then a no-op for them. pg_trgm is trusted: the migration creates it.
    db_admin = create_engine(make_url(superuser_url).set(database=database), isolation_level="AUTOCOMMIT")
    try:
        with db_admin.connect() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            if "pg_textsearch" in (conn.scalar(text("SHOW shared_preload_libraries")) or ""):
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_textsearch"))
    finally:
        db_admin.dispose()
    app_url = make_url(superuser_url).set(username=APP_ROLE, password=app_password, database=database)
    return app_url.render_as_string(hide_password=False)


def setup(superuser_url: str, app_password: str, ro_password: str, database: str = "researchly") -> str:
    """Provision, then migrate as the schema owner. Returns the app-role URL."""
    app_url = provision(superuser_url, app_password, ro_password, database)
    migrate.upgrade(app_url)
    return app_url


if __name__ == "__main__":
    env = os.environ
    setup(env["RESEARCHLY_DB_SUPERUSER_URL"], env["RESEARCHLY_DB_APP_PASSWORD"], env["RESEARCHLY_DB_RO_PASSWORD"])
