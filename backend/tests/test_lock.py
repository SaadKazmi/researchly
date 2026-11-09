"""Spec §26: torch/CUDA must never enter the native locks (Docling and fastembed run on onnxruntime)."""

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN = re.compile(r"^(torch|torchvision|torchaudio|triton|nvidia-.+|onnxruntime-gpu|tensorflow.*)$")


def forbidden(lock_text: str) -> list[str]:
    return sorted({p["name"] for p in tomllib.loads(lock_text)["package"] if FORBIDDEN.match(p["name"])})


def test_the_guard_catches_torch() -> None:
    lock = '[[package]]\nname = "torch"\n[[package]]\nname = "nvidia-cudnn-cu12"\n[[package]]\nname = "numpy"\n'
    assert forbidden(lock) == ["nvidia-cudnn-cu12", "torch"]


@pytest.mark.parametrize("lock", ["backend/uv.lock", "cli/uv.lock"])
def test_native_locks_have_no_torch(lock: str) -> None:
    assert forbidden((ROOT / lock).read_text("utf-8")) == []


# ADR 0011: the agent runtime is pinned exactly; an upgrade is an ADR plus the full suite (spec §6.6, §19.7)
PINS = {
    "deepagents": "0.7.32",
    "langchain": "1.4.7",
    "langchain-core": "1.7.3",
    "langchain-litellm": "0.11.1",
    "langgraph": "1.2.19",
    "langgraph-checkpoint-postgres": "3.2.0",
    "litellm": "1.105.1",
}


@pytest.mark.parametrize("lock", ["backend/uv.lock", "cli/uv.lock"])
def test_agent_runtime_pins(lock: str) -> None:
    versions = {p["name"]: p.get("version") for p in tomllib.loads((ROOT / lock).read_text("utf-8"))["package"]}
    assert {name: versions.get(name) for name in PINS} == PINS
