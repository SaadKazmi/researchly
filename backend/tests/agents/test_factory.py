import re
import uuid
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

import researchly
from researchly.agents.context import EFFORTS, RunContext
from researchly.agents.factory import _subagent, build_agent
from researchly.agents.middleware import BudgetGuard
from researchly.agents.tools import ToolDeps
from researchly.connectors.base import ExternalId, PaperRecord
from researchly.db import migrate
from researchly.db.engine import make_engine, make_sessionmaker
from researchly.db.models import Run

from .fakes import FakeEmbedder, KeywordReranker, StubConnector, StubFetcher, ToolFake, call, script, seed_run

pytestmark = pytest.mark.anyio
SRC = Path(researchly.__file__).parent
CTX = RunContext(project_id=uuid.uuid4(), run_id=uuid.uuid4())
GO: Any = {"messages": [HumanMessage("go")]}
ATTENTION = PaperRecord(
    source="openalex", ids=[ExternalId("openalex", "W1")], title="Attention Is All You Need", year=2017
)


async def nothing_spent(run_id: uuid.UUID) -> Decimal:
    return Decimal(0)


async def no_tokens(run_id: uuid.UUID) -> int:
    return 0


async def some_tokens(run_id: uuid.UUID) -> int:
    return 42


def build(effort: str, lead: ToolFake, scout: ToolFake | None = None, *, sessions: Any = None, connectors=None, **kw):
    deps = ToolDeps(
        sessions=sessions,
        connectors=connectors or {},
        searxng=None,
        fetcher=StubFetcher(),
        embedder=FakeEmbedder(),
        reranker=KeywordReranker("attention"),
        extractor=script(),
    )
    kw.setdefault("tokens", no_tokens)
    return build_agent(
        EFFORTS[effort], lead=lead, scout=scout or script(), reader=script(), deps=deps, spent=nothing_spent, **kw
    )


def test_only_the_factory_imports_deepagents():
    importer = re.compile(r"^\s*(from|import) deepagents\b", re.MULTILINE)
    found = sorted(p.relative_to(SRC).as_posix() for p in SRC.rglob("*.py") if importer.search(p.read_text("utf-8")))
    assert found == ["agents/factory.py"]


@pytest.mark.parametrize("effort", ["quick", "standard"])
async def test_every_tiers_budget_guard_reads_the_token_total(effort):
    graph = build(effort, script("done"), tokens=some_tokens)
    stream = graph.astream_events(GO, context=replace(CTX, effort=effort), version="v2")
    costs: list[Any] = [e["data"] async for e in stream if e["event"] == "on_custom_event" and e["name"] == "cost"]
    assert [c["tokens"] for c in costs] == [42]


def test_a_subagents_budget_guard_reads_the_token_total():
    sub = _subagent("scout", "d", "p", [], script(), EFFORTS["standard"], nothing_spent, some_tokens)
    guards = [m for m in sub.get("middleware", []) if isinstance(m, BudgetGuard)]
    assert [g.tokens for g in guards] == [some_tokens]


async def test_the_quick_lead_searches_itself_and_has_no_subagents():
    lead = script("done")
    await build("quick", lead).ainvoke(GO, context=replace(CTX, effort="quick"))
    assert {"search_papers", "search_web", "rank_candidates", "gather_evidence"} <= set(lead.bound)
    assert not {"task", "ask_user", "submit_plan", "write_todos"} & set(lead.bound)


async def test_the_standard_lead_delegates_and_cannot_read_project_files():
    store = InMemoryStore()
    await store.aput(("project", str(CTX.project_id)), "/candidates/sq1.jsonl", {"content": "x", "encoding": "utf-8"})
    lead = script(call("read_file", {"file_path": "/projects/candidates/sq1.jsonl"}), "done")
    await build("standard", lead, store=store).ainvoke(GO, context=CTX)
    assert {"task", "write_todos", "ask_user", "submit_plan", "rank_candidates"} <= set(lead.bound)
    assert not {"search_papers", "gather_evidence"} & set(lead.bound)
    assert "scout" in lead.bound["task"] and "reader" in lead.bound["task"]
    assert "- general-purpose:" not in lead.bound["task"]  # the tool text mentions it in boilerplate
    [denied] = [m for m in lead.seen[1] if isinstance(m, ToolMessage)]
    assert denied.status == "error" and "permission denied" in denied.text


@pytest.fixture
async def sm(db_url):
    migrate.upgrade(db_url)
    engine = make_engine(db_url)
    yield make_sessionmaker(engine)
    await engine.dispose()


@pytest.mark.db
async def test_plan_review_then_a_scout_fills_the_project_store(sm):
    run = await seed_run(sm)
    ctx = RunContext(project_id=run.project_id, run_id=run.id)
    lead = script(
        call("ask_user", {"question": "Which years?"}),
        call("submit_plan", {"plan": "1. search"}),
        call("task", {"description": "Find papers on attention. Use file sq1.", "subagent_type": "scout"}),
        "The answer.",
    )
    scout = script(
        call("search_papers", {"query": "attention", "file": "sq1"}),
        call("SubagentResult", {"status": "complete", "summary": "1 paper", "files": [], "gaps": []}),
    )
    store = InMemoryStore()
    connectors = {"openalex": StubConnector("openalex", [ATTENTION])}
    agent = build(
        "standard", lead, scout, sessions=sm, connectors=connectors, checkpointer=InMemorySaver(), store=store
    )
    cfg: Any = {"configurable": {"thread_id": str(run.id)}}

    out = await agent.ainvoke(GO, cfg, context=ctx)
    assert out["__interrupt__"][0].value["action_requests"][0]["name"] == "ask_user"
    out = await agent.ainvoke(
        Command(resume={"decisions": [{"type": "respond", "message": "2020 on"}]}), cfg, context=ctx
    )
    assert out["__interrupt__"][0].value["action_requests"][0]["name"] == "submit_plan"
    edit = {"type": "edit", "edited_action": {"name": "submit_plan", "args": {"plan": "1. search from 2020"}}}
    out = await agent.ainvoke(Command(resume={"decisions": [edit]}), cfg, context=ctx)

    assert out["messages"][-1].text == "The answer."
    answers = [m.text for m in out["messages"] if isinstance(m, ToolMessage)]
    assert answers[0] == "2020 on" and "Plan approved" in answers[1] and '"status":"complete"' in answers[2]
    assert answers[2].startswith('<untrusted source="subagent result">')  # spec §19.1: derived from paper text
    async with sm() as session:
        assert (await session.get_one(Run, run.id)).plan == {"text": "1. search from 2020"}
    item = await store.aget(("project", str(run.project_id)), "/candidates/sq1.jsonl")
    assert item is not None and "Attention Is All You Need" in str(item.value)
