"""Run Alembic programmatically (used by tests and, later, `researchly start`)."""

from pathlib import Path

from alembic import command
from alembic.config import Config

_MIGRATIONS = Path(__file__).with_name("migrations")


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def upgrade(url: str, rev: str = "head") -> None:
    command.upgrade(alembic_config(url), rev)


def downgrade(url: str, rev: str = "base") -> None:
    command.downgrade(alembic_config(url), rev)
