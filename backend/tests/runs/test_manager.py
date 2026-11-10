import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from langchain.agents.middleware import HumanInTheLoopMiddleware, ToolCallLimitMiddleware
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import select, update

from researchly.agents.middleware import BUDGET_SPENT, BudgetGuard
from researchly.db import migrate, provision
from researchly.db.engine import make_engine, make_sessionmaker
from researchly.db.models import Message, Run, RunEvent
from researchly.runs.manager import INTERRUPT, RUN_ERROR, RunManager, RunStateError

from ..agents.fakes import agent, blocking_tool, call, script, seed_run, until

pytestmark = [pytest.mark.anyio, pytest.mark.db]
QUESTION = "What makes Transformers fast to train?"


@pytest.fixture
async def sm(db_url):
    migrate.upgrade(db_url)
    engine = make_engine(db_url)
    yield make_sessionmaker(engine)
    await engine.dispose()


def manager(sm, graph: Any) -> RunManager:
    async def build(run: Run) -> Any:
        return graph

    return RunManager(sm, build)


async def row(sm, run_id: uuid.UUID) -> Run:
    async with sm() as session:
        return await session.get_one(Run, run_id)


async def answers(sm, run_id: uuid.UUID) -> list[str]:
    async with sm() as session:
        stmt = select(Message.content).where(Message.run_id == run_id, Message.role == "assistant")
        return [c["text"] for c in await session.scalars(stmt)]


async def events(sm, run_id: uuid.UUID, kind: str) -> list[dict]:
    async with sm() as session:
        stmt = select(RunEvent.payload).where(RunEvent.run_id == run_id, RunEvent.type == kind).order_by(RunEvent.seq)
        return list(await session.scalars(stmt))


async def test_create_takes_the_tier_budget_and_stores_the_question(sm):
    seeded = await seed_run(sm)
    run = await manager(sm, None).create(seeded.project_id, seeded.thread_id, "Why?", effort="quick")
    stored = await row(sm, run.id)
    assert stored.status == "pending" and stored.budget_usd == Decimal("0.1")
    assert stored.checkpoint_thread_id == str(run.id)
    async with sm() as session:
        assert await session.scalar(select(Message.content).where(Message.run_id == run.id)) == {"text": "Why?"}


async def test_a_run_completes_and_stores_the_answer(sm):
    run = await seed_run(sm)
    model = script("The answer.")
    m = manager(sm, agent(model))
    await m.start(run.id)
    await m.wait(run.id)
    stored = await row(sm, run.id)
    assert stored.status == "completed" and stored.started_at and stored.ended_at
    assert await answers(sm, run.id) == ["The answer."]
    assert model.seen[0][-1].text == QUESTION
    with pytest.raises(RunStateError):
        await m.start(run.id)
    with pytest.raises(RunStateError):
        await m.cancel(run.id)


async def test_a_spent_budget_ends_the_run_partial(sm):
    run = await seed_run(sm)

    async def spent(run_id: uuid.UUID) -> Decimal:
        return Decimal(5)

    model = script("never said")
    m = manager(sm, agent(model, middleware=[BudgetGuard(spent)]))
    await m.start(run.id)
    await m.wait(run.id)
    assert (await row(sm, run.id)).status == "partial"
    assert await answers(sm, run.id) == [BUDGET_SPENT] and model.seen == []


async def test_ask_user_waits_for_the_answer(sm):
    run = await seed_run(sm)

    @tool
    def ask_user(question: str) -> str:
        """Ask the user."""
        return "unanswered"

    hitl = HumanInTheLoopMiddleware(interrupt_on={"ask_user": {"allowed_decisions": ["respond"]}})
    model = script(call("ask_user", {"question": "Which years?"}), "Thanks.")
    m = manager(sm, agent(model, [ask_user], [hitl]))
    await m.start(run.id)
    await m.wait(run.id)
    assert (await row(sm, run.id)).status == "awaiting_user"
    [event] = await events(sm, run.id, INTERRUPT)
    assert event["interrupts"][0]["action_requests"][0]["name"] == "ask_user"
    with pytest.raises(RunStateError):
        await m.resume(run.id)  # decisions are required
    await m.resume(run.id, [{"type": "respond", "message": "2020 onwards"}])
    await m.wait(run.id)
    assert (await row(sm, run.id)).status == "completed"
    assert [t.text for t in model.seen[1] if isinstance(t, ToolMessage)] == ["2020 onwards"]


