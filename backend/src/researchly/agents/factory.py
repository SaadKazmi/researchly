"""Builds one run's agent graph (spec §6.1–6.6). The only module that imports deepagents (AGENTS.md)."""

import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

from deepagents import (
    FilesystemPermission,
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    SubAgent,
    create_deep_agent,
    register_harness_profile,
)
from deepagents._models import get_model_provider  # private; probed on 0.7.32, re-checked on every bump (ADR 0011)
from deepagents.backends import CompositeBackend, StateBackend, StoreBackend
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    TodoListMiddleware,
    ToolCallLimitMiddleware,
)
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore

from researchly.agents import prompts
from researchly.agents.context import Effort, RunContext, SubagentResult
from researchly.agents.middleware import BudgetGuard, EffortGuard, LoopGuard
from researchly.agents.tools import ToolDeps, make_tools

Spent = Callable[[uuid.UUID], Awaitable[Decimal]]
PRIVATE = ["/projects/candidates/**", "/projects/evidence/**"]  # full records and quotes: tools only (§19.1)
HITL: dict[str, Any] = {
    "submit_plan": {"allowed_decisions": ["approve", "edit", "reject"]},
    "ask_user": {"allowed_decisions": ["respond"]},
}


def project_files() -> StoreBackend:
    return StoreBackend(namespace=lambda rt: ("project", str(rt.context.project_id)))


def _no_general_purpose(*models: BaseChatModel) -> None:
    """deepagents adds a general-purpose subagent unless the provider's harness profile turns it off."""
    off = HarnessProfile(general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False))
    for model in models:
        if provider := get_model_provider(model):
            register_harness_profile(provider, off)


def _subagent(
    name: str, description: str, prompt: str, tools: list[BaseTool], model: BaseChatModel, effort: Effort, spent: Spent
) -> SubAgent:
    middleware: list[Any] = [  # Any: langchain's middleware generics are invariant in state and context
        BudgetGuard(spent),
        LoopGuard(),
        ModelCallLimitMiddleware(run_limit=effort.model_calls, exit_behavior="end"),
        ToolCallLimitMiddleware(run_limit=effort.tool_calls, exit_behavior="end"),
    ]
    return {
        "name": name,
        "description": description,
        "system_prompt": prompt,
        "tools": tools,
        "model": model,
        "middleware": middleware,
        "response_format": ToolStrategy(SubagentResult),
    }


def build_agent(
    effort: Effort,
    *,
    lead: BaseChatModel,
    scout: BaseChatModel,
    reader: BaseChatModel,
    deps: ToolDeps,
    spent: Spent,
    checkpointer: BaseCheckpointSaver | None = None,
    store: BaseStore | None = None,
):
    files = project_files()
    tools = make_tools(deps, files)
    _no_general_purpose(lead, scout, reader)
    common: dict[str, Any] = {
        "model": lead,
        "name": "lead",
        "backend": CompositeBackend(default=StateBackend(), routes={"/projects/": files}),
        "permissions": [FilesystemPermission(operations=["read", "write"], paths=PRIVATE, mode="deny")],
        "context_schema": RunContext,
        "checkpointer": checkpointer,
        "store": store,
    }
    if effort.name == "quick":
        # past the cap each tool call errors, the Lead answers from what it has and the run is marked partial
        quick_guards: list[Any] = [
            BudgetGuard(spent, wrap_up=True),
            LoopGuard(),
            ToolCallLimitMiddleware(thread_limit=effort.tool_calls, exit_behavior="continue"),
        ]
        return create_deep_agent(
            **common,
            system_prompt=prompts.QUICK_LEAD,
            tools=[tools[n] for n in ("search_papers", "search_web", "rank_candidates", "gather_evidence")],
            subagents=[],
            middleware=quick_guards,
        )
    lead_guards: list[Any] = [
        TodoListMiddleware(),
        BudgetGuard(spent, wrap_up=True),
        LoopGuard(),
        EffortGuard(effort),
        ToolCallLimitMiddleware(tool_name="ask_user", thread_limit=3, exit_behavior="continue"),
    ]
    scout_tools = [tools["search_papers"], tools["search_web"]]
    reader_tools = [tools["gather_evidence"]]
    return create_deep_agent(
        **common,
        system_prompt=prompts.LEAD,
        tools=[tools["rank_candidates"], tools["ask_user"], tools["submit_plan"]],
        subagents=[
            _subagent("scout", prompts.SCOUT_DESCRIPTION, prompts.SCOUT, scout_tools, scout, effort, spent),
            _subagent("reader", prompts.READER_DESCRIPTION, prompts.READER, reader_tools, reader, effort, spent),
        ],
        middleware=lead_guards,
        interrupt_on=HITL,
    )
