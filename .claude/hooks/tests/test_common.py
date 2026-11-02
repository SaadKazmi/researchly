from pathlib import Path

from _common import locate


def test_locate_plain_file(tmp_path):
    assert locate(str(tmp_path / "docs" / "x.md"), tmp_path) == (tmp_path, "docs/x.md")


def test_locate_strips_worktree_prefix(tmp_path):
    wt = tmp_path / ".claude" / "worktrees" / "agent-1"
    assert locate(str(wt / "docs" / "features.json"), tmp_path) == (wt, "docs/features.json")


def test_locate_worktree_dir_itself_and_outside(tmp_path):
    assert locate(str(tmp_path / ".claude" / "worktrees" / "agent-1"), tmp_path) is None
    assert locate(str(Path(tmp_path).parent / "elsewhere.txt"), tmp_path) is None
