import os

import pytest
from claude_agent_sdk import ResultMessage
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from pydantic import BaseModel

from researchly.llm.dev import claude_code
from researchly.llm.dev.claude_code import ClaudeCodeChatModel


@tool
def get_weather(city: str) -> str:
    """Current weather for a city."""
    return city


class Verdict(BaseModel):
    label: str
    score: float


def fake_query(structured=None, result=None, seen=None):
    async def _query(*, prompt, options):
        if seen is not None:
            seen.update(prompt=prompt, options=options)
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="s",
            result=result,
            structured_output=structured,
            usage={"input_tokens": 10, "cache_read_input_tokens": 5, "output_tokens": 7},
        )

    return _query


def test_plain_text_reply_and_isolated_options(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(claude_code, "query", fake_query(result="hello", seen=seen))
    reply = ClaudeCodeChatModel().invoke([SystemMessage("be brief"), HumanMessage("hi")])
    assert reply.content == "hello"
    assert reply.usage_metadata == {
        "input_tokens": 15,
        "output_tokens": 7,
        "total_tokens": 22,
        "input_token_details": {"cache_read": 5},
    }
    o = seen["options"]
    assert (o.tools, o.max_turns, o.setting_sources, o.model, o.output_format) == ([], 1, [], "sonnet", None)
    assert o.system_prompt == "be brief"
    assert "hi" in seen["prompt"]


@pytest.mark.anyio
async def test_tool_calls_are_emulated(monkeypatch):
    seen: dict = {}
    structured = {"type": "tool_calls", "content": "", "calls": [{"name": "get_weather", "args": {"city": "Paris"}}]}
    monkeypatch.setattr(claude_code, "query", fake_query(structured=structured, seen=seen))
    reply = await ClaudeCodeChatModel().bind_tools([get_weather]).ainvoke("weather in Paris?")
    assert reply.tool_calls[0]["name"] == "get_weather"
    assert reply.tool_calls[0]["args"] == {"city": "Paris"}
    assert "get_weather" in seen["options"].system_prompt
    assert seen["options"].output_format["type"] == "json_schema"


def test_structured_output_forces_a_call(monkeypatch):
    seen: dict = {}
    structured = {
        "type": "tool_calls",
        "content": "",
        "calls": [{"name": "Verdict", "args": {"label": "ok", "score": 0.9}}],
    }
    monkeypatch.setattr(claude_code, "query", fake_query(structured=structured, seen=seen))
    verdict = ClaudeCodeChatModel().with_structured_output(Verdict).invoke("judge")
    assert verdict == Verdict(label="ok", score=0.9)
    assert seen["options"].output_format["schema"]["properties"]["type"]["enum"] == ["tool_calls"]


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("RESEARCHLY_LIVE_DEV_PROVIDER") != "1", reason="opt-in: uses the local Claude Code login"
)
def test_live_tool_call_roundtrip():
    reply = (
        ClaudeCodeChatModel(model="haiku")
        .bind_tools([get_weather])
        .invoke("What's the weather in Paris? Use the tool.")
    )
    assert reply.tool_calls and reply.tool_calls[0]["name"] == "get_weather"
