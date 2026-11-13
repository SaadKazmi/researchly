import contextlib
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from researchly.app import create_app
from researchly.config import Settings
from researchly.db import migrate
from researchly.db.engine import make_engine, make_sessionmaker
from researchly.db.models import Run
from researchly.runs.manager import RunManager
from researchly.security.localhost_guard import TOKEN_HEADER

TOKEN = "t" * 43


@pytest.fixture
async def sm(db_url):
    migrate.upgrade(db_url)
    engine = make_engine(db_url)
    yield make_sessionmaker(engine)
    await engine.dispose()


@pytest.fixture
def api(sm):
    """`async with api(graph) as (client, manager)`: the app over ASGITransport, on a manager whose runs use `graph`."""

    @contextlib.asynccontextmanager
    async def open_api(graph: Any = None) -> AsyncIterator[tuple[httpx.AsyncClient, RunManager]]:
        async def build(run: Run) -> Any:
            return graph

        manager = RunManager(sm, build)
        app = create_app(Settings(env="test"), token=TOKEN)
        app.state.runs = manager  # ASGITransport runs no lifespan
        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://127.0.0.1", headers={TOKEN_HEADER: TOKEN}
            ) as client:
                yield client, manager
        finally:
            await manager.shutdown()

    return open_api


async def new_thread(client: httpx.AsyncClient) -> dict[str, Any]:
    project = (await client.post("/api/v1/projects", json={"name": "p"})).json()
    return (await client.post(f"/api/v1/projects/{project['id']}/threads", json={})).json()
