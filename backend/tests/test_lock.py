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
