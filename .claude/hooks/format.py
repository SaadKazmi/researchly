"""PostToolUse hook: format the edited file (ruff for backend Python, prettier for frontend)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import locate, project_root, read_payload  # noqa: E402

_FRONTEND_EXT = (".ts", ".tsx", ".css", ".json")


def commands_for(rel: str, root: Path) -> list[list[str]]:
    for pkg in ("backend", "cli"):
        if rel.startswith(f"{pkg}/") and rel.endswith(".py") and (root / pkg / "pyproject.toml").exists():
            return [
                ["uv", "run", "--directory", pkg, "ruff", "format", rel.removeprefix(f"{pkg}/")],
                ["uv", "run", "--directory", pkg, "ruff", "check", "--fix", rel.removeprefix(f"{pkg}/")],
            ]
    for pkg in ("frontend", "e2e"):
        if rel.startswith(f"{pkg}/") and rel.endswith(_FRONTEND_EXT) and (root / pkg / "package.json").exists():
            return [["pnpm", "-C", pkg, "exec", "prettier", "--write", rel.removeprefix(f"{pkg}/")]]
    return []


def main() -> int:
    try:
        located = locate(read_payload().get("tool_input", {}).get("file_path", ""), project_root())
        if located:
            root, rel = located
            for cmd in commands_for(rel, root):
                exe = shutil.which(cmd[0]) or cmd[0]
                subprocess.run([exe, *cmd[1:]], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, timeout=60)
    except Exception:  # formatting is best-effort; never block an edit
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
