"""Helpers shared by Claude Code hook scripts."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def read_payload() -> dict:
    raw = sys.stdin.read()
    return json.loads(raw) if raw.strip() else {}


def project_root() -> Path:
    return Path(os.environ.get("CLAUDE_PROJECT_DIR", Path(__file__).resolve().parents[2])).resolve()


def rel_path(file_path: str, root: Path) -> str | None:
    if not file_path:
        return None
    try:
        return Path(file_path).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None
