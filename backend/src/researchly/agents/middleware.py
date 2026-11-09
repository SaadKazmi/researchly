"""Run guards (spec §6.4) as LangChain agent middleware: budget, loops and effort tiers.

None of them keeps state of its own: they read the ledger or count earlier tool calls in state["messages"], which
the checkpointer persists, so a paused and resumed run is guarded exactly as if it had never stopped.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Mapping
from decimal import Decimal
from typing import Any

from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
    hook_config,
)
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolCall, ToolMessage
from langgraph.runtime import Runtime
from langgraph.types import Command

from researchly.agents.context import TIMEOUTS_S, Effort, RunContext
from researchly.services.evidence import untrusted

BUDGET_WRAP_UP = (
    "Budget note: 80% of this run's budget is spent. Start no new searches or readers; write the answer from what "
    "you already have and say what is missing."
)
BUDGET_SPENT = "Stopped: this run's budget is spent. The answer so far is partial."
LOOP_ERROR = "Error: this call repeats an earlier one. Change the query substantially or move on."
LOOP_STOP = "Stopped: the agent kept repeating the same calls. The answer so far is partial."
NEAR_DUPLICATE = 0.8  # Jaccard of query words

ToolHandler = Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]]


def _calls_before(messages: list[AnyMessage], call_id: str | None) -> list[ToolCall]:
    """Every tool call made before the one with `call_id` (earlier turns, then earlier calls in the same turn)."""
    out: list[ToolCall] = []
    for m in messages:
        if isinstance(m, AIMessage):
            for c in m.tool_calls:
                if c["id"] == call_id:
                    return out
                out.append(c)
    return out


def _words(args: Mapping[str, Any]) -> set[str]:
    query = args.get("query")
    return set(str(query).casefold().split()) if query else set()


def _refuse(call: ToolCall, text: str) -> ToolMessage:
    return ToolMessage(text, tool_call_id=call["id"] or "", name=call["name"], status="error")


def _quarantine(result: ToolMessage | Command) -> ToolMessage | Command:
    """A subagent's summary carries claims derived from paper text: the Lead sees it as untrusted (spec §19.1)."""

    def wrap(m: Any) -> Any:
        if not isinstance(m, ToolMessage):
            return m
        return ToolMessage(
            untrusted("subagent result", m.text), tool_call_id=m.tool_call_id, name=m.name, status=m.status
        )

    if isinstance(result, ToolMessage):
        return wrap(result)
    update = result.update
    if isinstance(update, dict) and isinstance(update.get("messages"), list):
        return Command(
            graph=result.graph,
            goto=result.goto,
            resume=result.resume,
            update={**update, "messages": [wrap(m) for m in update["messages"]]},
        )
    return result


class BudgetGuard(AgentMiddleware[AgentState[Any], RunContext]):
    def __init__(self, spent: Callable[[uuid.UUID], Awaitable[Decimal]], *, wrap_up: bool = False) -> None:
        super().__init__()
        self.spent, self.wrap_up = spent, wrap_up

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state: AgentState, runtime: Runtime[RunContext]) -> dict[str, Any] | None:
        ctx = runtime.context
        if await self.spent(ctx.run_id) >= ctx.budget_usd:
            return {"messages": [AIMessage(BUDGET_SPENT)], "jump_to": "end"}
        return None

    async def awrap_model_call(
        self, request: ModelRequest[RunContext], handler: Callable[[ModelRequest[RunContext]], Awaitable[ModelResponse]]
    ) -> ModelResponse:
        ctx = request.runtime.context
        assert ctx is not None
        if self.wrap_up and await self.spent(ctx.run_id) >= ctx.budget_usd * Decimal("0.8"):
            request = request.override(messages=[*request.messages, HumanMessage(BUDGET_WRAP_UP)])  # not persisted
        return await handler(request)


class LoopGuard(AgentMiddleware[AgentState[Any], RunContext]):
    async def awrap_tool_call(self, request: ToolCallRequest, handler: ToolHandler) -> ToolMessage | Command:
        call = request.tool_call
        earlier = [c for c in _calls_before(request.state["messages"], call["id"]) if c["name"] == call["name"]]
        repeats = sum(1 for c in earlier if c["args"] == call["args"])
        words = _words(call["args"])
        near = bool(words) and any(
            c["args"] != call["args"]
            and (other := _words(c["args"]))
            and len(words & other) / len(words | other) >= NEAR_DUPLICATE
            for c in earlier
        )
        if repeats >= 2 or near:  # one exact retry is allowed (a transient outage); rewording is not
            return _refuse(call, LOOP_ERROR)
        return await handler(request)

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state: AgentState, runtime: Runtime[RunContext]) -> dict[str, Any] | None:
        errors = sum(1 for m in state["messages"] if isinstance(m, ToolMessage) and m.content == LOOP_ERROR)
        if errors >= 2:
            return {"messages": [AIMessage(LOOP_STOP)], "jump_to": "end"}
        return None


class EffortGuard(AgentMiddleware[AgentState[Any], RunContext]):
    """Caps scout and reader tasks per effort tier and times each one out (spec §6.4)."""

    def __init__(self, effort: Effort, *, timeouts: Mapping[str, float] = TIMEOUTS_S) -> None:
        super().__init__()
        self.caps = {"scout": effort.scouts, "reader": effort.readers}
        self.timeouts = timeouts

    async def awrap_tool_call(self, request: ToolCallRequest, handler: ToolHandler) -> ToolMessage | Command:
        call = request.tool_call
        kind = call["args"].get("subagent_type") if call["name"] == "task" else None
        if kind not in self.caps:
            return await handler(request)
        used = sum(
            1
            for c in _calls_before(request.state["messages"], call["id"])
            if c["name"] == "task" and c["args"].get("subagent_type") == kind
        )
        if used >= self.caps[kind]:
            return _refuse(
                call, f"Error: this effort tier allows {self.caps[kind]} {kind} tasks per run; all are used."
            )
        try:
            result = await asyncio.wait_for(handler(request), self.timeouts[kind])
        except TimeoutError:
            return _refuse(call, f"Error: the {kind} task timed out after {self.timeouts[kind]:.0f} s.")
        return _quarantine(result)
