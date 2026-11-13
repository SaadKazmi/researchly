import re
import uuid
from pathlib import Path
from typing import Any

import pytest
from ag_ui.core import EventType, ResumeEntry, RunAgentInput, UserMessage
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

import researchly
from researchly.agents.context import RunContext
from researchly.runs.agui import ResearchlyAgent, allowed_decisions

from ..agents.fakes import agent, call, script

pytestmark = pytest.mark.anyio
SRC = Path(researchly.__file__).parent
CTX = RunContext(project_id=uuid.uuid4(), run_id=uuid.uuid4())
CFG: Any = {"configurable": {"thread_id": "t"}}


def ask(text: str = "Q?") -> list[Any]:
    return [UserMessage(id=str(uuid.uuid4()), content=text)]


async def drive(adapter: ResearchlyAgent, messages: list[Any] | None = None, resume: Any = None) -> list[Any]:
    run = RunAgentInput(thread_id="t", run_id=str(uuid.uuid4()), messages=messages or [], resume=resume)
    return [event async for event in adapter.run(run)]


@tool
def submit_plan(plan: str) -> str:
    """Submit the plan."""
    return "approved"


async def test_tools_receive_our_run_context():
    seen = []

    @tool
    def whoami(runtime: ToolRuntime[RunContext]) -> str:
        """Who am I."""
        seen.append(runtime.context)
        return "ok"

    events = await drive(ResearchlyAgent(agent(script(call("whoami", {}), "done"), [whoami]), CTX), ask())
    assert seen == [CTX] and events[-1].type == EventType.RUN_FINISHED  # a dict context fails on thread_id


async def test_state_snapshots_carry_only_todos():
    adapter = ResearchlyAgent(agent(script("done")), CTX)
    events = await drive(adapter, ask())
    assert [e.snapshot for e in events if e.type == EventType.STATE_SNAPSHOT] == [{}]
    assert adapter.get_state_snapshot({"messages": [], "files": {"/a": {}}, "todos": [{"content": "x"}]}) == {
        "todos": [{"content": "x"}]
    }


async def test_a_plan_review_is_a_typed_interrupt_and_an_approval_completes():
    hitl = HumanInTheLoopMiddleware(interrupt_on={"submit_plan": {"allowed_decisions": ["approve", "edit", "reject"]}})
    graph = agent(script(call("submit_plan", {"plan": "1. search"}), "Done."), [submit_plan], [hitl])
    events = await drive(ResearchlyAgent(graph, CTX), ask())
    assert not any(e.type == EventType.CUSTOM for e in events)  # no legacy on_interrupt event
    finished = events[-1].model_dump(mode="json", by_alias=True, exclude_none=True)
    [interrupt] = finished["outcome"]["interrupts"]
    assert (interrupt["reason"], interrupt["message"]) == ("researchly:submit_plan", "1. search")
    assert allowed_decisions(interrupt) == [["approve", "edit", "reject"]]
    assert allowed_decisions({"reason": "langgraph:interrupt"}) is None

    state = await graph.aget_state(CFG)
    decisions = {"decisions": [{"type": "approve"}]}
    resume = [ResumeEntry(interrupt_id=state.interrupts[0].id, status="resolved", payload=decisions)]
    events = await drive(ResearchlyAgent(graph, CTX), resume=resume)
    assert events[-1].type == EventType.RUN_FINISHED and events[-1].outcome is None
    assert (await graph.aget_state(CFG)).values["messages"][-1].text == "Done."


async def test_continue_runs_the_pending_tool_step():
    calls = []

    @tool
    def fetch(x: str) -> str:
        """Fetch."""
        calls.append(x)
        return "fetched"

    model = script(call("fetch", {"x": "a"}), "done")
    graph = create_agent(
        model, [fetch], checkpointer=InMemorySaver(), context_schema=RunContext, interrupt_before=["tools"]
    )
    await drive(ResearchlyAgent(graph, CTX), ask())
    assert (await graph.aget_state(CFG)).next == ("tools",) and calls == []
    await drive(ResearchlyAgent(graph, CTX, continue_pending=True))
    messages = (await graph.aget_state(CFG)).values["messages"]
    assert calls == ["a"] and messages[-1].text == "done"
    assert not any(isinstance(m, ToolMessage) and m.status == "error" for m in messages)  # Spike §4


async def test_a_branch_runs_from_its_fork_and_the_snapshot_shows_the_new_head():
    graph = agent(script("first answer", "second answer"))
    await drive(ResearchlyAgent(graph, CTX), ask())
    [point] = [s async for s in graph.aget_state_history(CFG) if s.next == ("model",)]
    checkpoint_id = point.config["configurable"]["checkpoint_id"]
    events = await drive(ResearchlyAgent(graph, CTX, continue_pending=True, checkpoint_id=checkpoint_id))
    [snapshot] = [e for e in events if e.type == EventType.MESSAGES_SNAPSHOT]
    assert snapshot.messages[-1].content == "second answer"
    assert [m.text for m in (await graph.aget_state(CFG)).values["messages"]] == ["Q?", "second answer"]


async def test_continue_never_overwrites_a_resume_with_decisions():
    hitl = HumanInTheLoopMiddleware(interrupt_on={"submit_plan": {"allowed_decisions": ["approve", "edit", "reject"]}})
    graph = agent(script(call("submit_plan", {"plan": "1. search"}), "Done."), [submit_plan], [hitl])
    await drive(ResearchlyAgent(graph, CTX), ask())
    state = await graph.aget_state(CFG)
    decisions = {"decisions": [{"type": "approve"}]}
    resume = [ResumeEntry(interrupt_id=state.interrupts[0].id, status="resolved", payload=decisions)]
    events = await drive(ResearchlyAgent(graph, CTX, continue_pending=True), resume=resume)
    # a dropped decision would re-run the checkpoint and pause on the same review again
    assert events[-1].type == EventType.RUN_FINISHED and events[-1].outcome is None
    assert (await graph.aget_state(CFG)).values["messages"][-1].text == "Done."


def test_only_the_adapter_imports_ag_ui_langgraph():
    """The adapter is pinned (ADR 0012), so an upgrade has exactly one file to re-check."""
    importer = re.compile(r"^\s*(from|import) ag_ui_langgraph\b", re.MULTILINE)
    found = sorted(p.relative_to(SRC).as_posix() for p in SRC.rglob("*.py") if importer.search(p.read_text("utf-8")))
    assert found == ["runs/agui.py"]


def test_only_the_event_log_writes_run_event():
    """A row written anywhere else would skip the seq, the coalescing and the publish to the hub."""
    writer = re.compile(r"(?<!class )\bRunEvent\(")
    found = sorted(p.relative_to(SRC).as_posix() for p in SRC.rglob("*.py") if writer.search(p.read_text("utf-8")))
    assert found == ["runs/events.py"]
