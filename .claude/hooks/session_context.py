"""SessionStart hook (compact|resume): re-inject progress tail, current plan and git status."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import project_root  # noqa: E402

PROGRESS_TAIL_LINES = 60


def build_context(root: Path, git_status: str, limit: int = 10_000) -> str:
    parts = []
    progress = root / "docs" / "progress.md"
    if progress.exists():
        tail = progress.read_text(encoding="utf-8").splitlines()[-PROGRESS_TAIL_LINES:]
        parts.append("## docs/progress.md (tail)\n" + "\n".join(tail))
    else:
        parts.append("## docs/progress.md\n(no progress log yet)")
    plans = sorted((root / "docs" / "superpowers" / "plans").glob("*.md")) if (
        root / "docs" / "superpowers" / "plans"
    ).exists() else []
    if plans:
        parts.append("## Latest plan\n" + plans[-1].relative_to(root).as_posix())
    parts.append("## git status --short\n" + (git_status.strip() or "(clean)"))
    return "\n\n".join(parts)[-limit:]


def main() -> int:
    root = project_root()
    status = subprocess.run(
        ["git", "status", "--short"], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    ).stdout
    print(
        json.dumps(
            {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": build_context(root, status)}}
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
