"""Container engine detection, auto-start and runtime selection (spec §4.1, D2).

Never installs an engine and never elevates. When starting one needs root, print the one command and use native.
"""

import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from researchly_cli.supervise import detached

Runner = Callable[[Sequence[str]], tuple[int, str]]

NATIVE_NOTE = (
    "Using native mode. A container engine (Docker, Podman, Colima, Rancher Desktop or OrbStack) unlocks BM25 "
    "keyword search, GROBID and the container sandbox; `researchly doctor` lists what is degraded."
)


def run(cmd: Sequence[str], timeout: float = 10) -> tuple[int, str]:
    try:
        done = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""
    return done.returncode, done.stdout.strip()


@dataclass(frozen=True)
class Engine:
    name: str
    cli: str
    compose: tuple[str, ...]
    start: tuple[str, ...] | None  # None: starting it needs elevation; `hint` is the command to give the user
    hint: str = ""


@dataclass(frozen=True)
class Selection:
    mode: Literal["docker", "native"]
    reason: str
    engine: Engine | None = None


class RuntimeUnavailable(RuntimeError):
    pass


def _docker_desktop_exe(which: Callable[[str], str | None]) -> Path | None:
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "DockerDesktop" / "Docker Desktop.exe",  # per-user
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Docker" / "Docker" / "Docker Desktop.exe",
    ]
    if found := which("docker"):  # …\Docker\Docker\resources\bin\docker.exe
        candidates += [parent / "Docker Desktop.exe" for parent in Path(found).resolve().parents[:3]]
    return next((p for p in candidates if p.is_file()), None)


def detect(
    run: Runner = run, which: Callable[[str], str | None] = shutil.which, platform: str = sys.platform
) -> Engine | None:
    """The installed engine, running or not (spec §4.1 step 1)."""
    if which("docker"):
        compose = ("docker", "compose") if run(["docker", "compose", "version"])[0] == 0 else ()
        context = run(["docker", "context", "show"])[1]
        if context.startswith("colima"):
            return Engine("colima", "docker", compose, ("colima", "start"))
        if context == "rancher-desktop":
            return Engine("rancher-desktop", "docker", compose, ("rdctl", "start"))
        if context == "orbstack":
            return Engine("orbstack", "docker", compose, ("orb", "start"))
        if platform == "win32" and (exe := _docker_desktop_exe(which)):
            return Engine("docker-desktop", "docker", compose, (str(exe),))
        if platform == "darwin" and (context == "desktop-linux" or Path("/Applications/Docker.app").exists()):
            return Engine("docker-desktop", "docker", compose, ("open", "-a", "Docker"))
        if platform.startswith("linux"):
            if context == "desktop-linux":
                return Engine("docker-desktop", "docker", compose, ("systemctl", "--user", "start", "docker-desktop"))
            if context == "rootless" or run(["systemctl", "--user", "cat", "docker.service"])[0] == 0:
                return Engine("docker-rootless", "docker", compose, ("systemctl", "--user", "start", "docker"))
            return Engine("docker", "docker", compose, None, "sudo systemctl start docker")
        return Engine("docker", "docker", compose, None, "start your Docker engine")
    if which("podman"):
        compose = ("podman", "compose") if which("docker-compose") or which("podman-compose") else ()
        start = None if platform.startswith("linux") else ("podman", "machine", "start")
        return Engine("podman", "podman", compose, start, "systemctl --user start podman.socket")
    return None


def ready(engine: Engine, run: Runner = run) -> bool:
    return run([engine.cli, "info"])[0] == 0


def launch(cmd: Sequence[str]) -> None:
    """Start an engine without waiting: some start commands are GUIs, others block until the engine is up."""
    subprocess.Popen(  # noqa: S603
        list(cmd), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **detached()
    )


def select_runtime(
    pin: str,
    engine: Engine | None,
    *,
    ready: Callable[[Engine], bool],
    launch: Callable[[Sequence[str]], None],
    wait: Callable[[Callable[[], bool], float], bool],
    echo: Callable[[str], None] = print,
    timeout: float = 120.0,
) -> Selection:
    """Spec §4.1: Docker mode when an engine is (or can be made) ready, else native; a pin overrides."""
    if pin == "native":
        return Selection("native", "runtime pinned to native")
    problem = _docker_problem(engine, ready, launch, wait, echo, timeout)
    if problem is None and engine is not None:
        return Selection("docker", f"{engine.name} is ready", engine)
    if pin == "docker":
        raise RuntimeUnavailable(f"runtime pinned to docker, but {problem}")
    return Selection("native", f"{problem}. {NATIVE_NOTE}")


def _docker_problem(
    engine: Engine | None,
    ready: Callable[[Engine], bool],
    launch: Callable[[Sequence[str]], None],
    wait: Callable[[Callable[[], bool], float], bool],
    echo: Callable[[str], None],
    timeout: float,
) -> str | None:
    if engine is None:
        return "no container engine was found"
    if not engine.compose:
        return f"{engine.name} has no compose support (install the Docker Compose plugin or podman-compose)"
    if ready(engine):
        return None
    if engine.start is None:
        return f"{engine.name} is stopped and starting it needs elevation; run `{engine.hint}`"
    echo(f"Starting {engine.name} (waiting up to {int(timeout)} s)…")
    try:
        launch(engine.start)
    except OSError as exc:
        return f"could not start {engine.name}: {exc}"
    if wait(lambda: ready(engine), timeout):
        return None
    return f"{engine.name} did not become ready within {int(timeout)} s"
