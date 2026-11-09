"""What every agent and tool of one run shares (spec §6.1, §6.4–6.5). Never holds keys: it reaches tool runtimes."""

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

EffortName = Literal["quick", "standard", "deep"]


@dataclass(frozen=True)
class Effort:
    name: EffortName
    scouts: int  # scout tasks per run
    readers: int  # reader tasks per run
    model_calls: int  # per subagent invocation
    tool_calls: int  # per subagent invocation; per run for Quick
    budget_usd: Decimal  # default budget when the user sets none


EFFORTS: dict[str, Effort] = {
    "quick": Effort("quick", scouts=0, readers=0, model_calls=0, tool_calls=10, budget_usd=Decimal("0.10")),
    "standard": Effort("standard", scouts=4, readers=4, model_calls=15, tool_calls=25, budget_usd=Decimal("1")),
    "deep": Effort("deep", scouts=8, readers=12, model_calls=25, tool_calls=40, budget_usd=Decimal("5")),
}
TIMEOUTS_S = {"scout": 300.0, "reader": 480.0}


@dataclass(frozen=True)
class RunContext:
    project_id: uuid.UUID
    run_id: uuid.UUID
    effort: EffortName = "standard"
    budget_usd: Decimal = Decimal(1)
    domains: frozenset[str] = frozenset()


class SubagentResult(BaseModel):
    """What a scout or reader hands back to the Lead (spec §6.1): a short summary and file paths, never paper text."""

    status: Literal["complete", "partial", "failed"]
    summary: str = Field(max_length=6000, description="What was done and found, in at most a few paragraphs")
    files: list[str] = Field(
        default_factory=list, description="Project files written, e.g. /projects/candidates/sq1.jsonl"
    )
    gaps: list[str] = Field(default_factory=list, description="What could not be found or read, and why")
