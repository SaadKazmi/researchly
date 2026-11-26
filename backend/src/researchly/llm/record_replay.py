"""RecordReplay (spec §7.6): wraps a chat model; records to / replays from committed JSONL fixtures."""

import difflib
import hashlib
import json
from collections.abc import AsyncIterator, Iterator, Sequence
from pathlib import Path
from typing import Any, Literal

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, message_to_dict, messages_from_dict
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.utils.function_calling import convert_to_openai_tool

from researchly.config import Settings
from researchly.security.redact import redact


class FixtureMissing(LookupError):
    pass


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def fixture_key(request: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(request).encode("utf-8")).hexdigest()


def _scrub(obj: Any) -> Any:
    """Redact every string leaf; on serialized JSON an escaped secret (quote, backslash) would slip past redact()."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {_scrub(k): _scrub(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub(v) for v in obj]
    return obj


def as_chunks(message: BaseMessage) -> Iterator[ChatGenerationChunk]:
    """A whole reply as a stream: its text, each tool call, then the usage. The AG-UI adapter drives
    `astream_events`, and a model that does not stream never reaches the transcript (ADR 0012)."""
    assert isinstance(message, AIMessage)
    if message.content:
        yield ChatGenerationChunk(message=AIMessageChunk(content=message.content, id=message.id))
    for i, call in enumerate(message.tool_calls):
        part = tool_call_chunk(name=call["name"], args=json.dumps(call["args"]), id=call["id"], index=i)
        yield ChatGenerationChunk(message=AIMessageChunk(content="", id=message.id, tool_call_chunks=[part]))
    last = AIMessageChunk(content="", id=message.id, usage_metadata=message.usage_metadata, chunk_position="last")
    yield ChatGenerationChunk(message=last)


def fixtures_path(settings: Settings) -> Path:
    return settings.llm_fixtures_dir / settings.llm_fixtures_suite / "calls.jsonl"


def _message(m: BaseMessage) -> dict[str, Any]:
    return {
        "type": m.type,
        "content": m.content,
        "tool_calls": getattr(m, "tool_calls", []),
        "tool_call_id": getattr(m, "tool_call_id", None),
        "name": m.name,
    }


class RecordReplayChatModel(BaseChatModel):
    inner: BaseChatModel | None = None
    mode: Literal["record", "replay"]
    path: Path
    role: str
    model_alias: str

    @property
    def _llm_type(self) -> str:
        return "record-replay"

    def bind_tools(
        self, tools: Sequence[Any], *, tool_choice: str | None = None, **kwargs: Any
    ) -> Runnable[LanguageModelInput, AIMessage]:
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        return self.bind(tools=[convert_to_openai_tool(t) for t in tools], **kwargs)

    def _request(self, messages: list[BaseMessage], kwargs: dict[str, Any]) -> dict[str, Any]:
        request = {
            "messages": [_message(m) for m in messages],
            "tools": kwargs.get("tools"),
            "response_schema": kwargs.get("response_format"),
            "role": self.role,
            "model_alias": self.model_alias,
        }
        # Key on the scrubbed form: replayed replies come back scrubbed and feed the next turn's history.
        return _scrub(json.loads(_canonical(request)))

    def _entries(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return {
            e["key"]: e for e in map(json.loads, filter(None, lines))
        }  # ponytail: re-read per call; index if suites grow large

    def _replay(self, request: dict[str, Any]) -> ChatResult:
        key = fixture_key(request)
        entries = self._entries()
        if key not in entries:
            raise FixtureMissing(self._miss_report(key, request, entries))
        message = messages_from_dict([entries[key]["response"]])[0]
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _miss_report(self, key: str, request: dict[str, Any], entries: dict[str, dict[str, Any]]) -> str:
        head = f"no LLM fixture for key {key} in {self.path}"
        if not entries:
            return head + " (file empty or missing; record with RESEARCHLY_LLM_FIXTURES=record)"
        wanted = json.dumps(request, indent=1, sort_keys=True, default=str).splitlines()
        nearest = max(
            entries.values(),
            key=lambda e: difflib.SequenceMatcher(None, _canonical(e["request"]), _canonical(request)).ratio(),
        )
        stored = json.dumps(nearest["request"], indent=1, sort_keys=True, default=str).splitlines()
        diff = list(difflib.unified_diff(stored, wanted, f"nearest {nearest['key'][:12]}", "requested", lineterm=""))
        return head + "\n" + "\n".join(diff[:40])

    def _record(self, request: dict[str, Any], result: ChatResult) -> None:
        key = fixture_key(request)
        if key in self._entries():
            return
        line = {"key": key, "request": request, "response": message_to_dict(result.generations[0].message)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as fh:
            # round-trip through JSON first so default=str leaves are strings _scrub can reach
            fh.write(_canonical(_scrub(json.loads(_canonical(line)))) + "\n")

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        request = self._request(messages, kwargs)
        if self.mode == "replay":
            return self._replay(request)
        assert self.inner is not None, "record mode needs an inner model"
        result = self.inner._generate(messages, stop=stop, **kwargs)
        self._record(request, result)
        return result

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        request = self._request(messages, kwargs)
        if self.mode == "replay":
            return self._replay(request)
        assert self.inner is not None, "record mode needs an inner model"
        result = await self.inner._agenerate(messages, stop=stop, **kwargs)
        self._record(request, result)
        return result

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        yield from as_chunks(self._generate(messages, stop=stop, **kwargs).generations[0].message)

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        result = await self._agenerate(messages, stop=stop, **kwargs)
        for chunk in as_chunks(result.generations[0].message):
            yield chunk
