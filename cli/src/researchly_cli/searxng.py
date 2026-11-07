"""Native web search (spec §4.1, D14): a pinned upstream SearXNG, installed and supervised by the CLI.

SearXNG is AGPL-3.0: it is downloaded from upstream at install time, run unmodified as a separate process and
talked to over HTTP only. Never import `searx` here and never vendor its source (ADR 0004).
"""

import contextlib
import hashlib
import http.client
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path, PurePosixPath

from researchly_cli import state

COMMIT = "54ca1d9cfb08674f66261d7f351b063558dfcc62"  # the commit of the Docker image in infra/compose.yaml
COMMIT_DATE = "2026.10.25"
TARBALL_SHA256 = "a40695381839d492eb6e65ec3af53de4fbefe7ad709ef39e945153fe14fc7ea9"
URL = f"https://codeload.github.com/searxng/searxng/tar.gz/{COMMIT}"
PORT = 8766
REQUIREMENTS = Path(__file__).with_name("searxng-requirements.txt")  # hash-locked; see its first line

# A test keeps this equal to infra/searxng/settings.yml (the Docker mode's copy). The secret comes from the env.
_SETTINGS = """\
use_default_settings:
  engines:
    keep_only: [brave, mojeek, yahoo, github]
server:
  limiter: false
  public_instance: false
  image_proxy: false
search:
  formats: [json]
outgoing:
  request_timeout: 5.0
engines:
  - name: mojeek
    inactive: false
  - name: yahoo
    disabled: false
"""

# SearXNG imports `pwd` at load time, only to log a failed Valkey connection, and Valkey is never configured here.
# A module in the venv avoids patching AGPL files.
_USER_LOOKUP_SHIM = '''"""Windows stand-in for the POSIX `pwd` module that SearXNG imports (searx/valkeydb.py).

It is used only to log a failed Valkey connection and Valkey is never configured, so a name lookup suffices.
Written by Researchly's installer into the venv instead of patching SearXNG (AGPL, ADR 0004).
"""

import getpass
from collections import namedtuple

struct_passwd = namedtuple("struct_passwd", "pw_name pw_passwd pw_uid pw_gid pw_gecos pw_dir pw_shell")


def getpwuid(uid):
    return struct_passwd(getpass.getuser(), "x", uid, 0, "", "", "")
'''


class SearxngError(RuntimeError):
    pass


def home() -> Path:
    return state.data_dir() / "searxng" / COMMIT[:12]


def settings_text() -> str:
    return _SETTINGS


def _venv_bin(home: Path, name: str) -> Path:
    return (
        home
        / "venv"
        / ("Scripts" if sys.platform == "win32" else "bin")
        / (name + (".exe" if sys.platform == "win32" else ""))
    )


def command(home: Path, port: int) -> list[str]:
    return [
        str(_venv_bin(home, "granian")),
        "--interface",
        "wsgi",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--workers",
        "1",
        "--blocking-threads",
        "4",
        "searx.webapp:app",
    ]


def env(home: Path, settings: Path, secret: str) -> dict[str, str]:
    """The secret travels only here, never in argv."""
    return {
        **os.environ,
        "SEARXNG_SETTINGS_PATH": str(settings),
        "SEARXNG_SECRET": secret,
        "PYTHONPATH": str(home / "src"),
        "PYTHONUTF8": "1",
    }


def healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as response:  # noqa: S310 - fixed local URL
            return response.status == 200
    except (OSError, http.client.HTTPException):
        return False


def status() -> str:
    root = home()
    if (root / "ready").exists():
        return f"ready (SearXNG {COMMIT[:9]})"
    error = root.parent / "error.txt"
    if error.exists():
        return f"off: {error.read_text('utf-8').strip()}"
    return "not installed yet (installed on the next native start)"


def _download(url: str, dest: Path) -> None:
    # The CLI's own installer, not a data connector: PoliteClient does not apply.
    with urllib.request.urlopen(url, timeout=60) as response, dest.open("wb") as out:  # noqa: S310 - fixed https URL
        shutil.copyfileobj(response, out)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract(tarball: Path, src: Path) -> None:
    """Only searx/, the requirements and the licence; links are skipped (upstream has one Windows cannot create)."""
    prefix = f"searxng-{COMMIT}/"
    with tarfile.open(tarball) as tar:
        members = []
        for member in tar:
            rest = member.name.removeprefix(prefix)
            keep = (
                rest.startswith("searx/") or rest == "LICENSE" or (rest.startswith("requirements") and "/" not in rest)
            )
            safe = (
                member.name.startswith(prefix) and ".." not in PurePosixPath(rest).parts
            )  # one bad member must not abort
            if keep and safe and not (member.issym() or member.islnk()):
                member.name = rest  # drop the top directory: shorter paths on Windows
                members.append(member)
        tar.extractall(src, members=members, filter="data")


def _run(cmd: list[str]) -> None:
    done = subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603 - our own argv
    if done.returncode:
        tail = (done.stderr.strip().splitlines() or ["no output"])[-1]
        raise SearxngError(f"{Path(cmd[0]).name} failed: {tail}")


def _install(home: Path) -> None:
    tarball = home.parent / "searxng.tar.gz"
    try:
        _download(URL, tarball)
        if (got := _sha256(tarball)) != TARBALL_SHA256:
            raise SearxngError(f"the download's sha256 is {got}, expected {TARBALL_SHA256}")
        _extract(tarball, home / "src")
    finally:
        tarball.unlink(missing_ok=True)
    uv = os.environ.get("UV") or shutil.which("uv")
    if not uv:
        raise SearxngError("uv not found")
    python = _venv_bin(home, "python")
    _run([uv, "venv", "--python", "3.12", str(home / "venv")])
    _run([uv, "pip", "install", "--python", str(python), "--require-hashes", "-r", str(REQUIREMENTS)])
    (home / "src" / "searx" / "version_frozen.py").write_text(
        f'VERSION_STRING = "{COMMIT_DATE}+{COMMIT[:9]}"\nVERSION_TAG = "{COMMIT_DATE}+{COMMIT[:9]}"\n'
        f'DOCKER_TAG = "{COMMIT_DATE}-{COMMIT[:9]}"\nGIT_URL = "https://github.com/searxng/searxng"\n'
        'GIT_BRANCH = "master"\n',
        "utf-8",
    )
    if sys.platform == "win32":
        (home / "venv" / "Lib" / "site-packages" / "pwd.py").write_text(_USER_LOOKUP_SHIM, "utf-8")
    (home / "settings.yml").write_text(_SETTINGS, "utf-8")


def install(home: Path) -> None:
    """Idempotent via `home/ready`. A failure is recorded in `error.txt` beside it, for `doctor`."""
    if (home / "ready").exists():
        return
    error = home.parent / "error.txt"
    try:
        shutil.rmtree(home, ignore_errors=True)  # a half-finished earlier attempt
        home.mkdir(parents=True, exist_ok=True)
        _install(home)
    except Exception as exc:
        first = next(iter(str(exc).splitlines()), "")
        with contextlib.suppress(OSError):
            error.write_text(f"{type(exc).__name__}: {first}\n", "utf-8")
        raise exc if isinstance(exc, SearxngError) else SearxngError(f"{type(exc).__name__}: {first}") from exc
    error.unlink(missing_ok=True)
    (home / "ready").touch()
