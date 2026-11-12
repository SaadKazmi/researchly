"""Runs as in-process asyncio tasks over a checkpointed graph (spec §6.5, §16), driven through the AG-UI adapter.

The database row is the source of truth for a run's status; LangGraph's checkpoints hold its progress. Every status
change is a conditional UPDATE, so two callers can never both move a run. The drive task is the only writer of its
run's events (ADR 0012), and a run's terminal event commits together with its new status.
"""

import asyncio
import contextlib
import logging
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from ag_ui.core import (
    ResumeEntry,
    RunAgentInput,
    RunErrorEvent,
    RunFinishedCancelledOutcome,
    RunFinishedEvent,
    RunFinishedInterruptOutcome,
    RunStartedEvent,
    UserMessage,
)
from langchain_core.messages import ToolMessage
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from researchly.agents.context import EFFORTS, EffortName, RunContext
from researchly.agents.middleware import BUDGET_SPENT, LOOP_STOP
from researchly.db.models import Message, Project, Run, RunEvent
from researchly.llm.ledger import spent_usd
from researchly.runs.agui import ResearchlyAgent, allowed_decisions
from researchly.runs.events import RunEventLog, RunHub, RunUpdate

logger = logging.getLogger(__name__)

TERMINAL = ("partial", "completed", "cancelled", "failed")
RUN_ERROR = "RUN_ERROR"
STOPPED = (BUDGET_SPENT, LOOP_STOP)
LIMIT_HIT = "Tool call limit exceeded. Do not make additional tool calls."  # langchain ToolCallLimitMiddleware


class RunStateError(Exception):
    """The run's status does not allow this action."""


def _now() -> datetime:
    return datetime.now(UTC)


