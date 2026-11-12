"""Projects (spec §17, F1)."""

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from researchly.api.deps import Manager, get_or_404
from researchly.db.models import Project

router = APIRouter(prefix="/api/v1", tags=["projects"])


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class ProjectRename(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    description: str | None
    created_at: datetime


@router.post("/projects", status_code=201, operation_id="createProject")
async def create_project(body: ProjectIn, runs: Manager) -> ProjectOut:
    async with runs.sessions.begin() as session:
        project = Project(name=body.name, description=body.description)
        session.add(project)
        await session.flush()
        await session.refresh(project)  # created_at comes from the database
        return ProjectOut.model_validate(project)


@router.get("/projects", operation_id="listProjects")
async def list_projects(runs: Manager) -> list[ProjectOut]:
    async with runs.sessions() as session:
        rows = await session.scalars(select(Project).order_by(Project.created_at.desc(), Project.id.desc()))
        return [ProjectOut.model_validate(p) for p in rows]


@router.get("/projects/{project_id}", operation_id="getProject")
async def get_project(project_id: uuid.UUID, runs: Manager) -> ProjectOut:
    return ProjectOut.model_validate(await get_or_404(runs, Project, project_id))


@router.patch("/projects/{project_id}", operation_id="renameProject")
async def rename_project(project_id: uuid.UUID, body: ProjectRename, runs: Manager) -> ProjectOut:
    async with runs.sessions.begin() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise HTTPException(404, f"project {project_id} not found")
        project.name = body.name
        return ProjectOut.model_validate(project)
