"""Runtime capabilities (spec §4.1): what this install can do, kept in the `capability` table for services and the UI.

Web search is probed in the background: SearXNG becomes healthy seconds after the app (native 12-15 s, Docker
5-7 s), so a check at startup would always say off (ADR 0010).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import insert

from researchly.connectors.base import ConnectorError
from researchly.connectors.sidecar import SidecarClient
from researchly.db.models import Capability

logger = logging.getLogger(__name__)


async def probe_web_search(
    client: SidecarClient | None,
    *,
    attempts: int = 30,
    interval: float = 2.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict[str, str]:
    if client is None:
        return {"state": "off", "reason": "not configured"}
    last = "no answer"
    for attempt in range(attempts):
        try:
            fetched = await client.get("/healthz")
        except ConnectorError as exc:
            last = type(exc.__cause__ or exc).__name__
        else:
            if fetched.status == 200:
                return {"state": "ready"}
            last = f"HTTP {fetched.status}"
        if attempt < attempts - 1:
            await sleep(interval)
    return {"state": "off", "reason": f"unreachable: {last}"}


def write_capability(database_url: str, key: str, value: Any) -> None:
    """Sync on purpose: uvicorn on Windows runs a Proactor loop, where psycopg's async driver cannot."""
    engine = create_engine(database_url, connect_args={"connect_timeout": 5})
    try:
        with engine.begin() as conn:
            stmt = insert(Capability).values(key=key, value=value)
            conn.execute(
                stmt.on_conflict_do_update(index_elements=[Capability.key], set_={"value": stmt.excluded.value})
            )
    finally:
        engine.dispose()


async def record_web_search(database_url: str, client: SidecarClient | None) -> None:
    value = await probe_web_search(client)
    try:
        await asyncio.to_thread(write_capability, database_url, "web_search", value)
    except Exception as exc:  # never take the app down over a status row
        # the type only: a driver message could carry connection details
        logger.warning("could not record the web search capability: %s", type(exc).__name__)
