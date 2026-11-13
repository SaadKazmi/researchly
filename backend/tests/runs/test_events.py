import asyncio

import anyio
import pytest
from ag_ui.core import (
    CustomEvent,
    MessagesSnapshotEvent,
    RawEvent,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    StepStartedEvent,
    SubagentStartedEvent,
    TextMessageContentEvent,
    ToolCallArgsEvent,
)

from researchly.db import migrate
from researchly.db.engine import make_engine, make_sessionmaker
from researchly.runs import events
from researchly.runs.events import RunEventLog, RunHub, follow, replay

from ..agents.fakes import seed_run, until

pytestmark = [pytest.mark.anyio, pytest.mark.db]


def frozen() -> float:
    return 0.0


def text(delta: str, mid: str = "m1") -> TextMessageContentEvent:
    return TextMessageContentEvent(message_id=mid, delta=delta)


def step(name: str) -> StepStartedEvent:
    return StepStartedEvent(step_name=name)


@pytest.fixture
async def sm(db_url):
    migrate.upgrade(db_url)
    engine = make_engine(db_url)
    yield make_sessionmaker(engine)
    await engine.dispose()


async def test_adjacent_deltas_coalesce_until_a_non_delta_event(sm):
    run = await seed_run(sm)
    log = RunEventLog(sm, RunHub(), run.id, clock=frozen)
    for event in (
        text("The "),
        text("answer"),
        ToolCallArgsEvent(tool_call_id="c1", delta='{"x":'),
        ToolCallArgsEvent(tool_call_id="c1", delta='"a"}'),
        text(".", "m2"),
    ):
        await log.append(event)
    assert await replay(sm, run.id, 0) == []  # deltas wait for the next non-delta event
    await log.append(step("model"))
    got = [(e.seq, e.type, e.payload.get("delta")) for e in await replay(sm, run.id, 0)]
    assert got == [
        (1, "TEXT_MESSAGE_CONTENT", "The answer"),
        (2, "TOOL_CALL_ARGS", '{"x":"a"}'),
        (3, "TEXT_MESSAGE_CONTENT", "."),
        (4, "STEP_STARTED", None),
    ]


async def test_deltas_flush_at_2_kb_and_after_250_ms(sm):
    run = await seed_run(sm)
    now = [0.0]
    log = RunEventLog(sm, RunHub(), run.id, clock=lambda: now[0])
    await log.append(text("a" * 2047))
    assert await replay(sm, run.id, 0) == []
    await log.append(text("b"))  # 2048 bytes
    await log.append(text("c"))
    now[0] = 0.3
    await log.append(text("d"))  # the batch is 300 ms old
    assert [len(e.payload["delta"]) for e in await replay(sm, run.id, 0)] == [2048, 2]


async def test_raw_events_and_message_snapshots_are_skipped_and_errors_scrubbed(sm):
    run = await seed_run(sm)
    log = RunEventLog(sm, RunHub(), run.id)
    await log.append(RawEvent(event={"provider": "secret"}))
    await log.append(MessagesSnapshotEvent(messages=[]))
    await log.append(RunErrorEvent(message="provider said sk-secret", code="APIError"))
    got = [(e.type, e.payload) for e in await replay(sm, run.id, 0)]
    assert got == [("RUN_ERROR", {"type": "RUN_ERROR", "message": "The run failed.", "code": "APIError"})]


async def test_a_raw_event_piggy_backed_on_another_event_is_not_persisted(sm):
    run = await seed_run(sm)
    log = RunEventLog(sm, RunHub(), run.id)
    await log.append(StepStartedEvent(step_name="plan", raw_event={"provider": "secret"}))
    [row] = await replay(sm, run.id, 0)
    assert row.payload == {"type": "STEP_STARTED", "stepName": "plan"}


