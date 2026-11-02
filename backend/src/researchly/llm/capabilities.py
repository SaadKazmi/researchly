"""Capability table (spec §7.2): role defaults and per-model overrides. No litellm import here."""

from pathlib import Path

import yaml
from pydantic import BaseModel

_DEFAULT = Path(__file__).with_name("capabilities.yaml")


class ModelOverride(BaseModel):
    input_per_mtok: float | None = None
    cached_per_mtok: float | None = None
    output_per_mtok: float | None = None
    context_window: int | None = None
    max_output: int | None = None
    tools: bool | None = None
    json_schema: bool | None = None
    vision: bool | None = None
    prompt_cache: bool | None = None


class CapabilityTable(BaseModel):
    roles: dict[str, dict[str, str]]
    models: dict[str, ModelOverride] = {}


def load_table(path: Path | None = None) -> CapabilityTable:
    return CapabilityTable.model_validate(yaml.safe_load((path or _DEFAULT).read_text(encoding="utf-8")))
