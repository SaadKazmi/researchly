from pathlib import Path

import format as fmt


def make(root: Path, rel: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("", encoding="utf-8")


def test_python_in_backend(tmp_path):
    make(tmp_path, "backend/pyproject.toml")
    cmds = fmt.commands_for("backend/src/researchly/app.py", tmp_path)
    assert cmds == [
        ["uv", "run", "--directory", "backend", "ruff", "format", "src/researchly/app.py"],
        ["uv", "run", "--directory", "backend", "ruff", "check", "--fix", "src/researchly/app.py"],
    ]


def test_python_without_backend_project_skipped(tmp_path):
    assert fmt.commands_for("backend/src/x.py", tmp_path) == []


def test_tsx_in_frontend(tmp_path):
    make(tmp_path, "frontend/package.json")
    assert fmt.commands_for("frontend/src/App.tsx", tmp_path) == [
        ["pnpm", "-C", "frontend", "exec", "prettier", "--write", "src/App.tsx"]
    ]


def test_ts_in_e2e(tmp_path):
    make(tmp_path, "e2e/package.json")
    assert fmt.commands_for("e2e/specs/smoke.spec.ts", tmp_path) == [
        ["pnpm", "-C", "e2e", "exec", "prettier", "--write", "specs/smoke.spec.ts"]
    ]


def test_other_files_skipped(tmp_path):
    make(tmp_path, "backend/pyproject.toml")
    assert fmt.commands_for("docs/progress.md", tmp_path) == []


def test_python_in_cli(tmp_path):
    make(tmp_path, "cli/pyproject.toml")
    assert fmt.commands_for("cli/src/researchly_cli/main.py", tmp_path) == [
        ["uv", "run", "--directory", "cli", "ruff", "format", "src/researchly_cli/main.py"],
        ["uv", "run", "--directory", "cli", "ruff", "check", "--fix", "src/researchly_cli/main.py"],
    ]