async def test_a_rejected_plan_cancels_the_run(sm):
    run = await seed_run(sm)

    @tool
    def submit_plan(plan: str) -> str:
        """Submit the plan."""
        return "approved"

    hitl = HumanInTheLoopMiddleware(interrupt_on={"submit_plan": {"allowed_decisions": ["approve", "edit", "reject"]}})
    model = script(call("submit_plan", {"plan": "p"}), "never said")
    m = manager(sm, agent(model, [submit_plan], [hitl]))
    await m.start(run.id)
    await m.wait(run.id)
    await m.resume(run.id, [{"type": "reject"}])
    stored = await row(sm, run.id)
    assert stored.status == "cancelled" and stored.ended_at and len(model.seen) == 1


async def test_pause_and_resume_continue_from_the_last_checkpoint(sm):
    run = await seed_run(sm)
    fetch, entered, gate, calls = blocking_tool()
    model = script(call("fetch", {"x": "a"}), "done")
    m = manager(sm, agent(model, [fetch]))
    await m.start(run.id)
    await entered.wait()
    with pytest.raises(RunStateError):
        await m.branch(run.id, "any")  # a live run cannot be branched
    with pytest.raises(RunStateError):
        await m.resume(run.id, [{"type": "reject"}])  # decisions are for a paused or waiting run
    await m.pause(run.id)
    assert (await row(sm, run.id)).status == "paused"
    gate.set()
    await m.resume(run.id)
    await m.wait(run.id)
    assert (await row(sm, run.id)).status == "completed"
    assert calls == ["a", "a"] and len(model.seen) == 2  # the tool step re-ran; the model step did not


async def test_running_runs_resume_after_a_restart(sm, db_url):
    provision.setup_agent_tables(db_url)
    run = await seed_run(sm)
    cfg = {"configurable": {"thread_id": str(run.id)}}
    async with AsyncPostgresSaver.from_conn_string(provision.conninfo(db_url)) as saver:
        fetch, entered, gate, calls = blocking_tool()
        first = agent(script(call("fetch", {"x": "a"})), [fetch], checkpointer=saver)
        m1 = manager(sm, first)
        await m1.start(run.id)
        await entered.wait()

        async def checkpointed() -> bool:
            return (await first.aget_state(cfg)).next == ("tools",)

        await until(checkpointed)
        await m1.shutdown()  # the app stops: the run stays `running`
        assert (await row(sm, run.id)).status == "running"

        gate.set()
        model = script("done")
        m2 = manager(sm, agent(model, [fetch], checkpointer=saver))  # a new process, the same database
        await m2.resume_running()
        await m2.wait(run.id)
    assert (await row(sm, run.id)).status == "completed"
    assert calls == ["a", "a"] and len(model.seen) == 1


async def test_a_branch_reruns_from_a_checkpoint_and_supersedes_its_parent(sm):
    run = await seed_run(sm)
    model = script("first answer", "second answer")
    graph = agent(model)
    m = manager(sm, graph)
    await m.start(run.id)
    await m.wait(run.id)
    cfg = {"configurable": {"thread_id": run.checkpoint_thread_id}}
    [point] = [s async for s in graph.aget_state_history(cfg) if s.next == ("model",)]
    checkpoint_id = point.config["configurable"]["checkpoint_id"]

    child = await m.branch(run.id, checkpoint_id)
    await m.wait(child.id)
    stored = await row(sm, child.id)
    assert stored.status == "completed" and stored.parent_run_id == run.id
    assert stored.branch_from_checkpoint == checkpoint_id and stored.checkpoint_thread_id == run.checkpoint_thread_id
    assert await answers(sm, child.id) == ["second answer"]

    async with sm() as session, session.begin():
        await session.execute(update(Run).where(Run.id == run.id).values(status="paused"))
    with pytest.raises(RunStateError, match="branch"):
        await m.resume(run.id)  # the thread's latest checkpoint is the branch's


