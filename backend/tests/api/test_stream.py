import asyncio
import json
import uuid
from typing import Any

import pytest

from researchly.runs.events import follow, replay

from ..agents.fakes import agent, blocking_tool, call, script, until
from .conftest import new_thread

pytestmark = [pytest.mark.anyio, pytest.mark.db]

# Spike §2 and the plan's probes; MESSAGES_SNAPSHOT is not stored, and the deltas are coalesced
SHAPE = [
    "RUN_STARTED",
    "STEP_STARTED:model",
    "TOOL_CALL_START:fetch",
    "TOOL_CALL_ARGS",
    "TOOL_CALL_END",
    "STEP_FINISHED:model",
    "STEP_STARTED:tools",
    "TOOL_CALL_RESULT",
    "STATE_SNAPSHOT",
    "STEP_FINISHED:tools",
    "STEP_STARTED:model",
    "TEXT_MESSAGE_START",
    "TEXT_MESSAGE_CONTENT",
    "TEXT_MESSAGE_END",
    "STEP_FINISHED:model",
    "STATE_SNAPSHOT",
    "CUSTOM:run_summary",
    "RUN_FINISHED",
]


def frames(body: str) -> list[tuple[int, dict[str, Any]]]:
    out = []
    for block in body.split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if line and not line.startswith(":"))
        if "data" in fields:
            out.append((int(fields["id"]), json.loads(fields["data"])))
    return out


def shape(payload: dict[str, Any]) -> str:
    name = payload.get("stepName") or payload.get("toolCallName") or payload.get("name")
    return f"{payload['type']}:{name}" if name else payload["type"]


async def test_a_run_streams_its_events_end_to_end(api):
    fetch, entered, gate, calls = blocking_tool()
    async with api(agent(script(call("fetch", {"x": "a"}), "The answer."), [fetch])) as (client, m):
        thread = await new_thread(client)
        run = (await client.post(f"/api/v1/threads/{thread['id']}/runs", json={"message": "Why?"})).json()
        run_id = uuid.UUID(run["id"])
        await entered.wait()
        # ASGITransport returns only when the stream ends: start it, wait for it to subscribe, then let the run finish
        stream = asyncio.create_task(client.post(f"/api/v1/runs/{run['id']}/stream", json={"threadId": "ignored"}))

        async def subscribed() -> bool:
            return run_id in m.hub._subscribers

        await until(subscribed)
        gate.set()
        response = await stream
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache" and response.headers["x-accel-buffering"] == "no"
    got = frames(response.text)
    assert [seq for seq, _ in got] == list(range(1, len(got) + 1))
    assert [shape(p) for _, p in got] == SHAPE
    summary = {"status": "completed", "spent_usd": 0.0, "budget_usd": 1.0, "coverage": None}
    assert got[-2][1]["value"] == summary


async def test_last_event_id_and_after_replay_from_the_next_event(api):
    async with api(agent(script("The answer."))) as (client, m):
        thread = await new_thread(client)
        run = (await client.post(f"/api/v1/threads/{thread['id']}/runs", json={"message": "Why?"})).json()
        await m.wait(uuid.UUID(run["id"]))
        url = f"/api/v1/runs/{run['id']}/stream"
        full = frames((await client.post(url)).text)
        by_header = frames((await client.post(url, headers={"Last-Event-ID": "3"}, params={"after": 1})).text)
        by_query = frames((await client.post(url, params={"after": 3})).text)
    assert by_header == by_query == full[3:] and by_header[0][0] == 4  # the header wins over ?after=


async def test_a_reconnect_resumes_without_gaps_or_duplicates(api):
    fetch, entered, gate, calls = blocking_tool()
    async with api(agent(script(call("fetch", {"x": "a"}), "The answer."), [fetch])) as (client, m):
        thread = await new_thread(client)
        run = (await client.post(f"/api/v1/threads/{thread['id']}/runs", json={"message": "Why?"})).json()
        run_id = uuid.UUID(run["id"])
        await entered.wait()
        first = follow(m.sessions, m.hub, run_id, 0)  # what the endpoint iterates
        got = [await anext(first) for _ in range(3)]
        await first.aclose()  # the client disconnects
        assert run_id not in m.hub._subscribers and run_id in m._tasks  # the run goes on
        gate.set()
        got += [e async for e in follow(m.sessions, m.hub, run_id, got[-1].seq)]  # reconnect with Last-Event-ID
        await m.wait(run_id)
        assert [e.seq for e in got] == list(range(1, len(got) + 1))
        assert got == await replay(m.sessions, run_id, 0) and got[-1].type == "RUN_FINISHED"
        assert (await client.get(f"/api/v1/runs/{run['id']}")).json()["status"] == "completed"


async def test_streaming_an_unknown_run_is_404(api):
    async with api() as (client, _):
        assert (await client.post(f"/api/v1/runs/{uuid.uuid4()}/stream")).status_code == 404
