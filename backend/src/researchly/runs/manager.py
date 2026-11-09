"""Runs as in-process asyncio tasks over a checkpointed graph (spec §6.5, §16).

The database row is the source of truth for a run's status; LangGraph's checkpoints hold its progress. Every status
change is a conditional UPDATE, so two callers can never both move a run.
"""

import asyncio
import contextlib
import json
import logging
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.types import Command
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from researchly.agents.context import EFFORTS, EffortName, RunContext
from researchly.agents.middleware import BUDGET_SPENT, LOOP_STOP
from researchly.db.models import Message, Project, Run, RunEvent
from researchly.llm.ledger import spent_usd

logger = logging.getLogger(__name__)

TERMINAL = ("partial", "completed", "cancelled", "failed")
RUN_ERROR = "RUN_ERROR"
INTERRUPT = "INTERRUPT"
STOPPED = (BUDGET_SPENT, LOOP_STOP)
LIMIT_HIT = "Tool call limit exceeded. Do not make additional tool calls."  # langchain ToolCallLimitMiddleware


class RunStateError(Exception):
    """The run's status does not allow this action."""


def _now() -> datetime:
    return datetime.now(UTC)


class RunManager:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], build: Callable[[Run], Awaitable[Any]]) -> None:
        self.sessions, self.build = sessions, build
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
        payload: Command | None = None
        if decisions:  # a run paused while awaiting the user still has its interrupt pending
            if run.status not in ("paused", "awaiting_user"):
                raise RunStateError(f"run {run_id} is {run.status}; decisions are for a paused or waiting run")
            if any(d.get("type") == "reject" for d in decisions):
                await self._cancel(run_id)
                return
            payload = Command(resume={"decisions": decisions})
        elif run.status == "awaiting_user":
            raise RunStateError("this run is waiting for the user's decisions")
        async with self.sessions.begin() as session:
            await self._set(session, run_id, "running", ("paused", "awaiting_user"))
        self._spawn(run_id, payload)

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

    def _spawn(self, run_id: uuid.UUID, payload: Any, checkpoint_id: str | None = None) -> None:
        task = asyncio.create_task(self._drive(run_id, payload, checkpoint_id))
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

    async def _drive(self, run_id: uuid.UUID, payload: Any, checkpoint_id: str | None) -> None:
        try:
            async with self.sessions() as session:
                run = await session.get_one(Run, run_id)
                project = await session.get_one(Project, run.project_id)
            graph = await self.build(run)
            configurable = {"thread_id": run.checkpoint_thread_id}
            if checkpoint_id is None and run.branch_from_checkpoint is not None and run.started_at is not None:
                latest = await graph.aget_state({"configurable": dict(configurable)})
                if latest.created_at is None or datetime.fromisoformat(latest.created_at) < run.started_at:
                    checkpoint_id = run.branch_from_checkpoint  # stopped before the branch's first checkpoint
            if checkpoint_id is not None:
                configurable["checkpoint_id"] = checkpoint_id
            config = {"configurable": configurable}
            ctx = RunContext(
                project_id=run.project_id,
                run_id=run.id,
                effort=cast(EffortName, run.effort),
                budget_usd=run.budget_usd,
                domains=frozenset(project.settings.get("domains", [])),
            )
            if payload is None and not (await graph.aget_state(config)).values:
                payload = {"messages": [HumanMessage(await self._question(run_id))]}  # stopped before a checkpoint
            out = await graph.ainvoke(payload, config, context=ctx)
            await self._finish(run, out)
        except Exception as exc:  # the type only: messages can carry provider responses
            logger.warning("run %s failed: %s", run_id, type(exc).__name__)
            async with self.sessions.begin() as session:
                await self._event(session, run_id, RUN_ERROR, {"error": type(exc).__name__})
                running = (Run.id == run_id, Run.status == "running")  # a pause or cancel in flight wins
                await session.execute(update(Run).where(*running).values(status="failed", ended_at=_now()))

    async def _finish(self, run: Run, out: dict[str, Any]) -> None:
        if interrupts := out.get("__interrupt__"):
            values = json.loads(json.dumps([i.value for i in interrupts], default=str))
            async with self.sessions.begin() as session:
                await self._set(session, run.id, "awaiting_user", ("running",))
                await self._event(session, run.id, INTERRUPT, {"interrupts": values})
            return
        text = out["messages"][-1].text
        spent = await spent_usd(self.sessions, run.id)
        limited = any(isinstance(m, ToolMessage) and m.text == LIMIT_HIT for m in out["messages"])
        status = "partial" if text in STOPPED or limited or spent >= run.budget_usd else "completed"
        async with self.sessions.begin() as session:
            await self._set(session, run.id, status, ("running",), spent_usd=spent, ended_at=_now())
            session.add(Message(thread_id=run.thread_id, role="assistant", content={"text": text}, run_id=run.id))

    async def _question(self, run_id: uuid.UUID) -> str:
        async with self.sessions() as session:
            stmt = select(Message.content).where(Message.run_id == run_id, Message.role == "user")
            content = await session.scalar(stmt.order_by(Message.created_at).limit(1))
        if content is None:
            raise RunStateError(f"run {run_id} has no question and no checkpoint")
        return str(content["text"])

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

    @staticmethod
    async def _event(session: AsyncSession, run_id: uuid.UUID, kind: str, payload: dict[str, Any]) -> None:
        last = await session.scalar(select(func.max(RunEvent.seq)).where(RunEvent.run_id == run_id))
        session.add(RunEvent(run_id=run_id, seq=(last or 0) + 1, type=kind, payload=payload))
