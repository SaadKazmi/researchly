"""Our AG-UI adapter (ADR 0012): ag-ui-langgraph's LangGraphAgent with our context, continue and review shape.

The adapter keeps per-run state on `self`, so the run manager builds one instance per drive. This is the only
module importing `ag_ui_langgraph`; it needs only the compiled graph, not deepagents (Spike §8).
"""

from collections.abc import Mapping
from typing import Any, Literal

from ag_ui_langgraph import LangGraphAgent
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from researchly.agents.context import RunContext

REVIEW = "researchly:"


def _review(raw: Any, interrupt: Any) -> Any:
    """HumanInTheLoopMiddleware's interrupt as a typed review: what is asked, and which decisions fit each action."""
    if not isinstance(raw, Mapping) or not raw.get("action_requests"):
        return interrupt
    requests, configs = raw["action_requests"], raw["review_configs"]
    items = [
        {"type": "object", "required": ["type"], "properties": {"type": {"enum": c["allowed_decisions"]}}}
        for c in configs
    ]
    decisions = {"type": "array", "minItems": len(requests), "maxItems": len(requests), "prefixItems": items}
    return interrupt.model_copy(
        update={
            "reason": REVIEW + "+".join(r["name"] for r in requests),
            "message": "\n\n".join(str(r["args"].get("plan") or r["args"].get("question") or "") for r in requests),
            "response_schema": {"type": "object", "required": ["decisions"], "properties": {"decisions": decisions}},
        }
    )


def allowed_decisions(interrupt: Mapping[str, Any]) -> list[list[str]] | None:
    """Per action, the decision types a stored (camelCase) review accepts; None if the interrupt is not a review."""
    try:
        items = interrupt["responseSchema"]["properties"]["decisions"]["prefixItems"]
    except (KeyError, TypeError):
        return None
    return [list(item["properties"]["type"]["enum"]) for item in items]


class ResearchlyAgent(LangGraphAgent):
    def __init__(
        self, graph: Any, context: RunContext, *, continue_pending: bool = False, checkpoint_id: str | None = None
    ) -> None:
        super().__init__(
            name="researchly",
            graph=graph,
            config={"configurable": {"checkpoint_id": checkpoint_id}} if checkpoint_id else None,
            emit_raw_events=False,
            emit_interrupt_outcome=True,
            enable_legacy_on_interrupt_event=False,
            subagent_visibility="attributed",
        )
        self.context, self.continue_pending = context, continue_pending

    def get_stream_kwargs(
        self,
        input: Any,
        subgraphs: bool = False,
        version: Literal["v1", "v2"] = "v2",
        config: RunnableConfig | None = None,
        context: dict[str, Any] | None = None,
        fork: Any | None = None,
    ) -> dict[str, Any]:
        kwargs = super().get_stream_kwargs(
            input, subgraphs=subgraphs, version=version, config=config, context=context, fork=fork
        )
        kwargs["context"] = self.context  # the adapter builds a dict, which RunContext(**...) rejects (Spike §3)
        if self.continue_pending and not isinstance(input, Command):
            kwargs["input"] = None  # run the checkpoint's pending tasks, not a new step (Spike §4); never drop a resume
        configurable = config.get("configurable", {}) if config else {}
        if config and "checkpoint_id" in configurable:
            kwargs["config"] = {**config, "configurable": dict(configurable)}  # the fork point, for the stream only
            del configurable["checkpoint_id"]  # the adapter re-reads state through this config after the stream
        return kwargs

    def get_state_snapshot(self, state: dict[str, Any]) -> dict[str, Any]:
        return {"todos": state["todos"]} if "todos" in state else {}  # files and messages stay off the wire

    def _interrupts_to_agui(self, lg_interrupts: Any) -> list[Any]:
        out = super()._interrupts_to_agui(lg_interrupts)
        return [_review(lg.value, i) for lg, i in zip(lg_interrupts, out, strict=True)]
