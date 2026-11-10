"""The run event log (spec §17, ADR 0012): AG-UI events persisted to `run_event`, then fanned out in process.

The drive task is the only writer of its run's events. Rows are published only after they commit, so a live
subscriber and a replay see the same rows with the same `seq`, and a client dedupes by `seq`.
"""

import asyncio
import contextlib
import time
import uuid
from collections import defaultdict
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator
from dataclasses import dataclass
from typing import Any

from ag_ui.core import BaseEvent
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from researchly.db.models import Run, RunEvent

LEAD = "lead"
SKIPPED = ("RAW", "MESSAGES_SNAPSHOT")  # RAW repeats LangGraph's own events; message snapshots grow quadratically
DELTAS = {"TEXT_MESSAGE_CONTENT": "messageId", "TOOL_CALL_ARGS": "toolCallId", "REASONING_MESSAGE_CONTENT": "messageId"}
ERRORS = ("RUN_ERROR", "SUBAGENT_ERROR")
ERROR_TEXT = "The run failed."  # the adapter puts str(exc) here, which can carry a provider's response
ENDS = ("RUN_FINISHED", "RUN_ERROR")
LIVE = ("pending", "running")

RunUpdate = Callable[[AsyncSession], Awaitable[None]]


@dataclass(frozen=True)
class StoredEvent:
    seq: int
    type: str
    agent: str | None
    payload: dict[str, Any]


class RunHub:
    """Per-run publish/subscribe inside the app process (spec §4.2: replaces LISTEN/NOTIFY)."""

    def __init__(self) -> None:
        self._subscribers: defaultdict[uuid.UUID, set[asyncio.Queue[StoredEvent]]] = defaultdict(set)

    @contextlib.contextmanager
    def subscribe(self, run_id: uuid.UUID) -> Iterator[asyncio.Queue[StoredEvent]]:
        # ponytail: unbounded queue; a stalled client buffers one run's events until it disconnects
        queue: asyncio.Queue[StoredEvent] = asyncio.Queue()
        self._subscribers[run_id].add(queue)
        try:
            yield queue
        finally:
            self._subscribers[run_id].discard(queue)
            if not self._subscribers[run_id]:
                del self._subscribers[run_id]

    def publish(self, run_id: uuid.UUID, events: list[StoredEvent]) -> None:
        for queue in self._subscribers.get(run_id, ()):
            for event in events:
                queue.put_nowait(event)


class RunEventLog:
    """Appends one run's AG-UI events: skips RAW and message snapshots, scrubs errors, coalesces deltas."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        hub: RunHub,
        run_id: uuid.UUID,
        *,
        max_bytes: int = 2048,
        max_age_s: float = 0.25,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.sessions, self.hub, self.run_id = sessions, hub, run_id
        self.max_bytes, self.max_age_s, self.clock = max_bytes, max_age_s, clock
        self.open = False  # an AG-UI run has started and not yet finished
        self._pending: list[tuple[str, str, dict[str, Any]]] = []  # deltas only: anything else flushes at once
        self._since: float | None = None
        self._seq: int | None = None
        self._agents: dict[str, str] = {}  # subagentRunId -> subagent name, from SUBAGENT_STARTED

    async def append(self, event: BaseEvent, *, run_update: RunUpdate | None = None) -> None:
        kind = event.type.value
        if kind in SKIPPED:
            return
        payload = event.model_dump(mode="json", by_alias=True, exclude_none=True)
        if kind in ERRORS:
            payload["message"] = ERROR_TEXT
        sub = payload.get("subagentRunId")
        if kind == "SUBAGENT_STARTED" and sub is not None:
            self._agents[sub] = payload["name"]
        agent = LEAD if sub is None else self._agents.get(sub, "subagent")
        key = DELTAS.get(kind)
        if run_update is not None and key is not None:
            raise ValueError("run_update needs a non-delta event: deltas may be held back")
        if key is None:
            self._pending.append((kind, agent, payload))
            await self.flush(run_update)
            return
        last = self._pending[-1] if self._pending else None
        if last is not None and last[0] == kind and last[2][key] == payload[key]:
            last[2]["delta"] += payload["delta"]
            delta = last[2]["delta"]
        else:
            self._pending.append((kind, agent, payload))
            delta = payload["delta"]
        if self._since is None:
            self._since = self.clock()
        # no timer task: the batch's age is checked when the next event arrives (an END event always follows)
        if len(delta.encode()) >= self.max_bytes or self.clock() - self._since >= self.max_age_s:
            await self.flush()

    async def flush(self, run_update: RunUpdate | None = None) -> None:
        """Commit the pending rows, and the run's status change, in one transaction; then publish them."""
        rows, self._pending, self._since = self._pending, [], None  # a failed write drops them; the run then fails
        if not rows and run_update is None:
            return
        async with self.sessions.begin() as session:
            base = self._seq
            if base is None:
                last = select(func.max(RunEvent.seq)).where(RunEvent.run_id == self.run_id)
                base = await session.scalar(last) or 0
            stored = [StoredEvent(base + i, kind, agent, payload) for i, (kind, agent, payload) in enumerate(rows, 1)]
            session.add_all(
                RunEvent(run_id=self.run_id, seq=e.seq, type=e.type, agent=e.agent, payload=e.payload) for e in stored
            )
            if run_update is not None:
                await run_update(session)
        self._seq = base + len(stored)
        for event in stored:
            if event.type == "RUN_STARTED":
                self.open = True
            elif event.type in ENDS:
                self.open = False
        self.hub.publish(self.run_id, stored)


async def replay(sessions: async_sessionmaker[AsyncSession], run_id: uuid.UUID, after: int) -> list[StoredEvent]:
    # ponytail: loads the whole tail at once; page it if run logs grow past a few thousand rows
    stmt = (
        select(RunEvent.seq, RunEvent.type, RunEvent.agent, RunEvent.payload)
        .where(RunEvent.run_id == run_id, RunEvent.seq > after)
        .order_by(RunEvent.seq)
    )
    async with sessions() as session:
        rows = (await session.execute(stmt)).all()
    return [StoredEvent(seq=r.seq, type=r.type, agent=r.agent, payload=r.payload) for r in rows]


async def follow(
    sessions: async_sessionmaker[AsyncSession], hub: RunHub, run_id: uuid.UUID, after: int
) -> AsyncGenerator[StoredEvent, None]:
    """Replay the rows after `after`, then tail live rows until the current AG-UI run ends.

    Subscribing first means no row falls between the replay and the tail; rows already replayed are skipped by
    `seq`. A run at rest ends the stream after its replay.
    """
    with hub.subscribe(run_id) as queue:
        async with sessions() as session:
            status = await session.scalar(select(Run.status).where(Run.id == run_id))
        last = after
        for event in await replay(sessions, run_id, after):
            yield event
            last = event.seq
        if status not in LIVE:
            return
        while True:
            event = await queue.get()
            if event.seq > last:
                yield event
                last = event.seq
            if event.type in ENDS:  # even one the replay already sent: that was this run's end
                return
