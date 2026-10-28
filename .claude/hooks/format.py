"""PostToolUse hook: format the edited file (ruff for backend Python, prettier for frontend)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import project_root, read_payload, rel_path  # noqa: E402

_FRONTEND_EXT = (".ts", ".tsx", ".css", ".json")


def commands_for(rel: str, root: Path) -> list[list[str]]:
    if rel.startswith("backend/") and rel.endswith(".py") and (root / "backend/pyproject.toml").exists():
        return [
            ["uv", "run", "--directory", "backend", "ruff", "format", rel.removeprefix("backend/")],
            ["uv", "run", "--directory", "backend", "ruff", "check", "--fix", rel.removeprefix("backend/")],
        ]
    if rel.startswith("frontend/") and rel.endswith(_FRONTEND_EXT) and (root / "frontend/package.json").exists():
        return [["pnpm", "-C", "frontend", "exec", "prettier", "--write", rel.removeprefix("frontend/")]]
    return []


def main() -> int:
    root = project_root()
    rel = rel_path(read_payload().get("tool_input", {}).get("file_path", ""), root)
    for cmd in commands_for(rel, root) if rel else []:
        exe = shutil.which(cmd[0]) or cmd[0]
        subprocess.run([exe, *cmd[1:]], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
