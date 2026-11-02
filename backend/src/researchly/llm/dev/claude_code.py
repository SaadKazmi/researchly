"""Dev-only provider (spec §7.7): answers the app's LLM calls through the developer's local Claude Code login.

Never shipped (excluded from the wheel) and never used in CI.
"""

import asyncio
import json
import sys
import tempfile
import uuid
from collections.abc import Sequence
from typing import Any, Literal

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_core.messages.ai import UsageMetadata
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.utils.function_calling import convert_to_openai_tool

_LOOP = asyncio.ProactorEventLoop if sys.platform == "win32" else None  # the SDK spawns a subprocess


def _render(messages: list[BaseMessage]) -> tuple[str, str]:
    system = "\n\n".join(m.text for m in messages if isinstance(m, SystemMessage))
    turns: list[str] = []
    for m in messages:
        if isinstance(m, SystemMessage):
            continue
        if isinstance(m, ToolMessage):
            turns.append(f"[tool result {m.name} {m.tool_call_id}]\n{m.text}")
        elif isinstance(m, AIMessage) and m.tool_calls:
            turns.append(f"[assistant]\n{m.text}\n[tool calls] {json.dumps(m.tool_calls, default=str)}")
        else:
            turns.append(f"[{m.type}]\n{m.text}")
    return system, "\n\n".join(turns)


def _catalogue(tools: list[dict[str, Any]]) -> str:
    lines = ["You can call these tools. To call, reply with type=tool_calls; otherwise type=final with content."]
    for t in tools:
        fn = t["function"]
        lines.append(
            f"- {fn['name']}: {fn.get('description', '')}\n  parameters: {json.dumps(fn.get('parameters', {}))}"
        )
    return "\n".join(lines)


def _schema(forced: bool) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["tool_calls"] if forced else ["final", "tool_calls"]},
            "content": {"type": "string"},
            "calls": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "args": {"type": "object"}},
                    "required": ["name", "args"],
                },
            },
        },
        "required": ["type"],
    }


def _well_formed(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    if data.get("type") == "tool_calls":
        return bool(data.get("calls"))
    return data.get("type") == "final" and isinstance(data.get("content"), str)


def _usage(result: ResultMessage) -> UsageMetadata:
    u = result.usage or {}
    cached = u.get("cache_read_input_tokens", 0)
    inputs = u.get("input_tokens", 0) + cached + u.get("cache_creation_input_tokens", 0)
    out = u.get("output_tokens", 0)
    return {
        "input_tokens": inputs,
        "output_tokens": out,
        "total_tokens": inputs + out,
        "input_token_details": {"cache_read": cached},
    }


class ClaudeCodeChatModel(BaseChatModel):
    model: Literal["sonnet", "haiku"] = "sonnet"

    @property
    def _llm_type(self) -> str:
        return "claude-code"

    def bind_tools(
        self, tools: Sequence[Any], *, tool_choice: str | None = None, **kwargs: Any
    ) -> Runnable[LanguageModelInput, AIMessage]:
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        return self.bind(tools=[convert_to_openai_tool(t) for t in tools], **kwargs)

    async def _run(self, system: str, prompt: str, output_format: dict[str, Any] | None) -> ResultMessage:
        with tempfile.TemporaryDirectory() as cwd:
            options = ClaudeAgentOptions(
                tools=[],
                system_prompt=system,
                max_turns=1,
                setting_sources=[],
                cwd=cwd,
                model=self.model,
                output_format=output_format,
            )
            result: ResultMessage | None = None
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, ResultMessage):
                    result = message
        if result is None or result.is_error:
            raise RuntimeError(f"claude-code provider failed: {result.subtype if result else 'no result message'}")
        return result

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        system, prompt = _render(messages)
        tools: list[dict[str, Any]] = kwargs.get("tools") or []
        output_format = None
        if tools:
            system = f"{system}\n\n{_catalogue(tools)}".strip()
            forced = kwargs.get("tool_choice") not in (None, "auto", "none")
            output_format = {"type": "json_schema", "schema": _schema(forced)}
        result = asyncio.run(self._run(system, prompt, output_format), loop_factory=_LOOP)
        data = result.structured_output if tools else None
        if tools and not _well_formed(data):  # an empty reply would silently end an agent loop
            raise RuntimeError(f"claude-code provider returned malformed structured output: {data!r}")
        if data and data.get("type") == "tool_calls":
            calls = [
                {"name": c["name"], "args": c["args"], "id": f"call_{uuid.uuid4().hex[:24]}", "type": "tool_call"}
                for c in data.get("calls", [])
            ]
            message = AIMessage(content=data.get("content", ""), tool_calls=calls, usage_metadata=_usage(result))
        else:
            content = data.get("content", "") if data else (result.result or "")
            message = AIMessage(content=content, usage_metadata=_usage(result))
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return await asyncio.to_thread(self._generate, messages, stop, None, **kwargs)
