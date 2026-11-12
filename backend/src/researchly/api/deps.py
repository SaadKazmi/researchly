"""Shared dependencies of the /api/v1 routers: the run manager (and through it, the database) and 404 lookups."""

import uuid
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel

from researchly.db.base import Base
from researchly.runs.manager import RunManager


class ErrorOut(BaseModel):
    detail: str


# Declared on the routers so the OpenAPI schema (and the generated client) types the error bodies.
# UNAVAILABLE goes on each router (every route needs the run manager); NOT_FOUND only on routes that look a row up.
UNAVAILABLE: dict[int | str, dict[str, Any]] = {503: {"model": ErrorOut}}
NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"model": ErrorOut}}
CONFLICT: dict[int | str, dict[str, Any]] = {409: {"model": ErrorOut}}  # RunStateError, see app.py


def run_manager(request: Request) -> RunManager:
    runs = getattr(request.app.state, "runs", None)
    if runs is None:  # the lifespan has not started it (unit-test apps have none)
        raise HTTPException(503, "the run manager is not running")
    return runs


Manager = Annotated[RunManager, Depends(run_manager)]


async def get_or_404[T: Base](runs: RunManager, model: type[T], row_id: uuid.UUID) -> T:
    async with runs.sessions() as session:
        row = await session.get(model, row_id)
    if row is None:
        raise HTTPException(404, f"{model.__name__.lower()} {row_id} not found")
    return row
