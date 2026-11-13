import uuid

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from researchly.agents.context import RunContext

from .fakes import agent, call, script

pytestmark = pytest.mark.anyio
CTX = RunContext(project_id=uuid.uuid4(), run_id=uuid.uuid4())


async def test_tool_fake_streams_text_and_tool_calls():
    @tool
    def echo(x: str) -> str:
        """Echo."""
        return x

    model = script(call("echo", {"x": "a"}), "The answer.")
    graph = agent(model, [echo])
    cfg = {"configurable": {"thread_id": "t"}}
    stream = graph.astream_events({"messages": [HumanMessage("go")]}, cfg, context=CTX, version="v2")
    chunks = [e["data"]["chunk"] async for e in stream if e["event"] == "on_chat_model_stream"]
    assert [c.content for c in chunks if c.content] == ["The ", "answer."]
    assert "".join(tc["args"] or "" for c in chunks for tc in c.tool_call_chunks) == '{"x": "a"}'
    messages = (await graph.aget_state(cfg)).values["messages"]
    assert [m.text for m in messages] == ["go", "", "a", "The answer."] and len(model.seen) == 2
