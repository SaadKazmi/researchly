import json

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from researchly.llm.record_replay import FixtureMissing, RecordReplayChatModel
from researchly.security.redact import register_secret


@tool
def lookup(query: str) -> str:
    """Look something up."""
    return query


def recorder(path, reply):
    inner = GenericFakeChatModel(messages=iter([reply]))
    return RecordReplayChatModel(inner=inner, mode="record", path=path, role="cheap", model_alias="claude-code/sonnet")


def replayer(path):
    return RecordReplayChatModel(inner=None, mode="replay", path=path, role="cheap", model_alias="claude-code/sonnet")


@pytest.mark.anyio
async def test_record_then_replay_roundtrip(tmp_path):
    path = tmp_path / "suite" / "calls.jsonl"
    reply = AIMessage(content="answer", usage_metadata={"input_tokens": 3, "output_tokens": 1, "total_tokens": 4})
    assert (await recorder(path, reply).ainvoke("question")).content == "answer"
    replayed = await replayer(path).ainvoke("question")
    assert replayed.content == "answer"
    assert replayed.usage_metadata is not None
    assert replayed.usage_metadata["output_tokens"] == 1
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


@pytest.mark.anyio
async def test_tool_calls_survive_and_tools_change_the_key(tmp_path):
    path = tmp_path / "calls.jsonl"
    reply = AIMessage(content="", tool_calls=[{"name": "lookup", "args": {"query": "x"}, "id": "call_1"}])
    await recorder(path, reply).bind_tools([lookup]).ainvoke("q")
    replayed = await replayer(path).bind_tools([lookup]).ainvoke("q")
    assert replayed.tool_calls[0]["name"] == "lookup"
    with pytest.raises(FixtureMissing):
        await replayer(path).ainvoke("q")  # same prompt without tools: different key


@pytest.mark.anyio
async def test_missing_fixture_reports_key_and_diff(tmp_path):
    path = tmp_path / "calls.jsonl"
    await recorder(path, AIMessage(content="a")).ainvoke("the original question")
    with pytest.raises(FixtureMissing) as err:
        await replayer(path).ainvoke("the changed question")
    message = str(err.value)
    assert "key" in message and "changed" in message and "original" in message


@pytest.mark.anyio
async def test_recording_scrubs_secrets(tmp_path):
    register_secret("super-secret-token-123")
    path = tmp_path / "calls.jsonl"
    await recorder(path, AIMessage(content="token is super-secret-token-123 mail me@example.org")).ainvoke("hi")
    text = path.read_text(encoding="utf-8")
    assert "super-secret-token-123" not in text and "me@example.org" not in text
    json.loads(text)  # still valid JSON


@pytest.mark.anyio
async def test_recording_scrubs_secrets_with_json_escapes(tmp_path):
    secret = r'pa"ss\word12'  # noqa: S105
    register_secret(secret)
    path = tmp_path / "calls.jsonl"
    await recorder(path, AIMessage(content=f"x {secret} y")).ainvoke("hi")
    line = json.loads(path.read_text(encoding="utf-8"))
    assert line["response"]["data"]["content"] == "x [REDACTED] y"


@pytest.mark.anyio
async def test_multi_turn_replay_survives_scrubbed_replies(tmp_path):
    path = tmp_path / "calls.jsonl"
    first, second = AIMessage(content="write to a.b@uni.edu"), AIMessage(content="done")
    inner = GenericFakeChatModel(messages=iter([first, second]))
    rec = RecordReplayChatModel(inner=inner, mode="record", path=path, role="cheap", model_alias="claude-code/sonnet")
    reply = await rec.ainvoke("hi")
    await rec.ainvoke([HumanMessage("hi"), reply, HumanMessage("thanks")])
    rep = replayer(path)
    replayed = await rep.ainvoke("hi")  # comes back scrubbed
    assert (await rep.ainvoke([HumanMessage("hi"), replayed, HumanMessage("thanks")])).content == "done"
