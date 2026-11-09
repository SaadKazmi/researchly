from typing import Any

import pytest
from langchain.agents import create_agent
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from sqlalchemy import select

from researchly import app as app_module
from researchly.agents.context import RunContext
from researchly.app import create_app
from researchly.config import Settings
from researchly.db import migrate, provision
from researchly.db.engine import make_engine, make_sessionmaker
from researchly.db.models import Message, Run
from researchly.llm.registry import NoModelForRole
from researchly.runs.live import LiveRuntime

from ..agents.fakes import script, seed_run

pytestmark = [pytest.mark.anyio, pytest.mark.db]


@pytest.fixture
async def sm(db_url):
    migrate.upgrade(db_url)
    engine = make_engine(db_url)
    yield make_sessionmaker(engine)
    await engine.dispose()


class ScriptedRuntime:
    """LiveRuntime's constructor, but a scripted model on the app's real Postgres checkpointer."""

    def __init__(self, settings: Settings, sessions: Any, *, checkpointer: Any, store: Any, searxng: Any) -> None:
        self.checkpointer = checkpointer

    async def build(self, run: Run) -> Any:
        return create_agent(script("Resumed."), [], checkpointer=self.checkpointer, context_schema=RunContext)

    async def aclose(self) -> None:
        pass


async def test_the_app_resumes_running_runs_on_startup(sm, db_url, tmp_path, monkeypatch):
    provision.setup_agent_tables(db_url)
    run = await seed_run(sm, status="running")  # left running by an app that stopped
    monkeypatch.setattr(app_module, "LiveRuntime", ScriptedRuntime)
    app = create_app(Settings(env="development", database_url=db_url, data_dir=tmp_path), token="t" * 43)
    async with app.router.lifespan_context(app):
        await app.state.runs.wait(run.id)
    async with sm() as session:
        assert (await session.get_one(Run, run.id)).status == "completed"
        answer = select(Message.content).where(Message.run_id == run.id, Message.role == "assistant")
        assert await session.scalar(answer) == {"text": "Resumed."}


async def test_a_run_without_a_provider_fails_before_any_model_loads(sm, tmp_path):
    run = await seed_run(sm)
    settings = Settings(
        env="development", data_dir=tmp_path, dev_provider=None, llm_fixtures="off"
    )  # not the dev box env
    runtime = LiveRuntime(settings, sm, checkpointer=InMemorySaver(), store=InMemoryStore(), searxng=None)
    with pytest.raises(NoModelForRole):
        await runtime.build(run)
    assert runtime._base is None  # no HTTP client, embedder or reranker was made
    await runtime.aclose()
