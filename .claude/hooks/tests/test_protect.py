import io
import json
import sys
from pathlib import Path

import _common
import protect

MIGRATION_DIR = "backend/src/researchly/db/migrations/versions"


def features(passes=False, e2e=None, title="Projects"):
    return {
        "features": [
            {
                "id": "F1.1",
                "title": title,
                "build_step": 3,
                "spec_ref": "§3 F1.1",
                "acceptance": "a",
                "e2e": e2e if e2e is not None else [],
                "passes": passes,
            }
        ]
    }


def write(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def payload(tool: str, path: Path, **tool_input) -> dict:
    return {"tool_name": tool, "tool_input": {"file_path": str(path), **tool_input}}


def test_unrelated_file_allowed(tmp_path):
    assert protect.decide(payload("Write", tmp_path / "README.md", content="x"), tmp_path) is None


def test_outside_repo_allowed(tmp_path):
    outside = tmp_path.parent / "elsewhere.txt"
    assert protect.decide(payload("Write", outside, content="x"), tmp_path) is None


def test_workflow_edit_asks(tmp_path):
    decision = protect.decide(payload("Write", tmp_path / ".github/workflows/ci.yml", content="x"), tmp_path)
    assert decision is not None and decision[0] == "ask"


def test_new_migration_allowed(tmp_path):
    p = tmp_path / MIGRATION_DIR / "0002_new.py"
    assert protect.decide(payload("Write", p, content="x"), tmp_path) is None


def test_existing_migration_asks(tmp_path):
    p = write(tmp_path, f"{MIGRATION_DIR}/0001_base.py", "x")
    decision = protect.decide(payload("Edit", p, old_string="x", new_string="y"), tmp_path)
    assert decision is not None and decision[0] == "ask"


def test_features_passes_flip_allowed(tmp_path):
    spec = ["e2e/specs/F1.1.spec.ts"]
    p = write(tmp_path, "docs/features.json", json.dumps(features(e2e=spec)))
    new = json.dumps(features(passes=True, e2e=spec))
    assert protect.decide(payload("Write", p, content=new), tmp_path) is None


def test_features_edit_tool_passes_flip_allowed(tmp_path):
    spec = ["e2e/specs/F1.1.spec.ts"]
    p = write(tmp_path, "docs/features.json", json.dumps(features(e2e=spec)))
    decision = protect.decide(
        payload("Edit", p, old_string='"passes": false', new_string='"passes": true'), tmp_path
    )
    assert decision is None


def test_features_structure_change_asks(tmp_path):
    p = write(tmp_path, "docs/features.json", json.dumps(features()))
    new = json.dumps(features(title="Renamed"))
    decision = protect.decide(payload("Write", p, content=new), tmp_path)
    assert decision is not None and decision[0] == "ask"


def test_features_pass_without_e2e_denied(tmp_path):
    p = write(tmp_path, "docs/features.json", json.dumps(features()))
    new = json.dumps(features(passes=True))
    decision = protect.decide(payload("Write", p, content=new), tmp_path)
    assert decision is not None and decision[0] == "deny"
    assert "requires at least one e2e spec" in decision[1]


def test_features_invalid_json_denied(tmp_path):
    p = write(tmp_path, "docs/features.json", json.dumps(features()))
    decision = protect.decide(payload("Write", p, content="{not json"), tmp_path)
    assert decision is not None and decision[0] == "deny"


def test_multiedit_applies_all_edits(tmp_path):
    # Edit 1 is a passes flip (allowed alone). Edit 2 only matches after edit 1 has
    # run and adds a top-level key (structure change). Ask only if both are applied.
    spec = ["e2e/specs/F1.1.spec.ts"]
    p = write(tmp_path, "docs/features.json", json.dumps(features(e2e=spec)))
    decision = protect.decide(
        {
            "tool_name": "MultiEdit",
            "tool_input": {
                "file_path": str(p),
                "edits": [
                    {"old_string": '"passes": false', "new_string": '"passes": true'},
                    {"old_string": '"passes": true}]}', "new_string": '"passes": true}], "owner": "x"}'},
                ],
            },
        },
        tmp_path,
    )
    assert decision is not None and decision[0] == "ask"


def test_read_payload_decodes_utf8_regardless_of_locale(monkeypatch):
    raw = json.dumps({"x": "§"}, ensure_ascii=False).encode("utf-8")
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw), encoding="cp1252"))
    assert _common.read_payload()["x"] == "§"


def test_main_fails_closed_on_hook_error(monkeypatch, capsys):
    def boom(payload, root):
        raise RuntimeError("boom")

    monkeypatch.setattr(protect, "decide", boom)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b""), encoding="utf-8"))
    assert protect.main() == 0
    out = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert out["permissionDecision"] == "ask"
    assert "RuntimeError: boom" in out["permissionDecisionReason"]


def test_features_non_object_json_denied(tmp_path):
    p = write(tmp_path, "docs/features.json", json.dumps(features()))
    decision = protect.decide(payload("Write", p, content="[]"), tmp_path)
    assert decision is not None and decision[0] == "deny"
    assert "must be a JSON object" in decision[1]


def test_unreadable_existing_features_asks(tmp_path):
    p = write(tmp_path, "docs/features.json", "{bad")
    decision = protect.decide(payload("Write", p, content=json.dumps(features())), tmp_path)
    assert decision is not None and decision[0] == "ask"


def test_non_object_existing_features_asks(tmp_path):
    p = write(tmp_path, "docs/features.json", "[]")
    decision = protect.decide(payload("Write", p, content=json.dumps(features())), tmp_path)
    assert decision is not None and decision[0] == "ask"


CONTROL_PLANE_REASON = "Harness control-plane file (executed by Claude Code): confirm this edit with the user."


def test_control_plane_files_ask(tmp_path):
    for tool, rel in [
        ("Write", ".claude/settings.json"),
        ("Write", ".claude/settings.local.json"),
        ("Edit", ".claude/hooks/format.py"),
        ("Write", ".claude/hooks/tests/test_protect.py"),
        ("Write", ".claude/statusline.py"),
        ("Write", ".mcp.json"),
    ]:
        assert protect.decide(payload(tool, tmp_path / rel, content="x"), tmp_path) == ("ask", CONTROL_PLANE_REASON), rel


def test_agent_prompts_not_control_plane(tmp_path):
    assert protect.decide(payload("Write", tmp_path / ".claude/agents/architect.md", content="x"), tmp_path) is None
