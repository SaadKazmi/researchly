from pathlib import Path

import pytest

from researchly.sandbox.light import run_cell


def test_comma_in_a_path_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="comma"):
        run_cell("pass", tmp_path / "a,b", tmp_path)
