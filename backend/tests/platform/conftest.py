"""Platform tests download real models and runtimes; CI runs them on all five OS (`pytest -m platform`)."""

import os
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def models_dir() -> Path:
    # CI caches this dir between runs; locally it is the gitignored backend/.models
    return Path(os.environ.get("RESEARCHLY_TEST_MODELS_DIR", Path(__file__).resolve().parents[2] / ".models"))


def report(label: str, seconds: float) -> None:
    """Timings for the ADRs: a row in the GitHub job summary, or stdout locally."""
    line = f"| {label} | {seconds:.2f} s |"
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    print(line)
