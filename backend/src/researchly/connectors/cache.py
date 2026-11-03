"""Postgres-backed response cache keyed by a normalized ID (spec §8.2). UNLOGGED: lost after a crash, by design."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from researchly.connectors.http import Fetched
from researchly.db.models import HttpCacheRow

CACHED_HEADERS = frozenset({"etag", "content-type", "content-language", "last-modified"})


@dataclass(frozen=True)
class Cached:
    fetched: Fetched
    etag: str | None
    fresh: bool


class HttpCache:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def get(self, key: str) -> Cached | None:
        async with self._sessions() as session:
            row = await session.scalar(select(HttpCacheRow).where(HttpCacheRow.key == key))
        if row is None:
            return None
        fetched = Fetched(row.status, dict(row.headers), row.body, row.url)
        return Cached(fetched, row.etag, row.expires_at > datetime.now(UTC))

    async def put(self, key: str, fetched: Fetched, ttl: timedelta) -> None:
        headers = {k: v for k, v in fetched.headers.items() if k.lower() in CACHED_HEADERS}
        values = {
            "url": urlsplit(fetched.url)._replace(query="", fragment="").geturl(),  # never persist query params
            "status": fetched.status,
            "headers": headers,
            "body": fetched.body,
            "etag": headers.get("etag"),
            "expires_at": datetime.now(UTC) + ttl,
        }
        stmt = insert(HttpCacheRow).values(key=key, **values)
        async with self._sessions() as session, session.begin():
            await session.execute(stmt.on_conflict_do_update(index_elements=["key"], set_=values))

    async def refresh(self, key: str, ttl: timedelta) -> None:
        async with self._sessions() as session, session.begin():
            await session.execute(
                update(HttpCacheRow).where(HttpCacheRow.key == key).values(expires_at=datetime.now(UTC) + ttl)
            )
