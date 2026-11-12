"""Runs (spec §17): create in a thread, read, pause, resume with decisions, cancel, branch."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from researchly.agents.context import EffortName
from researchly.api.deps import CONFLICT, NOT_FOUND, UNAVAILABLE, Manager, get_or_404
from researchly.db.models import Run, Thread

router = APIRouter(prefix="/api/v1", tags=["runs"], responses=UNAVAILABLE)


class RunIn(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    effort: EffortName = "standard"


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
