"""System information (spec §17)."""

from importlib.metadata import version

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1")


class SystemInfo(BaseModel):
    version: str


@router.get("/system")
async def system() -> SystemInfo:
    return SystemInfo(version=version("researchly"))