async def test_subagent_events_are_attributed_by_name(sm):
    run = await seed_run(sm)
    log = RunEventLog(sm, RunHub(), run.id)
    await log.append(SubagentStartedEvent(subagent_run_id="tools:1", name="scout"))
    await log.append(CustomEvent(name="cost", value={}, subagent_run_id="tools:1"))
    await log.append(CustomEvent(name="cost", value={}, subagent_run_id="tools:2"))
    await log.append(step("model"))
    assert [e.agent for e in await replay(sm, run.id, 0)] == ["scout", "scout", "subagent", "lead"]


async def test_subscribers_get_exactly_the_persisted_rows(sm):
    run = await seed_run(sm)
    hub = RunHub()
    log = RunEventLog(sm, hub, run.id, clock=frozen)
    with hub.subscribe(run.id) as queue:
        for event in (text("a"), text("b"), step("x")):
            await log.append(event)
        live = [queue.get_nowait() for _ in range(queue.qsize())]
    assert live == await replay(sm, run.id, 0) and [e.seq for e in live] == [1, 2]
    assert run.id not in hub._subscribers


async def test_seq_continues_across_logs(sm):
    run = await seed_run(sm)
    await RunEventLog(sm, RunHub(), run.id).append(step("a"))
    await RunEventLog(sm, RunHub(), run.id).append(step("b"))
    assert [e.seq for e in await replay(sm, run.id, 0)] == [1, 2]


async def test_a_failed_run_update_persists_nothing(sm):
    run = await seed_run(sm)

    async def boom(session) -> None:
        raise RuntimeError("no")

    log = RunEventLog(sm, RunHub(), run.id, clock=frozen)
    await log.append(text("a"))
    with pytest.raises(RuntimeError):
        await log.append(RunStartedEvent(thread_id="t", run_id="r"), run_update=boom)
    assert await replay(sm, run.id, 0) == [] and not log.open


async def test_follow_replays_then_tails_until_the_ag_ui_run_ends(sm):
    run = await seed_run(sm, status="running")
    hub = RunHub()
    log = RunEventLog(sm, hub, run.id)
    await log.append(RunStartedEvent(thread_id="t", run_id="r"))
    await log.append(step("model"))
    assert log.open
    got: list[str] = []

    async def consume() -> None:
        async for event in follow(sm, hub, run.id, 1):
            got.append(event.type)

    task = asyncio.create_task(consume())

    async def subscribed() -> bool:
        return run.id in hub._subscribers

    await until(subscribed)
    await log.append(RunFinishedEvent(thread_id="t", run_id="r"))
    with anyio.fail_after(5):
        await task
    assert got == ["STEP_STARTED", "RUN_FINISHED"] and not log.open


async def test_follow_ends_after_the_replay_when_the_run_is_at_rest(sm):
    run = await seed_run(sm, status="completed")
    log = RunEventLog(sm, RunHub(), run.id)
    await log.append(RunStartedEvent(thread_id="t", run_id="r"))
    await log.append(RunFinishedEvent(thread_id="t", run_id="r"))
    with anyio.fail_after(5):
        assert [e.seq async for e in follow(sm, RunHub(), run.id, 0)] == [1, 2]


async def test_a_delta_event_cannot_carry_a_run_update(sm):
    run = await seed_run(sm)

    async def noop(session) -> None:
        return None

    log = RunEventLog(sm, RunHub(), run.id)
    with pytest.raises(ValueError):
        await log.append(text("a"), run_update=noop)
    assert await replay(sm, run.id, 0) == []


async def test_a_stream_opened_in_the_pause_window_ends_on_the_replayed_terminal(sm):
    """`pause` commits the cancelled RUN_FINISHED before it commits status="paused" (manager.py:275, :138).

    A stream opened between those two commits reads `running`, replays the terminal row, and would otherwise
    block on the queue forever: the run is untouched, but the connection is held open on pings for good.
    The last replayed row being a terminal ends the stream whatever the status row still says.
    """
    run = await seed_run(sm, status="running")
    hub = RunHub()
    log = RunEventLog(sm, hub, run.id)
    await log.append(RunStartedEvent(thread_id="t", run_id="r"))
    await log.append(RunFinishedEvent(thread_id="t", run_id="r"))
    got: list[int] = []
    with anyio.fail_after(5):
        async for event in follow(sm, hub, run.id, 0):
            got.append(event.seq)
    assert got == [1, 2]


