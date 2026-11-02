import os
import subprocess
import sys
from pathlib import Path

import pytest

from researchly.sandbox import light
from researchly.sandbox.light import run_cell


def plant_dir_link(link: Path, target: Path) -> None:
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))  # unlike a symlink, needs no privilege
    else:
        link.symlink_to(target, target_is_directory=True)


def test_comma_in_a_path_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="comma"):
        run_cell("pass", tmp_path / "a,b", tmp_path)


def test_comma_in_the_resolved_path_is_rejected(tmp_path: Path) -> None:
    # Deno gets the resolved path, so a link to "a,b" must not slip past the check
    (tmp_path / "a,b").mkdir()
    plant_dir_link(tmp_path / "alias", tmp_path / "a,b")
    with pytest.raises(ValueError, match="comma"):
        run_cell("pass", tmp_path / "alias", tmp_path / "cache")


@pytest.mark.parametrize("kind", ["dir-link", "hardlink"])
def test_a_link_in_the_workdir_is_refused(tmp_path: Path, kind: str) -> None:
    work, outside = tmp_path / "work", tmp_path / "outside"
    (work / "sub").mkdir(parents=True)
    outside.mkdir()
    (outside / "secret.txt").write_text("decoy-secret", encoding="utf-8")
    if kind == "dir-link":
        plant_dir_link(work / "sub" / "j", outside)
    else:
        os.link(outside / "secret.txt", work / "sub" / "h")
    result = run_cell("print(1)", work, tmp_path / "cache")  # refused before Deno starts
    assert not result.ok
    assert "is a link" in (result.error or "")


def test_deno_ignores_config_files_and_a_forged_result_is_an_error(tmp_path: Path, monkeypatch) -> None:
    # a cell can print its own last line and exit, so the result line is untrusted
    seen = []

    def fake_run(args, **kwargs):
        seen.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="1\n", stderr="")

    monkeypatch.setattr(light.subprocess, "run", fake_run)
    result = run_cell("pass", tmp_path, tmp_path / "cache")
    assert not result.ok
    assert "--no-config" in seen[0]
