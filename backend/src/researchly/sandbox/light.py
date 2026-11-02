"""Light code cells: Pyodide inside Deno, with Deno's permissions as the boundary (spec §19.4, ADR 0006).

No network, no subprocesses, no FFI, no environment; reads only Pyodide itself, the package cache and the cell's
workdir; writes only the workdir. Pyodide-level escapes (ctypes, emscripten_run_script) reach Deno's JS, where
these flags still apply, so the platform tests attempt each escape on every OS.
"""

import json
import os
import subprocess
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path

PYODIDE_VERSION = "314.0.7"  # also in pyodide_runner.ts
PACKAGES = ("numpy", "pandas", "matplotlib")  # also in pyodide_runner.ts
RUNNER = Path(__file__).with_name("pyodide_runner.ts")


@dataclass(frozen=True)
class CellResult:
    ok: bool
    stdout: str
    error: str | None


def _deno() -> str:
    return str(Path(sysconfig.get_path("scripts")) / ("deno.exe" if sys.platform == "win32" else "deno"))


def _p(path: Path) -> str:
    return path.resolve().as_posix()  # Deno denies 8.3 short paths on Windows


def _env(cache: Path) -> dict[str, str]:
    env = {"DENO_DIR": _p(cache / "deno"), "DENO_NO_UPDATE_CHECK": "1", "NO_COLOR": "1"}
    if sys.platform == "win32" and (root := os.environ.get("SYSTEMROOT")):
        env["SYSTEMROOT"] = root  # Windows DLL loading needs it
    return env


def _check_paths(*paths: Path) -> None:
    for path in paths:
        if "," in str(path):  # Deno's --allow-read/--allow-write lists are comma-separated
            raise ValueError(f"path must not contain a comma: {path}")


def _first_link(workdir: Path) -> Path | None:
    """Deno checks a link's own path, not its target, so a link in the workdir would lead outside it.

    A cell cannot create links (Deno stats the target first), so this only catches links the host put there.
    """
    for root, dirs, files in os.walk(workdir):  # top-down: a linked dir is seen before anything inside it
        for name in dirs + files:
            path = Path(root, name)
            if path.is_symlink() or path.is_junction() or (path.is_file() and path.stat().st_nlink > 1):
                return path
    return None


def _pyodide_dir(cache: Path) -> Path:
    return cache / "deno" / "npm" / "registry.npmjs.org" / "pyodide" / PYODIDE_VERSION


def warm_up(cache: Path) -> None:
    """Download Pyodide and the default packages once; network only to the Pyodide CDN."""
    pkgs = cache / "pyodide"
    pkgs.mkdir(parents=True, exist_ok=True)
    subprocess.run(  # noqa: S603 - fixed argv
        [
            _deno(),
            "run",
            "--no-prompt",
            "--allow-net=cdn.jsdelivr.net",
            f"--allow-read={_p(cache)}",
            f"--allow-write={_p(pkgs)}",
            str(RUNNER),
            "--warm",
            _p(pkgs),
        ],
        env=_env(cache),
        check=True,
        capture_output=True,
        timeout=600,
    )


def run_cell(code: str, workdir: Path, cache: Path, *, timeout: float = 60.0) -> CellResult:
    _check_paths(workdir, cache)
    if link := _first_link(workdir):
        return CellResult(
            ok=False, stdout="", error=f"refused: {link.relative_to(workdir)} is a link out of the workdir"
        )
    read = ",".join(_p(p) for p in (_pyodide_dir(cache), cache / "pyodide", workdir))
    args = [
        _deno(),
        "run",
        "--no-prompt",
        "--cached-only",
        f"--allow-read={read}",
        f"--allow-write={_p(workdir)}",
        str(RUNNER),
        _p(cache / "pyodide"),
        _p(workdir),
    ]
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv
            args, input=code, capture_output=True, text=True, encoding="utf-8", env=_env(cache), timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return CellResult(ok=False, stdout="", error=f"timed out after {timeout:g} s")
    lines = proc.stdout.strip().splitlines()
    try:
        result = json.loads(lines[-1])
    except (IndexError, ValueError):
        return CellResult(ok=False, stdout=proc.stdout, error=proc.stderr.strip() or f"exit code {proc.returncode}")
    return CellResult(ok=bool(result["ok"]), stdout=result["stdout"], error=result["error"])