async def test_a_resumed_run_keeps_tailing_past_the_terminal_its_replay_starts_with(sm):
    """The mirror of the pause window: only the LAST replayed row may end the stream.

    A resumed run's log holds the paused segment's RUN_FINISHED followed by the resume's RUN_STARTED, so a
    client reconnecting from seq 0 replays a terminal that is not the end. Ending on any replayed terminal
    would cut the stream off at the resume, and the client would watch a live run in silence.
    """
    run = await seed_run(sm, status="running")
    hub = RunHub()
    log = RunEventLog(sm, hub, run.id)
    await log.append(RunFinishedEvent(thread_id="t", run_id="r"))  # the segment that was paused
    await log.append(RunStartedEvent(thread_id="t", run_id="r2"))  # the resume: the run is live again
    got: list[int] = []

    async def consume() -> None:
        async for event in follow(sm, hub, run.id, 0):
            got.append(event.seq)

    task = asyncio.create_task(consume())

    async def replayed() -> bool:
        return len(got) >= 2

    await until(replayed)
    await log.append(step("still working"))
    await log.append(RunFinishedEvent(thread_id="t", run_id="r2"))
    with anyio.fail_after(5):
        await task
    assert got == [1, 2, 3, 4]  # the replayed terminal at seq 1 did not end the stream


async def test_follow_subscribes_before_it_replays_so_no_row_falls_in_the_gap(sm, monkeypatch):
    """The other race: a row committed and published AFTER the SELECT reaches a subscriber that already exists.

    Replaying first and subscribing second would lose it in both places - the SELECT has already run, and the
    queue does not exist yet - so the client would never see it and, dedupe being by seq, would never ask again.
    """
    run = await seed_run(sm, status="running")
    hub = RunHub()
    log = RunEventLog(sm, hub, run.id)
    await log.append(RunStartedEvent(thread_id="t", run_id="r"))
    real_replay = events.replay

    async def late_publishing_replay(sessions, run_id, after):
        rows = await real_replay(sessions, run_id, after)
        await log.append(step("after the select"))  # only a subscription that already exists can catch this
        return rows

    monkeypatch.setattr(events, "replay", late_publishing_replay)
    got: list[int] = []

    async def consume() -> None:
        async for event in follow(sm, hub, run.id, 0):
            got.append(event.seq)

    task = asyncio.create_task(consume())

    async def replayed() -> bool:
        return len(got) >= 2

    await until(replayed)
    await log.append(RunFinishedEvent(thread_id="t", run_id="r"))
    with anyio.fail_after(5):
        await task
    assert got == [1, 2, 3]  # gapless: seq 2 was published between the SELECT and the first tail read


async def test_follow_skips_a_row_that_arrives_through_both_the_replay_and_the_queue(sm, monkeypatch):
    run = await seed_run(sm, status="running")
    hub = RunHub()
    log = RunEventLog(sm, hub, run.id)
    await log.append(RunStartedEvent(thread_id="t", run_id="r"))
    real_replay = events.replay

    async def racing_replay(sessions, run_id, after):
        await log.append(step("late"))  # committed and published after the subscription, before the SELECT
        return await real_replay(sessions, run_id, after)

    monkeypatch.setattr(events, "replay", racing_replay)
    got: list[int] = []

    async def consume() -> None:
        async for event in follow(sm, hub, run.id, 0):
            got.append(event.seq)

    task = asyncio.create_task(consume())

    async def replayed() -> bool:
        return len(got) >= 2

    await until(replayed)
    await log.append(RunFinishedEvent(thread_id="t", run_id="r"))
    with anyio.fail_after(5):
        await task
    assert got == [1, 2, 3]
