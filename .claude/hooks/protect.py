"""PreToolUse hook: ask/deny rules for sensitive files.

- Harness control plane (.claude/settings*.json, hooks/, agents/, skills/, statusline, .mcp.json): ask.
- .github/workflows/*: ask (CI changes need the user's eye).
- Existing Alembic revisions: ask (add a new revision instead).
- docs/features.json: deny invalid content; ask for anything beyond flipping `passes`.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from _common import project_root, read_payload, rel_path  # noqa: E402
from repo_checks import check_features  # noqa: E402

_CONTROL_PLANE = {".claude/settings.json", ".claude/settings.local.json", ".claude/statusline.py", ".mcp.json"}
_CONTROL_PLANE_DIRS = (".claude/hooks/", ".claude/agents/", ".claude/skills/")
_MIGRATION_RE = re.compile(r"^backend/src/researchly/db/migrations/versions/.+\.py$")


def proposed_content(tool_name: str, tool_input: dict, path: Path) -> str:
    if tool_name == "Write":
        return tool_input.get("content", "")
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    for edit in tool_input.get("edits") or [tool_input]:
        old, new = edit.get("old_string", ""), edit.get("new_string", "")
        text = text.replace(old, new) if edit.get("replace_all") else text.replace(old, new, 1)
    return text


def _skeleton(data: dict) -> dict:
    stripped = [{k: v for k, v in f.items() if k != "passes"} for f in data.get("features", [])]
    return {**{k: v for k, v in data.items() if k != "features"}, "features": stripped}


def _check_features_change(payload: dict, path: Path) -> tuple[str, str] | None:
    new_text = proposed_content(payload.get("tool_name", ""), payload.get("tool_input", {}), path)
    try:
        new = json.loads(new_text)
    except json.JSONDecodeError as exc:
        return ("deny", f"docs/features.json would become invalid JSON: {exc}")
    if not isinstance(new, dict):
        return ("deny", "docs/features.json must be a JSON object")
    errors = check_features(new)
    if errors:
        return ("deny", "docs/features.json invalid: " + "; ".join(errors[:5]))
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return ("ask", "existing docs/features.json is unreadable; confirm this edit manually.")
        if not isinstance(old, dict) or _skeleton(old) != _skeleton(new):
            return ("ask", "docs/features.json structure change: only `passes` may change without user approval.")
    return None


def decide(payload: dict, root: Path) -> tuple[str, str] | None:
    file_path = payload.get("tool_input", {}).get("file_path", "")
    rel = rel_path(file_path, root)
    if rel is None:
        return None
    key = rel.casefold()  # NTFS is case-insensitive; resolve() only fixes case of segments that exist
    if key in _CONTROL_PLANE or key.startswith(_CONTROL_PLANE_DIRS):
        return ("ask", "Harness control-plane file (executed by Claude Code): confirm this edit with the user.")
    if key.startswith(".github/workflows/"):
        return ("ask", "CI workflow change: confirm with the user before editing.")
    if _MIGRATION_RE.match(key) and (root / rel).exists():
        return ("ask", "Editing an existing Alembic revision; add a new revision unless the user confirms.")
    if key == "docs/features.json":
        return _check_features_change(payload, root / rel)
    return None


def main() -> int:
    try:
        result = decide(read_payload(), project_root())
    except Exception as exc:
        # A broken guard must fail closed to user approval, not silently allow the edit.
        result = ("ask", f"protect hook error ({type(exc).__name__}: {exc}); confirm this edit manually.")
    if result:
        decision, reason = result
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": decision,
                        "permissionDecisionReason": reason,
                    }
                }
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