class RunManager:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], build: Callable[[Run], Awaitable[Any]]) -> None:
        self.sessions, self.build = sessions, build
        self.hub = RunHub()
        self._tasks: dict[uuid.UUID, asyncio.Task[None]] = {}
        # a pause or cancel must not interleave with a resume or start of the same run
        # ponytail: in-process locks; one app process
        self._locks: defaultdict[uuid.UUID, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def create(
        self,
        project_id: uuid.UUID,
        thread_id: uuid.UUID,
        question: str,
        *,
        effort: EffortName = "standard",
        budget_usd: Decimal | None = None,
    ) -> Run:
        run_id = uuid.uuid4()
        run = Run(
            id=run_id,
            project_id=project_id,
            thread_id=thread_id,
            effort=effort,
            status="pending",
            budget_usd=EFFORTS[effort].budget_usd if budget_usd is None else budget_usd,
            spent_usd=Decimal(0),
            checkpoint_thread_id=str(run_id),
        )
        async with self.sessions.begin() as session:
            session.add(run)
            await session.flush()
            session.add(Message(thread_id=thread_id, role="user", content={"text": question}, run_id=run_id))
        return run

    async def start(self, run_id: uuid.UUID) -> None:
        async with self._locks[run_id]:
            async with self.sessions.begin() as session:
                await self._set(session, run_id, "running", ("pending",), started_at=_now())
            self._spawn(run_id, None)

    async def resume(self, run_id: uuid.UUID, decisions: list[dict[str, Any]] | None = None) -> None:
        async with self._locks[run_id]:
            await self._resume(run_id, decisions)

    async def _resume(self, run_id: uuid.UUID, decisions: list[dict[str, Any]] | None) -> None:
        async with self.sessions() as session:
            run = await session.get_one(Run, run_id)
            superseded = await self._superseded(session, run)
        if superseded:
            raise RunStateError("a newer branch owns this run's checkpoints; resume the branch instead")
        if decisions:  # a run paused while awaiting the user still has its interrupt pending
            if run.status not in ("paused", "awaiting_user"):
                raise RunStateError(f"run {run_id} is {run.status}; decisions are for a paused or waiting run")
            await self._check(run_id, decisions)
            if any(d.get("type") == "reject" for d in decisions):
                await self._cancel(run_id)
                return
        elif run.status == "awaiting_user":
            raise RunStateError("this run is waiting for the user's decisions")
        async with self.sessions.begin() as session:
            await self._set(session, run_id, "running", ("paused", "awaiting_user"))
        self._spawn(run_id, decisions or None)

    async def _check(self, run_id: uuid.UUID, decisions: list[dict[str, Any]]) -> None:
        """Decisions must answer the open review action by action: HITL fails the run on a mismatch."""
        stmt = select(RunEvent.payload).where(RunEvent.run_id == run_id, RunEvent.type == "RUN_FINISHED")
        async with self.sessions() as session:
            last = await session.scalar(stmt.order_by(RunEvent.seq.desc()).limit(1))
        outcome = (last or {}).get("outcome") or {}
        allowed = allowed_decisions(outcome["interrupts"][0]) if outcome.get("type") == "interrupt" else None
        if (
            allowed is None
            or len(allowed) != len(decisions)
            or any(d.get("type") not in a for d, a in zip(decisions, allowed, strict=True))
        ):
            raise RunStateError(f"these decisions do not answer the run's open review (allowed: {allowed})")

    async def pause(self, run_id: uuid.UUID) -> None:
        async with self._locks[run_id]:
            await self._stop(run_id)
            async with self.sessions.begin() as session:
                await self._set(session, run_id, "paused", ("running", "awaiting_user"))

    async def cancel(self, run_id: uuid.UUID) -> None:
        async with self._locks[run_id]:
            await self._cancel(run_id)

    async def _cancel(self, run_id: uuid.UUID) -> None:
        await self._stop(run_id)
        async with self.sessions.begin() as session:
            await self._set(
                session, run_id, "cancelled", ("pending", "running", "awaiting_user", "paused"), ended_at=_now()
            )

    async def branch(self, run_id: uuid.UUID, checkpoint_id: str) -> Run:
        async with self._locks[run_id]:
            return await self._branch(run_id, checkpoint_id)

    async def _branch(self, run_id: uuid.UUID, checkpoint_id: str) -> Run:
        if run_id in self._tasks:
            raise RunStateError("pause the run before branching it")
        async with self.sessions.begin() as session:
            parent = await session.get_one(Run, run_id)
            if parent.status not in (*TERMINAL, "paused"):
                raise RunStateError(f"run {run_id} is {parent.status}; only a paused or finished run can be branched")
            child = Run(
                id=uuid.uuid4(),
                project_id=parent.project_id,
                thread_id=parent.thread_id,
                effort=parent.effort,
                status="running",
                budget_usd=parent.budget_usd,
                spent_usd=Decimal(0),
                checkpoint_thread_id=parent.checkpoint_thread_id,
                parent_run_id=parent.id,
                branch_from_checkpoint=checkpoint_id,
                started_at=_now(),
            )
            session.add(child)
        self._spawn(child.id, None, checkpoint_id)
        return child

    async def resume_running(self) -> None:
        """At startup: runs a stopped app left `running` continue from their last checkpoint."""
        async with self.sessions() as session:
            runs = (await session.scalars(select(Run).where(Run.status == "running"))).all()
            superseded = {r.id for r in runs if await self._superseded(session, r)}
        for run in runs:
            if run.id in superseded:  # its thread's latest checkpoint belongs to a newer branch
                async with self.sessions.begin() as session:
                    await self._set(session, run.id, "paused", ("running",))
            else:
                self._spawn(run.id, None)

    async def wait(self, run_id: uuid.UUID) -> None:
        task = self._tasks.get(run_id)
        if task is not None:
            await asyncio.wait({task})  # never raises: _drive records failures on the run

    async def shutdown(self) -> None:
        """App stop: cancel the tasks and leave their runs `running`, so the next start resumes them."""
        for run_id in list(self._tasks):
            await self._stop(run_id)

    def _spawn(
        self, run_id: uuid.UUID, decisions: list[dict[str, Any]] | None, checkpoint_id: str | None = None
    ) -> None:
        task = asyncio.create_task(self._drive(run_id, decisions, checkpoint_id))
        self._tasks[run_id] = task

        def forget(done: asyncio.Task[None]) -> None:
            if self._tasks.get(run_id) is done:
                del self._tasks[run_id]

        task.add_done_callback(forget)

    async def _stop(self, run_id: uuid.UUID) -> None:
        task = self._tasks.get(run_id)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _drive(
        self, run_id: uuid.UUID, decisions: list[dict[str, Any]] | None, checkpoint_id: str | None
    ) -> None:
        log = RunEventLog(self.sessions, self.hub, run_id)
        thread, agui_run = str(run_id), str(uuid.uuid4())  # one AG-UI run per drive
        try:
            async with self.sessions() as session:
                run = await session.get_one(Run, run_id)
                project = await session.get_one(Project, run.project_id)
            thread = run.checkpoint_thread_id  # the AG-UI thread is LangGraph's thread; branches share it
            graph = await self.build(run)
            if checkpoint_id is None and run.branch_from_checkpoint is not None and run.started_at is not None:
                latest = await graph.aget_state({"configurable": {"thread_id": thread}})
                if latest.created_at is None or datetime.fromisoformat(latest.created_at) < run.started_at:
                    checkpoint_id = run.branch_from_checkpoint  # stopped before the branch's first checkpoint
            configurable = {"thread_id": thread}
            if checkpoint_id is not None:
                configurable["checkpoint_id"] = checkpoint_id
            state = await graph.aget_state({"configurable": configurable})
            ctx = RunContext(
                project_id=run.project_id,
                run_id=run.id,
                effort=cast(EffortName, run.effort),
                budget_usd=run.budget_usd,
                domains=frozenset(project.settings.get("domains", [])),
            )
            messages: list[Any] = []
            resume: list[ResumeEntry] | None = None
            if decisions and state.interrupts:
                payload = {"decisions": decisions}
                resume = [ResumeEntry(interrupt_id=state.interrupts[0].id, status="resolved", payload=payload)]
            elif decisions:  # the review was answered but the graph is not waiting on it: never drop the answer
                raise RunStateError(f"run {run_id} has no pending interrupt for these decisions")
            elif not state.values:  # a new run, or one stopped before its first checkpoint
                question = await self._question(run_id)
                messages = [UserMessage(id=str(question.id), content=str(question.content["text"]))]
            adapter = ResearchlyAgent(
                graph, ctx, continue_pending=resume is None and not messages, checkpoint_id=checkpoint_id
            )
            end: RunFinishedEvent | RunErrorEvent | None = None
            agui_input = RunAgentInput(thread_id=thread, run_id=agui_run, messages=messages, resume=resume)
            async with contextlib.aclosing(adapter.run(agui_input)) as events:
                async for event in events:
                    if isinstance(event, RunFinishedEvent | RunErrorEvent):
                        end = event  # held back: it commits together with the run's new status
                    else:
                        await log.append(event)
            if end is None:
                raise RuntimeError("the AG-UI run ended without a terminal event")
            await self._finish(run, graph, log, end)
        except asyncio.CancelledError:  # pause, cancel or app stop: close the open AG-UI run for subscribers
            if log.open:  # best effort: a failed write must not strand the run or abort shutdown
                cancelled = RunFinishedEvent(thread_id=thread, run_id=agui_run, outcome=RunFinishedCancelledOutcome())
                try:
                    await log.append(cancelled)
                except Exception as exc:
                    logger.warning("run %s: could not close its AG-UI run: %s", run_id, type(exc).__name__)
            raise
        except Exception as exc:  # the type only: messages can carry provider responses
            name = type(exc).__name__
            logger.warning("run %s failed: %s", run_id, name)
            if not log.open:
                await log.append(RunStartedEvent(thread_id=thread, run_id=agui_run))
            await log.append(RunErrorEvent(message=name, code=name), run_update=self._failed(run_id))

    async def _finish(self, run: Run, graph: Any, log: RunEventLog, end: RunFinishedEvent | RunErrorEvent) -> None:
        spent = await spent_usd(self.sessions, run.id)
        text: str | None = None
        if isinstance(end, RunErrorEvent):
            status = "failed"
        elif isinstance(end.outcome, RunFinishedInterruptOutcome):
            status = "awaiting_user"
        else:
            state = await graph.aget_state({"configurable": {"thread_id": run.checkpoint_thread_id}})
            messages = state.values["messages"]
            text = messages[-1].text
            limited = any(isinstance(m, ToolMessage) and m.text == LIMIT_HIT for m in messages)
            status = "partial" if text in STOPPED or limited or spent >= run.budget_usd else "completed"

        async def settle(session: AsyncSession) -> None:
            ended = {} if status == "awaiting_user" else {"ended_at": _now()}
            await self._set(session, run.id, status, ("running",), spent_usd=spent, **ended)
            if text is not None:
                session.add(Message(thread_id=run.thread_id, role="assistant", content={"text": text}, run_id=run.id))

        await log.append(end, run_update=settle)

    @staticmethod
    def _failed(run_id: uuid.UUID) -> RunUpdate:
        async def fail(session: AsyncSession) -> None:
            running = (Run.id == run_id, Run.status == "running")  # a pause or cancel in flight wins
            await session.execute(update(Run).where(*running).values(status="failed", ended_at=_now()))

        return fail

    async def _question(self, run_id: uuid.UUID) -> Message:
        async with self.sessions() as session:
            stmt = select(Message).where(Message.run_id == run_id, Message.role == "user")
            message = await session.scalar(stmt.order_by(Message.created_at).limit(1))
        if message is None:
            raise RunStateError(f"run {run_id} has no question and no checkpoint")
        return message

    @staticmethod
    async def _superseded(session: AsyncSession, run: Run) -> bool:
        if run.started_at is None:
            return False
        newer = select(func.count()).where(
            Run.checkpoint_thread_id == run.checkpoint_thread_id, Run.id != run.id, Run.started_at > run.started_at
        )
        return bool(await session.scalar(newer))

    @staticmethod
    async def _set(
        session: AsyncSession, run_id: uuid.UUID, status: str, only_from: Iterable[str], **values: Any
    ) -> None:
        stmt = (
            update(Run)
            .where(Run.id == run_id, Run.status.in_(list(only_from)))
            .values(status=status, **values)
            .returning(Run.id)
        )
        if await session.scalar(stmt) is None:
            current = await session.scalar(select(Run.status).where(Run.id == run_id))
            raise RunStateError(f"run {run_id} is {current or 'missing'}; it cannot become {status}")
