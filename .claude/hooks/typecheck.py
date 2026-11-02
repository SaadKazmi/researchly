"""Stop hook: typecheck packages with changed files; block the stop on errors."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import project_root, read_payload  # noqa: E402

MAX_REASON_LINES = 40


class CompletedLike(Protocol):
    returncode: int
    stdout: str
    stderr: str


def parse_porcelain(text: str) -> list[str]:
    """Parse `git status --porcelain=v1 -z` output: NUL-separated, a rename's source path in its own field."""
    paths: list[str] = []
    fields = iter(text.split("\0"))
    for entry in fields:
        if len(entry) < 4:
            continue
        paths.append(entry[3:])
        if entry[0] in "RC":
            next(fields, None)
    return paths


def checks_for(changed: list[str], root: Path) -> list[list[str]]:
    cmds: list[list[str]] = []
    if any(p.startswith("backend/") and p.endswith(".py") for p in changed) and (
        root / "backend/pyproject.toml"
    ).exists():
        cmds.append(["uv", "run", "--directory", "backend", "pyright"])
    if any(p.startswith("frontend/") and p.endswith((".ts", ".tsx")) for p in changed) and (
        root / "frontend/package.json"
    ).exists():
        cmds.append(["pnpm", "-C", "frontend", "exec", "tsc", "--noEmit"])
    return cmds


def decide(
    payload: dict, root: Path, changed: list[str], run: Callable[[list[str]], CompletedLike]
) -> dict | None:
    if payload.get("stop_hook_active"):
        return None
    for cmd in checks_for(changed, root):
        result = run(cmd)
        if result.returncode != 0:
            lines = (result.stdout + result.stderr).splitlines()[:MAX_REASON_LINES]
            return {"decision": "block", "reason": f"`{' '.join(cmd)}` failed:\n" + "\n".join(lines)}
    return None


def _run(root: Path) -> Callable[[list[str]], CompletedLike]:
    def run(cmd: list[str]) -> CompletedLike:
        exe = shutil.which(cmd[0]) or cmd[0]
        try:
            return subprocess.run([exe, *cmd[1:]], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, timeout=600)
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(cmd, 1, "", f"timed out after 600s: {' '.join(cmd)}")

    return run


def main() -> int:
    root = project_root()
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "-uall"], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, timeout=30
    ).stdout
    result = decide(read_payload(), root, parse_porcelain(status), _run(root))
    if result:
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
