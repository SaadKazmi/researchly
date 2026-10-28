"""Repository invariants enforced in CI and by Claude Code hooks.

Run: uv run --no-project --python 3.12 scripts/repo_checks.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

FEATURE_KEYS = frozenset({"id", "title", "build_step", "spec_ref", "acceptance", "e2e", "passes"})
ALLOWED_MODELS = ("haiku", "inherit", "opus", "sonnet")
INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md", "backend/AGENTS.md", "frontend/AGENTS.md")
_ID_RE = re.compile(r"^F\d+\.\d+$")


def check_features(data: dict) -> list[str]:
    features = data.get("features")
    if not isinstance(features, list) or not features:
        return ["features must be a non-empty list"]
    errors: list[str] = []
    seen: set[str] = set()
    for f in features:
        fid = f.get("id", "?") if isinstance(f, dict) else "?"
        if not isinstance(f, dict) or set(f) != FEATURE_KEYS:
            errors.append(f"{fid}: keys must be {sorted(FEATURE_KEYS)}")
            continue
        if not _ID_RE.match(fid):
            errors.append(f"{fid}: bad id")
        if fid in seen:
            errors.append(f"{fid}: duplicate id")
        seen.add(fid)
        if not isinstance(f["passes"], bool):
            errors.append(f"{fid}: passes must be a boolean")
        elif f["passes"] and not f["e2e"]:
            errors.append(f"{fid}: passes=true requires at least one e2e spec")
        if not isinstance(f["build_step"], int) or not 1 <= f["build_step"] <= 10:
            errors.append(f"{fid}: build_step must be 1..10")
        if not isinstance(f["e2e"], list):
            errors.append(f"{fid}: e2e must be a list")
    return errors


def parse_frontmatter(text: str) -> dict[str, str]:
    text = text.replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end == -1:
        return {}
    fields: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" in line and not line.startswith((" ", "\t", "-")):
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return fields


def check_agents(agents_dir: Path) -> list[str]:
    errors: list[str] = []
    for path in sorted(agents_dir.glob("*.md")):
        fm = parse_frontmatter(path.read_text(encoding="utf-8"))
        for key in ("name", "description", "model"):
            if not fm.get(key):
                errors.append(f"{path.name}: missing {key}")
        model = fm.get("model")
        if model and model not in ALLOWED_MODELS:
            errors.append(f"{path.name}: model must be one of {', '.join(ALLOWED_MODELS)}")
    return errors


def check_line_budget(path: Path, max_lines: int = 200) -> list[str]:
    count = len(path.read_text(encoding="utf-8").splitlines())
    if count > max_lines:
        return [f"{path.name}: {count} lines exceeds budget of {max_lines}"]
    return []


def main(root: Path | None = None) -> int:
    root = root or Path(__file__).resolve().parents[1]
    errors: list[str] = []
    features_path = root / "docs" / "features.json"
    if features_path.exists():
        errors += check_features(json.loads(features_path.read_text(encoding="utf-8")))
    else:
        errors.append("docs/features.json missing")
    agents_dir = root / ".claude" / "agents"
    if agents_dir.exists():
        errors += check_agents(agents_dir)
    for rel in INSTRUCTION_FILES:
        if (root / rel).exists():
            errors += check_line_budget(root / rel)
    for e in errors:
        print(e)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
