"""Threads and their messages (spec §17, F1)."""

import uuid
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from researchly.api.deps import NOT_FOUND, UNAVAILABLE, Manager, get_or_404
from researchly.db.models import Message, Project, Thread

router = APIRouter(prefix="/api/v1", tags=["threads"], responses=UNAVAILABLE)


class ThreadIn(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ThreadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    project_id: uuid.UUID
    title: str | None
    created_at: datetime


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    text: str
    run_id: uuid.UUID | None
    created_at: datetime


class ThreadDetail(ThreadOut):
    messages: list[MessageOut]


@router.post("/projects/{project_id}/threads", status_code=201, operation_id="createThread", responses=NOT_FOUND)
async def create_thread(project_id: uuid.UUID, body: ThreadIn, runs: Manager) -> ThreadOut:
    await get_or_404(runs, Project, project_id)
    async with runs.sessions.begin() as session:
        thread = Thread(project_id=project_id, title=body.title)
        session.add(thread)
        await session.flush()
        await session.refresh(thread)
        return ThreadOut.model_validate(thread)


@router.get("/projects/{project_id}/threads", operation_id="listThreads", responses=NOT_FOUND)
async def list_threads(project_id: uuid.UUID, runs: Manager) -> list[ThreadOut]:
    await get_or_404(runs, Project, project_id)
    stmt = select(Thread).where(Thread.project_id == project_id).order_by(Thread.created_at.desc(), Thread.id.desc())
    async with runs.sessions() as session:
        return [ThreadOut.model_validate(t) for t in await session.scalars(stmt)]


@router.get("/threads/{thread_id}", operation_id="getThread", responses=NOT_FOUND)
async def get_thread(thread_id: uuid.UUID, runs: Manager) -> ThreadDetail:
    thread = await get_or_404(runs, Thread, thread_id)
    stmt = select(Message).where(Message.thread_id == thread_id).order_by(Message.created_at, Message.id)
    async with runs.sessions() as session:
        messages = [
            MessageOut(
                id=m.id, role=m.role, text=str(m.content.get("text", "")), run_id=m.run_id, created_at=m.created_at
            )
            for m in await session.scalars(stmt)
        ]
    return ThreadDetail(**ThreadOut.model_validate(thread).model_dump(), messages=messages)
