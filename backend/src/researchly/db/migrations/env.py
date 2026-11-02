from alembic import context
from sqlalchemy import create_engine, pool

from researchly.config import Settings
from researchly.db import models  # noqa: F401  (registers tables on Base.metadata)
from researchly.db.base import Base

url = context.config.get_main_option("sqlalchemy.url") or Settings().database_url
engine = create_engine(url, poolclass=pool.NullPool)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
