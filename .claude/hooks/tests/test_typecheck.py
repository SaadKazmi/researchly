from dataclasses import dataclass
from pathlib import Path

import typecheck


@dataclass
class Result:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def make(root: Path, rel: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("", encoding="utf-8")


def test_parse_porcelain_handles_renames_and_untracked():
    text = " M backend/a.py\n?? frontend/src/b.tsx\nR  old.py -> backend/new.py\n"
    assert typecheck.parse_porcelain(text) == ["backend/a.py", "frontend/src/b.tsx", "backend/new.py"]


def test_checks_for_backend_and_frontend(tmp_path):
    make(tmp_path, "backend/pyproject.toml")
    make(tmp_path, "frontend/package.json")
    cmds = typecheck.checks_for(["backend/src/x.py", "frontend/src/y.ts"], tmp_path)
    assert cmds == [
        ["uv", "run", "--directory", "backend", "pyright"],
        ["pnpm", "-C", "frontend", "exec", "tsc", "--noEmit"],
    ]


def test_checks_skip_missing_projects(tmp_path):
    assert typecheck.checks_for(["backend/src/x.py", "frontend/src/y.ts"], tmp_path) == []


def test_stop_hook_active_never_blocks(tmp_path):
    make(tmp_path, "backend/pyproject.toml")
    result = typecheck.decide(
        {"stop_hook_active": True}, tmp_path, ["backend/x.py"], run=lambda cmd: Result(1, "err")
    )
    assert result is None


def test_failure_blocks_with_truncated_reason(tmp_path):
    make(tmp_path, "backend/pyproject.toml")
    output = "\n".join(f"error {i}" for i in range(100))
    result = typecheck.decide({}, tmp_path, ["backend/x.py"], run=lambda cmd: Result(1, output))
    assert result is not None and result["decision"] == "block"
    assert "error 39" in result["reason"] and "error 40" not in result["reason"]


def test_success_does_not_block(tmp_path):
    make(tmp_path, "backend/pyproject.toml")
    assert typecheck.decide({}, tmp_path, ["backend/x.py"], run=lambda cmd: Result(0)) is None
