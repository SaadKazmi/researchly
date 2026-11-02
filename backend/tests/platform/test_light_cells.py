"""Light code cells: Deno's permissions are the boundary, so every escape is attempted on every OS."""

import re
import sys
import time
from pathlib import Path

import pytest

from researchly.sandbox.light import CellResult, _deno, run_cell, warm_up

from ..test_sandbox_light import plant_dir_link
from .conftest import report

pytestmark = pytest.mark.platform


@pytest.fixture(scope="session")
def cache(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("sandbox-cache")
    start = time.perf_counter()
    warm_up(path)
    report("light cells: warm-up (Deno + Pyodide + numpy/pandas/matplotlib download)", time.perf_counter() - start)
    return path


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    path = tmp_path / "outside"
    path.mkdir()
    (path / "secret.txt").write_text("decoy-secret", encoding="utf-8")
    return path


def _p(path: Path) -> str:
    return path.resolve().as_posix()


def _host_js(js: str) -> str:
    """Cell code that evals `js` in the host (Deno) through Emscripten's exports and prints the result."""
    wrapped = f"try {{ String({js}) }} catch (e) {{ String(e) }}"
    return (
        "import ctypes\n"
        "f = ctypes.CDLL(None).emscripten_run_script_string\n"
        "f.restype = ctypes.c_char_p\n"
        f"print('JS->', f({wrapped!r}.encode()).decode())\n"
    )


def test_numpy_pandas_matplotlib_and_workdir_files(cache: Path, workdir: Path) -> None:
    code = (
        "import numpy, pandas, matplotlib\n"
        "matplotlib.use('agg')\n"
        "import matplotlib.pyplot as plt\n"
        "plt.plot(numpy.arange(5))\n"
        "plt.savefig('plot.png')\n"
        "print(pandas.DataFrame({'a': [1, 2]}).a.sum())\n"
    )
    start = time.perf_counter()
    result = run_cell(code, workdir, cache)
    report("light cells: warm cell (numpy+pandas+matplotlib)", time.perf_counter() - start)
    assert result.ok, result.error
    assert "3" in result.stdout
    assert (workdir / "plot.png").stat().st_size > 0


def test_network_is_denied(cache: Path, workdir: Path) -> None:
    result = run_cell('import js\nawait js.fetch("https://example.com")\n', workdir, cache)
    assert not result.ok
    assert "NotCapable" in (result.error or "")


def test_ctypes_system_cannot_spawn(cache: Path, workdir: Path, outside: Path) -> None:
    target = outside / "pwn.txt"
    cmd = f"echo pwned > {_p(target)}"
    code = (
        "import ctypes\n"
        "open('/work/reached', 'w').close()\n"  # a file, because a fatal Pyodide error loses buffered stdout
        f"print('rc', ctypes.CDLL(None).system({cmd.encode()!r}))\n"
    )
    result = run_cell(code, workdir, cache)
    assert (workdir / "reached").exists(), (result.stdout, result.error)  # the cell got as far as system()
    assert not target.exists()
    text = result.stdout + (result.error or "")
    if sys.platform == "win32":  # Pyodide routes system() to Deno's spawnSync, which needs env COMSPEC
        assert "NotCapable" in text
    else:  # wasm system() returns non-zero, or Deno refuses the spawn
        assert "NotCapable" in text or re.search(r"^rc -?[1-9]", result.stdout, re.M), text


def test_deno_command_is_denied(cache: Path, workdir: Path) -> None:
    # absolute path, so the run permission check is what refuses it, not a PATH lookup
    deno = Path(_deno()).as_posix()
    result = run_cell(_host_js(f"new Deno.Command('{deno}', {{args: ['--version']}}).outputSync()"), workdir, cache)
    assert "NotCapable" in result.stdout + (result.error or "")
    assert "run access" in result.stdout + (result.error or "")


def test_host_js_cannot_read_outside(cache: Path, workdir: Path, outside: Path) -> None:
    (workdir / "mine.txt").write_text("my-content", encoding="utf-8")
    control = run_cell(_host_js(f"Deno.readTextFileSync('{_p(workdir / 'mine.txt')}')"), workdir, cache)
    assert "my-content" in control.stdout, (control.stdout, control.error)  # the harness can see a success
    result = run_cell(_host_js(f"Deno.readTextFileSync('{_p(outside / 'secret.txt')}')"), workdir, cache)
    assert "decoy-secret" not in result.stdout
    assert "NotCapable" in result.stdout + (result.error or "")


def test_host_js_cannot_write_outside(cache: Path, workdir: Path, outside: Path) -> None:
    control = run_cell(_host_js(f"Deno.writeTextFileSync('{_p(workdir / 'c.txt')}', 'x')"), workdir, cache)
    assert (workdir / "c.txt").exists(), (control.stdout, control.error)  # the harness can see a success
    result = run_cell(_host_js(f"Deno.writeTextFileSync('{_p(outside / 'w.txt')}', 'x')"), workdir, cache)
    assert not (outside / "w.txt").exists()
    assert "NotCapable" in result.stdout + (result.error or "")


def test_env_and_ffi_are_denied(cache: Path, workdir: Path) -> None:
    env = run_cell(_host_js("Deno.env.get('PATH')"), workdir, cache)
    assert "NotCapable" in env.stdout + (env.error or "")
    ffi = run_cell(_host_js("Deno.dlopen('nonexistent.dll', {})"), workdir, cache)
    assert "NotCapable" in ffi.stdout + (ffi.error or "")


def test_cell_cannot_create_a_symlink_out(cache: Path, workdir: Path, outside: Path) -> None:
    # NODEFS passes the target to Deno as a host path, and Deno stats it first: no read access, no link
    code = f"import os\nos.symlink('{_p(outside / 'secret.txt')}', '/work/l')\nprint('READ', open('/work/l').read())\n"
    result = run_cell(code, workdir, cache)
    assert "decoy-secret" not in result.stdout + (result.error or "")
    assert "NotCapable" in (result.error or "")
    assert not (workdir / "l").is_symlink()


def test_planted_link_out_of_the_workdir_is_refused(cache: Path, workdir: Path, outside: Path) -> None:
    # a link already in the workdir (say, from an unpacked archive): Deno would follow it (seen on windows-2025)
    plant_dir_link(workdir / "j", outside)
    result = run_cell("print('READ', open('/work/j/secret.txt').read())\n", workdir, cache)
    assert "decoy-secret" not in result.stdout + (result.error or "")
    assert "is a link" in (result.error or "")


def test_runaway_cell_is_killed(cache: Path, workdir: Path) -> None:
    start = time.perf_counter()
    result = run_cell("while True: pass\n", workdir, cache, timeout=5)
    assert isinstance(result, CellResult)
    assert not result.ok
    assert "timed out" in (result.error or "")
    assert time.perf_counter() - start < 15
