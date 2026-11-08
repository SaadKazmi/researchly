"""Side-effecting tool results keyed by sha256(tool, args) (spec §6.5): a resumed or replayed step reuses them."""

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from researchly.db.models import ToolCache


def cache_key(tool: str, args: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps([tool, args], sort_keys=True, default=str).encode()).hexdigest()


async def get(session: AsyncSession, key: str) -> Any | None:
    # expiry is judged by the database clock, which also wrote created_at
    age = func.extract("epoch", func.now() - ToolCache.created_at)
    fresh = or_(ToolCache.ttl.is_(None), age < ToolCache.ttl)
    return await session.scalar(select(ToolCache.result).where(ToolCache.key == key, fresh))


async def put(session: AsyncSession, key: str, tool: str, result: Any, *, ttl: int | None = None) -> None:
    stmt = insert(ToolCache).values(key=key, tool=tool, result=result, ttl=ttl)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[ToolCache.key],
            set_={
                "tool": stmt.excluded.tool,
                "result": stmt.excluded.result,
                "ttl": stmt.excluded.ttl,
                "created_at": func.now(),
            },
        )
    )
