import uuid
from decimal import Decimal

import pytest
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.messages import AIMessage, ToolMessage
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


async def _finished_run(client, m) -> dict:
    thread = await new_thread(client)
    run = (await client.post(f"/api/v1/threads/{thread['id']}/runs", json={"message": "Why?"})).json()
    await m.wait(uuid.UUID(run["id"]))
    return run


def _note_tool():
    notes: list[tuple[str, dict]] = []

    @tool
    def note(text: str, extra: dict) -> str:
        """Record a note."""
        notes.append((text, extra))
        return "noted"

    return note, notes


def _review(*tools):
    return HumanInTheLoopMiddleware(
        interrupt_on={t.name: {"allowed_decisions": ["approve", "edit", "reject"]} for t in tools}
    )


async def test_every_decision_reaches_the_manager_intact_and_in_order(api, monkeypatch):
    sent = [
        {"type": "approve"},
        {"type": "edit", "edited_action": {"name": "note", "args": {"text": "t", "extra": {"k": [1, {"z": 2}]}}}},
        {"type": "reject", "message": "no, because"},
        {"type": "respond", "message": "here is more"},
        {"type": "reject"},
    ]
    async with api(agent(script("x"))) as (client, m):
        run = await _finished_run(client, m)
        seen = []

        async def spy(run_id, decisions=None):
            seen.append(decisions)

        monkeypatch.setattr(m, "resume", spy)
        assert (await client.post(f"/api/v1/runs/{run['id']}/resume", json={"decisions": sent})).status_code == 200
    assert seen == [sent]  # a reject without a message carries no `message` key


async def test_edited_and_approved_actions_run_in_the_order_the_user_decided(api):
    note, notes = _note_tool()
    two_calls = AIMessage(
        "",
        tool_calls=[
            {"name": "note", "args": {"text": "a", "extra": {}}, "id": "call-a"},
            {"name": "note", "args": {"text": "b", "extra": {"orig": True}}, "id": "call-b"},
        ],
    )
    graph = agent(script(two_calls, "Done."), [note], [_review(note)])
    edited = {"name": "note", "args": {"text": "edited", "extra": {"k": [1, {"z": 2}]}}}
    async with api(graph) as (client, m):
        run = await _finished_run(client, m)
        assert (await client.get(f"/api/v1/runs/{run['id']}")).json()["status"] == "awaiting_user"
        decisions = [{"type": "edit", "edited_action": edited}, {"type": "approve"}]
        assert (await client.post(f"/api/v1/runs/{run['id']}/resume", json={"decisions": decisions})).status_code == 200
        await m.wait(uuid.UUID(run["id"]))
        assert (await client.get(f"/api/v1/runs/{run['id']}")).json()["status"] == "completed"
    # action 1 (a) was edited, action 2 (b) approved as asked; the tool node runs them concurrently
    assert sorted(notes) == [("b", {"orig": True}), ("edited", {"k": [1, {"z": 2}]})]


async def test_a_plan_is_rejected_through_the_api(api):
    note, notes = _note_tool()
    graph = agent(script(call("note", {"text": "a", "extra": {}}), "Done."), [note], [_review(note)])
    async with api(graph) as (client, m):
        run = await _finished_run(client, m)
        assert (await client.get(f"/api/v1/runs/{run['id']}")).json()["status"] == "awaiting_user"
        decisions = [{"type": "reject", "message": "not this plan"}]
        rejected = await client.post(f"/api/v1/runs/{run['id']}/resume", json={"decisions": decisions})
        assert rejected.status_code == 200 and rejected.json()["status"] == "cancelled"
        await m.wait(uuid.UUID(run["id"]))
        assert (await client.get(f"/api/v1/runs/{run['id']}")).json()["status"] == "cancelled"
    assert notes == []  # the rejected tool never ran


