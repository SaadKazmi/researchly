import uuid
from decimal import Decimal

import pytest
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool

from ..agents.fakes import agent, blocking_tool, call, script
from .conftest import new_thread

pytestmark = [pytest.mark.anyio, pytest.mark.db]


@tool
def submit_plan(plan: str) -> str:
    """Submit the plan."""
    return "approved"


async def test_a_run_is_created_started_and_finishes(api):
    async with api(agent(script("The answer."))) as (client, m):
        thread = await new_thread(client)
        created = await client.post(f"/api/v1/threads/{thread['id']}/runs", json={"message": "Why?", "effort": "quick"})
        assert created.status_code == 201
        run = created.json()
        assert run["effort"] == "quick" and Decimal(run["budget_usd"]) == Decimal("0.1")
        assert run["thread_id"] == thread["id"] and run["parent_run_id"] is None
        await m.wait(uuid.UUID(run["id"]))
        done = (await client.get(f"/api/v1/runs/{run['id']}")).json()
        assert done["status"] == "completed" and done["ended_at"]
        messages = (await client.get(f"/api/v1/threads/{thread['id']}")).json()["messages"]
        assert [x["text"] for x in messages] == ["Why?", "The answer."]


async def test_a_plan_is_approved_through_the_api(api):
    hitl = HumanInTheLoopMiddleware(interrupt_on={"submit_plan": {"allowed_decisions": ["approve", "edit", "reject"]}})
    graph = agent(script(call("submit_plan", {"plan": "1. search"}), "Done."), [submit_plan], [hitl])
    async with api(graph) as (client, m):
        thread = await new_thread(client)
        run = (await client.post(f"/api/v1/threads/{thread['id']}/runs", json={"message": "Why?"})).json()
        await m.wait(uuid.UUID(run["id"]))
        assert (await client.get(f"/api/v1/runs/{run['id']}")).json()["status"] == "awaiting_user"
        url = f"/api/v1/runs/{run['id']}/resume"
        wrong = await client.post(url, json={"decisions": [{"type": "respond", "message": "x"}]})
        assert wrong.status_code == 409 and "open review" in wrong.json()["detail"]
        assert (await client.post(url, json={"decisions": [{"type": "maybe"}]})).status_code == 422
        assert (await client.post(url, json={"decisions": [{"type": "approve"}]})).status_code == 200
        await m.wait(uuid.UUID(run["id"]))
        assert (await client.get(f"/api/v1/runs/{run['id']}")).json()["status"] == "completed"
        messages = (await client.get(f"/api/v1/threads/{thread['id']}")).json()["messages"]
        assert messages[-1]["text"] == "Done."


async def test_pause_and_resume_continue_the_pending_tool(api):
    fetch, entered, gate, calls = blocking_tool()
    graph = agent(script(call("fetch", {"x": "a"}), "done"), [fetch])
    async with api(graph) as (client, m):
        thread = await new_thread(client)
        run = (await client.post(f"/api/v1/threads/{thread['id']}/runs", json={"message": "Why?"})).json()
        await entered.wait()
        paused = await client.post(f"/api/v1/runs/{run['id']}/pause")
        assert paused.status_code == 200 and paused.json()["status"] == "paused"
        gate.set()
        assert (await client.post(f"/api/v1/runs/{run['id']}/resume")).status_code == 200  # no body: continue
        await m.wait(uuid.UUID(run["id"]))
        assert (await client.get(f"/api/v1/runs/{run['id']}")).json()["status"] == "completed"
    messages = (await graph.aget_state({"configurable": {"thread_id": run["id"]}})).values["messages"]
    assert calls == ["a", "a"]  # the pending tool step re-ran (Spike §4: continue, not a new step)
    assert not any(isinstance(x, ToolMessage) and x.status == "error" for x in messages)


async def test_unknown_ids_are_404_and_illegal_moves_409(api):
    async with api(agent(script("x"))) as (client, m):
        assert (await client.get(f"/api/v1/runs/{uuid.uuid4()}")).status_code == 404
        for action in ("pause", "resume", "cancel"):
            assert (await client.post(f"/api/v1/runs/{uuid.uuid4()}/{action}")).status_code == 404
        missing = await client.post(f"/api/v1/threads/{uuid.uuid4()}/runs", json={"message": "x"})
        assert missing.status_code == 404
        thread = await new_thread(client)
        assert (await client.post(f"/api/v1/threads/{thread['id']}/runs", json={"message": ""})).status_code == 422
        run = (await client.post(f"/api/v1/threads/{thread['id']}/runs", json={"message": "Why?"})).json()
        await m.wait(uuid.UUID(run["id"]))
        conflict = await client.post(f"/api/v1/runs/{run['id']}/pause")
        assert conflict.status_code == 409 and "completed" in conflict.json()["detail"]
        assert (await client.post(f"/api/v1/runs/{run['id']}/branch", json={})).status_code == 422


async def test_a_run_is_branched_from_a_checkpoint(api):
    graph = agent(script("first answer", "second answer"))
    async with api(graph) as (client, m):
        thread = await new_thread(client)
        run = (await client.post(f"/api/v1/threads/{thread['id']}/runs", json={"message": "Why?"})).json()
        await m.wait(uuid.UUID(run["id"]))
        cfg = {"configurable": {"thread_id": run["id"]}}
        [point] = [s async for s in graph.aget_state_history(cfg) if s.next == ("model",)]
        checkpoint_id = point.config["configurable"]["checkpoint_id"]
        branched = await client.post(f"/api/v1/runs/{run['id']}/branch", json={"checkpoint_id": checkpoint_id})
        assert branched.status_code == 201
        child = branched.json()
        assert child["parent_run_id"] == run["id"] and child["branch_from_checkpoint"] == checkpoint_id
        await m.wait(uuid.UUID(child["id"]))
        assert (await client.get(f"/api/v1/runs/{child['id']}")).json()["status"] == "completed"
        messages = (await client.get(f"/api/v1/threads/{thread['id']}")).json()["messages"]
        assert [x["text"] for x in messages] == ["Why?", "first answer", "second answer"]


async def test_error_responses_are_declared_in_the_schema(api):
    async with api(agent(script("x"))) as (client, _):
        paths = (await client.get("/api/openapi.json")).json()["paths"]
    error_ref = {"$ref": "#/components/schemas/ErrorOut"}
    for path, method, codes in [
        ("/api/v1/runs/{run_id}/pause", "post", {"404", "409", "503"}),
        ("/api/v1/runs/{run_id}/resume", "post", {"404", "409", "503"}),
        ("/api/v1/runs/{run_id}", "get", {"404", "503"}),
        ("/api/v1/threads/{thread_id}/runs", "post", {"404", "503"}),
        ("/api/v1/projects/{project_id}", "get", {"404", "503"}),
        ("/api/v1/threads/{thread_id}", "get", {"404", "503"}),
    ]:
        responses = paths[path][method]["responses"]
        assert codes <= responses.keys(), path
        assert all(responses[c]["content"]["application/json"]["schema"] == error_ref for c in codes), path