async def test_a_branch_stopped_before_its_first_checkpoint_resumes_from_its_fork(sm):
    run = await seed_run(sm)
    graph = agent(script("first answer", "second answer"))
    m = manager(sm, graph)
    await m.start(run.id)
    await m.wait(run.id)
    cfg = {"configurable": {"thread_id": run.checkpoint_thread_id}}
    [point] = [s async for s in graph.aget_state_history(cfg) if s.next == ("model",)]
    child = Run(
        id=uuid.uuid4(),
        project_id=run.project_id,
        thread_id=run.thread_id,
        effort=run.effort,
        status="running",
        budget_usd=run.budget_usd,
        spent_usd=Decimal(0),
        checkpoint_thread_id=run.checkpoint_thread_id,
        parent_run_id=run.id,
        branch_from_checkpoint=point.config["configurable"]["checkpoint_id"],
        started_at=datetime.now(UTC),
    )
    async with sm() as session, session.begin():
        session.add(child)  # the app stopped after branch() stored the row, before the child's first checkpoint
    restarted = manager(sm, graph)
    await restarted.resume_running()
    await restarted.wait(child.id)
    assert await answers(sm, child.id) == ["second answer"]  # not the parent's finished state


async def test_the_quick_tool_cap_ends_the_run_partial_with_an_answer(sm):
    run = await seed_run(sm)

    @tool
    def echo(x: str) -> str:
        """Echo."""
        return x

    model = script(call("echo", {"x": "a"}), call("echo", {"x": "b"}), "What I found.")
    cap = ToolCallLimitMiddleware(thread_limit=1, exit_behavior="continue")
    m = manager(sm, agent(model, [echo], [cap]))
    await m.start(run.id)
    await m.wait(run.id)
    assert (await row(sm, run.id)).status == "partial"
    assert await answers(sm, run.id) == ["What I found."]


async def test_a_failing_build_fails_the_run_with_an_event(sm):
    run = await seed_run(sm)

    async def build(run: Run) -> Any:
        raise ValueError("no provider configured")

    m = RunManager(sm, build)
    await m.start(run.id)
    await m.wait(run.id)
    assert (await row(sm, run.id)).status == "failed"
    assert await events(sm, run.id, RUN_ERROR) == [{"error": "ValueError"}]


async def test_illegal_transitions_are_refused(sm):
    run = await seed_run(sm)
    m = manager(sm, agent(script("x")))
    for action in (m.pause, m.resume):
        with pytest.raises(RunStateError):
            await action(run.id)  # a pending run is neither running nor paused
    with pytest.raises(RunStateError):
        await m.branch(run.id, "any")
    await m.cancel(run.id)
    assert (await row(sm, run.id)).status == "cancelled"


async def test_decisions_for_a_paused_run_are_not_dropped(sm):
    run = await seed_run(sm)

    @tool
    def submit_plan(plan: str) -> str:
        """Submit the plan."""
        return "approved"

    hitl = HumanInTheLoopMiddleware(interrupt_on={"submit_plan": {"allowed_decisions": ["approve", "edit", "reject"]}})
    model = script(call("submit_plan", {"plan": "p"}), "never said")
    m = manager(sm, agent(model, [submit_plan], [hitl]))
    await m.start(run.id)
    await m.wait(run.id)
    await m.pause(run.id)  # paused while the plan awaits review
    assert (await row(sm, run.id)).status == "paused"
    await m.resume(run.id, [{"type": "reject"}])
    assert (await row(sm, run.id)).status == "cancelled" and len(model.seen) == 1


@pytest.mark.parametrize("attempt", range(5))
async def test_cancel_cannot_race_resume(sm, attempt):
    run = await seed_run(sm)
    fetch, entered, gate, calls = blocking_tool()
    m = manager(sm, agent(script(call("fetch", {"x": "a"}), "done"), [fetch]))
    await m.start(run.id)
    await entered.wait()
    await m.pause(run.id)
    await asyncio.gather(m.resume(run.id), m.cancel(run.id), return_exceptions=True)
    try:
        assert (await row(sm, run.id)).status == "cancelled"
        assert run.id not in m._tasks  # no orphan task left running
    finally:
        gate.set()
        await m.wait(run.id)
    assert await answers(sm, run.id) == []