async def test_decisions_with_unknown_fields_are_rejected(api):
    async with api(agent(script("x"))) as (client, m):
        run = await _finished_run(client, m)
        url = f"/api/v1/runs/{run['id']}/resume"
        edit = {"type": "edit", "edited_action": {"name": "n", "args": {}, "arg": {}}}
        for decision in ({"type": "reject", "mesage": "typo"}, {"type": "approve", "x": 1}, edit):
            assert (await client.post(url, json={"decisions": [decision]})).status_code == 422, decision


async def test_cancel_ends_a_running_run(api):
    fetch, entered, gate, _ = blocking_tool()
    graph = agent(script(call("fetch", {"x": "a"}), "done"), [fetch])
    async with api(graph) as (client, m):
        thread = await new_thread(client)
        run = (await client.post(f"/api/v1/threads/{thread['id']}/runs", json={"message": "Why?"})).json()
        await entered.wait()
        cancelled = await client.post(f"/api/v1/runs/{run['id']}/cancel")
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
        assert cancelled.json()["ended_at"]
        gate.set()
        await m.wait(uuid.UUID(run["id"]))
        assert (await client.get(f"/api/v1/runs/{run['id']}")).json()["status"] == "cancelled"


async def test_finished_runs_refuse_cancel_and_resume(api):
    async with api(agent(script("x"))) as (client, m):
        run = await _finished_run(client, m)
        for action in ("cancel", "resume"):
            r = await client.post(f"/api/v1/runs/{run['id']}/{action}")
            assert r.status_code == 409 and "completed" in r.json()["detail"], action


async def test_a_running_run_cannot_be_branched(api):
    fetch, entered, gate, _ = blocking_tool()
    graph = agent(script(call("fetch", {"x": "a"}), "done"), [fetch])
    async with api(graph) as (client, m):
        thread = await new_thread(client)
        run = (await client.post(f"/api/v1/threads/{thread['id']}/runs", json={"message": "Why?"})).json()
        await entered.wait()
        busy = await client.post(f"/api/v1/runs/{run['id']}/branch", json={"checkpoint_id": "c"})
        assert busy.status_code == 409 and "pause the run" in busy.json()["detail"]
        gate.set()


async def test_branching_an_unknown_run_is_404(api):
    async with api(agent(script("x"))) as (client, _):
        r = await client.post(f"/api/v1/runs/{uuid.uuid4()}/branch", json={"checkpoint_id": "c"})
        assert r.status_code == 404 and "run" in r.json()["detail"]


OPERATION_IDS = {
    "createProject",
    "listProjects",
    "getProject",
    "renameProject",
    "createThread",
    "listThreads",
    "getThread",
    "createRun",
    "getRun",
    "pauseRun",
    "resumeRun",
    "cancelRun",
    "branchRun",
    "streamRun",
}


async def test_error_responses_and_operation_ids_are_pinned_in_the_schema(api):
    async with api(agent(script("x"))) as (client, _):
        paths = (await client.get("/api/openapi.json")).json()["paths"]
    # the generated client's method names come from these ids: a rename is a breaking change
    ops = {
        op["operationId"]: (path, method, op)
        for path, item in paths.items()
        if path.startswith("/api/v1/") and path != "/api/v1/system"
        for method, op in item.items()
    }
    assert ops.keys() == OPERATION_IDS
    error_ref = {"$ref": "#/components/schemas/ErrorOut"}
    lookups = OPERATION_IDS - {"createProject", "listProjects"}
    conflicts = {"pauseRun", "resumeRun", "cancelRun", "branchRun"}
    for op_id, (_path, _, op) in ops.items():
        responses = op["responses"]
        expected = {"503"} | ({"404"} if op_id in lookups else set()) | ({"409"} if op_id in conflicts else set())
        assert {c for c in responses if c in {"404", "409", "503"}} == expected, op_id
        assert all(responses[c]["content"]["application/json"]["schema"] == error_ref for c in expected), op_id
