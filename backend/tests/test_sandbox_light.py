import os
import sys
from pathlib import Path

import pytest

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
    assert "is a link out of the workdir" in (result.error or "")
