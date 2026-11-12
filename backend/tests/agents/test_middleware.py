import asyncio
import uuid
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool

from researchly.agents.context import EFFORTS, RunContext
from researchly.agents.middleware import (
    BUDGET_SPENT,
    BUDGET_WRAP_UP,
    LOOP_ERROR,
    LOOP_STOP,
    BudgetGuard,
    EffortGuard,
    LoopGuard,
)

from .fakes import call, script

pytestmark = pytest.mark.anyio
CTX = RunContext(project_id=uuid.uuid4(), run_id=uuid.uuid4(), budget_usd=Decimal(1))
GO: Any = {"messages": [HumanMessage("go")]}


def spend(box: dict[str, Decimal]):
    async def spent(run_id: uuid.UUID) -> Decimal:
        assert run_id == CTX.run_id
        return box["v"]

    return spent


@tool
async def search(query: str) -> str:
    """Search."""
    return "ok"


async def test_budget_guard_ends_the_agent_once_the_budget_is_spent():
    box = {"v": Decimal(0)}

    @tool
    async def costly(x: str) -> str:
        """Spend the budget."""
        box["v"] = Decimal(2)
        return "done"

    model = script(call("costly", {"x": "a"}), "never said")
    agent = create_agent(model, [costly], middleware=[BudgetGuard(spend(box))], context_schema=RunContext)
    out = await agent.ainvoke(GO, context=CTX)
    assert out["messages"][-1].text == BUDGET_SPENT and len(model.seen) == 1


async def test_budget_guard_asks_for_a_wrap_up_at_80_percent():
    box = {"v": Decimal("0.85")}
    lead = script("fine")
    agent = create_agent(lead, [], middleware=[BudgetGuard(spend(box), wrap_up=True)], context_schema=RunContext)
    await agent.ainvoke(GO, context=CTX)
    assert lead.seen[0][-1].text == BUDGET_WRAP_UP
    sub = script("fine")
    agent = create_agent(sub, [], middleware=[BudgetGuard(spend(box))], context_schema=RunContext)
    await agent.ainvoke(GO, context=CTX)
    assert sub.seen[0][-1].text == "go"  # subagents get no wrap-up note


async def test_loop_guard_errors_on_repeats_then_stops_the_agent():
    q = {"query": "attention models"}
    model = script(call("search", q), call("search", q), call("search", q), call("search", q), "never said")
    agent = create_agent(model, [search], middleware=[LoopGuard()], context_schema=RunContext)
    out = await agent.ainvoke(GO, context=CTX)
    statuses = [m.status for m in out["messages"] if isinstance(m, ToolMessage)]
    assert statuses == ["success", "success", "error", "error"]
    assert out["messages"][-1].text == LOOP_STOP and len(model.seen) == 4


async def test_loop_guard_catches_reworded_queries():
    model = script(
        call("search", {"query": "graph neural networks survey"}),
        call("search", {"query": "survey of graph neural networks"}),
        "done",
    )
    agent = create_agent(model, [search], middleware=[LoopGuard()], context_schema=RunContext)
    out = await agent.ainvoke(GO, context=CTX)
    tool_messages = [m for m in out["messages"] if isinstance(m, ToolMessage)]
    # {graph, neural, networks, survey} vs {survey, of, graph, neural, networks}: Jaccard 4/5 = 0.8, the threshold
    assert [m.status for m in tool_messages] == ["success", "error"]
    assert tool_messages[1].text == LOOP_ERROR


async def test_effort_guard_caps_tasks_per_subagent_and_times_them_out():
    calls: list[str] = []

    @tool
    async def task(description: str, subagent_type: str) -> str:
        """Run a subagent."""
        calls.append(description)
        if description == "slow":
            await asyncio.sleep(1)
        return "sub done"

    effort = replace(EFFORTS["standard"], scouts=1, readers=1)
    model = script(
        call("task", {"description": "a", "subagent_type": "scout"}),
        call("task", {"description": "b", "subagent_type": "scout"}),
        call("task", {"description": "slow", "subagent_type": "reader"}),
        "end",
    )
    guard = EffortGuard(effort, timeouts={"scout": 5.0, "reader": 0.05})
    agent = create_agent(model, [task], middleware=[guard], context_schema=RunContext)
    out = await agent.ainvoke(GO, context=CTX)
    results = [(m.status, m.text) for m in out["messages"] if isinstance(m, ToolMessage)]
    assert results[0] == ("success", '<untrusted source="subagent result">\nsub done\n</untrusted>')
    assert results[1][0] == "error" and "1 scout" in results[1][1]
    assert results[2][0] == "error" and "timed out" in results[2][1]
    assert calls == ["a", "slow"]  # the refused task never ran


async def test_budget_guard_reports_the_cost_before_each_model_call():
    box = {"v": Decimal("0.25")}
    graph = create_agent(script("fine"), [], middleware=[BudgetGuard(spend(box))], context_schema=RunContext)
    stream = graph.astream_events(GO, context=CTX, version="v2")
    got = [(e["name"], e["data"]) async for e in stream if e["event"] == "on_custom_event"]
    assert got == [("cost", {"spent_usd": 0.25, "budget_usd": 1.0})]
