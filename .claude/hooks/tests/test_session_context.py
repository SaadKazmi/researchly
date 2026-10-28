from pathlib import Path

import session_context


def test_context_includes_progress_plan_and_status(tmp_path: Path):
    (tmp_path / "docs/superpowers/plans").mkdir(parents=True)
    (tmp_path / "docs/progress.md").write_text("# Progress\n- did a thing\n", encoding="utf-8")
    older = tmp_path / "docs/superpowers/plans/2026-10-28-a.md"
    newer = tmp_path / "docs/superpowers/plans/2026-10-29-b.md"
    older.write_text("a", encoding="utf-8")
    newer.write_text("b", encoding="utf-8")
    text = session_context.build_context(tmp_path, " M AGENTS.md\n")
    assert "did a thing" in text
    assert "docs/superpowers/plans/2026-10-29-b.md" in text
    assert "M AGENTS.md" in text


def test_context_is_capped(tmp_path: Path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/progress.md").write_text("x" * 50_000, encoding="utf-8")
    assert len(session_context.build_context(tmp_path, "", limit=1000)) <= 1000


def test_context_without_files(tmp_path: Path):
    text = session_context.build_context(tmp_path, "")
    assert "no progress log" in text
