"""Runs (spec §17): create in a thread, read, pause, resume with decisions, cancel, branch, and the AG-UI stream."""

import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.sse import EventSourceResponse, ServerSentEvent
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, ConfigDict, Field

from researchly.agents.context import EFFORTS, EffortName
from researchly.api.deps import CONFLICT, NOT_FOUND, UNAVAILABLE, ErrorOut, Manager, get_or_404
from researchly.db.models import Run, Thread
from researchly.runs.events import follow

router = APIRouter(prefix="/api/v1", tags=["runs"], responses=UNAVAILABLE)

# FastAPI files an additional response's `model` under the route's own media type, so the SSE route below would
# declare its errors as `text/event-stream`. Its error bodies are the same JSON as everywhere else: say so.
_JSON = {"content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{ErrorOut.__name__}"}}}}
SSE_ERRORS: dict[int | str, dict[str, Any]] = {code: body | _JSON for code, body in (UNAVAILABLE | NOT_FOUND).items()}


class RunIn(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    effort: EffortName = "standard"


CENT = Decimal("0.01")  # money rounds half-up, not Decimal's default half-even: 0.025 is 3 cents, not 2
LOW = Decimal("0.25")  # ponytail: the estimate is a flat share of the tier budget; use the ledger once runs exist


class EffortOut(BaseModel):
    """A tier as the UI offers it (spec §6.4): its limits, its budget and the pre-run estimate range (F1.7)."""

    name: EffortName
    scouts: int
    readers: int
    tool_calls: int
    budget_usd: Decimal
    estimate_low_usd: Decimal
    estimate_high_usd: Decimal


@router.get("/efforts", operation_id="listEfforts")
async def list_efforts() -> list[EffortOut]:
    return [
        EffortOut(
            name=effort.name,
            scouts=effort.scouts,
            readers=effort.readers,
            tool_calls=effort.tool_calls,
            budget_usd=effort.budget_usd.quantize(CENT, ROUND_HALF_UP),
            estimate_low_usd=(effort.budget_usd * LOW).quantize(CENT, ROUND_HALF_UP),
            estimate_high_usd=effort.budget_usd.quantize(CENT, ROUND_HALF_UP),
        )
        for effort in EFFORTS.values()
    ]


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    project_id: uuid.UUID
    thread_id: uuid.UUID
    parent_run_id: uuid.UUID | None
    effort: str
    status: str
    plan: dict[str, Any] | None
    budget_usd: Decimal  # a JSON string: no float rounding of money
    spent_usd: Decimal
    branch_from_checkpoint: str | None
    started_at: datetime | None
    ended_at: datetime | None


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a typo such as `mesage` is a 422, not a silently empty decision


class EditedAction(_Strict):
    name: str
    args: dict[str, Any]


class Approve(_Strict):
    type: Literal["approve"]


class Edit(_Strict):
    type: Literal["edit"]
    edited_action: EditedAction


class Reject(_Strict):
    type: Literal["reject"]
    message: str | None = None


class Respond(_Strict):
    type: Literal["respond"]
    message: str


Decision = Annotated[Approve | Edit | Reject | Respond, Field(discriminator="type")]


class ResumeIn(BaseModel):
    decisions: list[Decision] | None = None  # one per action of the open review, in order


class BranchIn(BaseModel):
    checkpoint_id: str = Field(min_length=1)


async def existing_run(run_id: uuid.UUID, runs: Manager) -> Run:
    return await get_or_404(runs, Run, run_id)


ExistingRun = Annotated[Run, Depends(existing_run)]


async def _out(runs: Manager, run_id: uuid.UUID) -> RunOut:
    return RunOut.model_validate(await get_or_404(runs, Run, run_id))


@router.post("/threads/{thread_id}/runs", status_code=201, operation_id="createRun", responses=NOT_FOUND)
async def create_run(thread_id: uuid.UUID, body: RunIn, runs: Manager) -> RunOut:
    thread = await get_or_404(runs, Thread, thread_id)
    # ponytail: no cap on concurrent runs; one local user. Add an app-wide cap when runs contend (spec §16)
    run = await runs.create(thread.project_id, thread.id, body.message, effort=body.effort)
    await runs.start(run.id)
    return await _out(runs, run.id)


@router.get("/runs/{run_id}", operation_id="getRun", responses=NOT_FOUND)
async def get_run(run: ExistingRun) -> RunOut:
    return RunOut.model_validate(run)


@router.post("/runs/{run_id}/pause", operation_id="pauseRun", responses=NOT_FOUND | CONFLICT)
async def pause_run(run: ExistingRun, runs: Manager) -> RunOut:
    await runs.pause(run.id)
    return await _out(runs, run.id)


@router.post("/runs/{run_id}/resume", operation_id="resumeRun", responses=NOT_FOUND | CONFLICT)
async def resume_run(run: ExistingRun, runs: Manager, body: ResumeIn | None = None) -> RunOut:
    decisions = [d.model_dump(exclude_none=True) for d in body.decisions] if body and body.decisions else None
    await runs.resume(run.id, decisions)
    return await _out(runs, run.id)


@router.post("/runs/{run_id}/cancel", operation_id="cancelRun", responses=NOT_FOUND | CONFLICT)
async def cancel_run(run: ExistingRun, runs: Manager) -> RunOut:
    await runs.cancel(run.id)
    return await _out(runs, run.id)


@router.post("/runs/{run_id}/branch", status_code=201, operation_id="branchRun", responses=NOT_FOUND | CONFLICT)
async def branch_run(run: ExistingRun, body: BranchIn, runs: Manager) -> RunOut:
    child = await runs.branch(run.id, body.checkpoint_id)
    return await _out(runs, child.id)


@router.post(
    "/runs/{run_id}/stream", operation_id="streamRun", responses=SSE_ERRORS, response_class=EventSourceResponse
)
async def stream_run(
    run: ExistingRun,
    runs: Manager,
    after: Annotated[int, Query(ge=0)] = 0,
    last_event_id: Annotated[int | None, Header(ge=0)] = None,
) -> AsyncIterator[ServerSentEvent]:
    """The run's AG-UI events as SSE (ADR 0012), from `Last-Event-ID` (or `?after=`) on. Any request body is ignored.

    A disconnect ends only this generator: the run goes on, and a reconnect replays from its last `seq`.
    """
    start = after if last_event_id is None else last_event_id
    async for event in follow(runs.sessions, runs.hub, run.id, start):
        yield ServerSentEvent(raw_data=json.dumps(event.payload), id=str(event.seq))


CHECKPOINT_PAGE = 20  # ponytail: the newest 20; paginate when a run outgrows one menu


class CheckpointOut(BaseModel):
    """A point a run can be branched from (F1.6): LangGraph's checkpoint id, when it was written and its step."""

    checkpoint_id: str
    created_at: str | None
    step: int | None


@router.get("/runs/{run_id}/checkpoints", operation_id="listCheckpoints", responses=NOT_FOUND)
async def list_checkpoints(run: ExistingRun, runs: Manager) -> list[CheckpointOut]:
    """The run's newest checkpoints, newest first.

    Read straight from the checkpoint saver, never through `runs.build`: building a run's graph needs a configured
    LLM provider and loads the local embedding models, and branching must stay readable without either.
    """
    saver = runs.checkpointer
    if saver is None:  # no lifespan ran, as in a unit-test app
        raise HTTPException(503, "the run manager is not running")
    # `checkpoint_ns` is pinned to the root graph. The saver filters on it only when the key is present, so
    # leaving it out lists every subagent subgraph's checkpoints as well - ids the branch endpoint, which
    # passes them back as root config, cannot resume from.
    config: RunnableConfig = {"configurable": {"thread_id": run.checkpoint_thread_id, "checkpoint_ns": ""}}
    out: list[CheckpointOut] = []
    # `limit` stops at one page in the saver, so the iterator is always drained and never left open mid-stream
    async for saved in saver.alist(config, limit=CHECKPOINT_PAGE):
        checkpoint_id = saved.config.get("configurable", {}).get("checkpoint_id")
        if checkpoint_id is None:
            continue
        out.append(
            CheckpointOut(
                checkpoint_id=checkpoint_id,
                created_at=saved.checkpoint.get("ts"),
                step=saved.metadata.get("step"),
            )
        )
    return out
