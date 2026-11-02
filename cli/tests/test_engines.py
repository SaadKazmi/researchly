import subprocess
import sys

import pytest

from researchly_cli.engines import NATIVE_NOTE, Engine, RuntimeUnavailable, detect, launch, select_runtime

COMPOSE_OK = {("docker", "compose", "version"): (0, "Docker Compose version v5.2.0")}


def fake_run(extra: dict[tuple[str, ...], tuple[int, str]] | None = None):
    responses = {**COMPOSE_OK, **(extra or {})}
    return lambda cmd: responses.get(tuple(cmd), (1, ""))


def which_only(*names: str):
    return lambda name: f"/usr/bin/{name}" if name in names else None


def context(name: str) -> dict[tuple[str, ...], tuple[int, str]]:
    return {("docker", "context", "show"): (0, name)}


def test_no_engine():
    assert detect(fake_run(), which_only(), "linux") is None


@pytest.mark.parametrize(
    ("ctx", "name", "start"),
    [
        ("colima", "colima", ("colima", "start")),
        ("rancher-desktop", "rancher-desktop", ("rdctl", "start")),
        ("orbstack", "orbstack", ("orb", "start")),
    ],
)
def test_engine_from_docker_context(ctx, name, start):
    engine = detect(fake_run(context(ctx)), which_only("docker"), "darwin")
    assert engine == Engine(name, "docker", ("docker", "compose"), start)


def test_docker_desktop_on_macos():
    engine = detect(fake_run(context("desktop-linux")), which_only("docker"), "darwin")
    assert engine is not None and engine.start == ("open", "-a", "Docker")


def test_docker_desktop_on_windows(tmp_path, monkeypatch):
    exe = tmp_path / "Programs" / "DockerDesktop" / "Docker Desktop.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "pf"))
    engine = detect(fake_run(context("desktop-linux")), which_only("docker"), "win32")
    assert engine is not None and (engine.name, engine.start) == ("docker-desktop", (str(exe),))


def test_rootless_docker_on_linux():
    run = fake_run({**context("default"), ("systemctl", "--user", "cat", "docker.service"): (0, "")})
    engine = detect(run, which_only("docker"), "linux")
    assert engine is not None and engine.start == ("systemctl", "--user", "start", "docker")


def test_rootful_docker_on_linux_needs_elevation():
    engine = detect(fake_run(context("default")), which_only("docker"), "linux")
    assert engine is not None and engine.start is None and engine.hint == "sudo systemctl start docker"


def test_docker_without_compose_plugin():
    engine = detect(lambda cmd: (0, "colima") if cmd[1] == "context" else (1, ""), which_only("docker"), "darwin")
    assert engine is not None and engine.compose == ()


def test_podman_needs_a_compose_provider():
    bare = detect(fake_run(), which_only("podman"), "darwin")
    assert bare is not None and bare.compose == () and bare.start == ("podman", "machine", "start")
    full = detect(fake_run(), which_only("podman", "podman-compose"), "linux")
    assert full is not None and full.compose == ("podman", "compose")
    assert full.start == ("systemctl", "--user", "start", "podman.socket")  # rootless: no elevation


def test_podman_docker_shim_is_podman():
    run = fake_run({("docker", "--version"): (0, "podman version 5.4.0"), **context("default")})
    engine = detect(run, which_only("docker", "podman", "podman-compose"), "linux")
    assert engine is not None and engine.name == "podman" and engine.start is not None


@pytest.mark.skipif(sys.platform != "win32", reason="Job Object breakaway is Windows-only")
def test_launch_retries_without_breakaway(monkeypatch):
    assert sys.platform == "win32"  # skipif is invisible to pyright
    flags: list[int] = []

    def popen(cmd, **kwargs):
        flags.append(kwargs["creationflags"])
        if kwargs["creationflags"] & subprocess.CREATE_BREAKAWAY_FROM_JOB:
            raise PermissionError(5, "Access is denied")  # the CLI's own Job Object forbids breakaway

    monkeypatch.setattr(subprocess, "Popen", popen)
    launch(["colima", "start"])
    assert len(flags) == 2 and not flags[1] & subprocess.CREATE_BREAKAWAY_FROM_JOB


COLIMA = Engine("colima", "docker", ("docker", "compose"), ("colima", "start"))


def boom(*_args, **_kwargs):
    raise AssertionError("must not be called")


def test_pinned_native_never_probes():
    selection = select_runtime("native", None, ready=boom, launch=boom, wait=boom)
    assert selection.mode == "native"


def test_ready_engine_selects_docker():
    selection = select_runtime("auto", COLIMA, ready=lambda e: True, launch=boom, wait=boom)
    assert (selection.mode, selection.engine) == ("docker", COLIMA)


def test_stopped_engine_is_started_then_selected():
    launched: list[tuple[str, ...]] = []
    states = iter([False, True])
    selection = select_runtime(
        "auto",
        COLIMA,
        ready=lambda e: next(states),
        launch=lambda cmd: launched.append(tuple(cmd)),
        wait=lambda check, timeout: check(),
        echo=lambda msg: None,
    )
    assert selection.mode == "docker"
    assert launched == [("colima", "start")]


def test_start_timeout_falls_back_to_native_on_auto():
    selection = select_runtime(
        "auto",
        COLIMA,
        ready=lambda e: False,
        launch=lambda cmd: None,
        wait=lambda check, timeout: False,
        echo=lambda msg: None,
    )
    assert selection.mode == "native"
    assert "did not become ready" in selection.reason and NATIVE_NOTE in selection.reason


def test_launch_failure_falls_back():
    def missing(cmd):
        raise FileNotFoundError(cmd[0])

    selection = select_runtime("auto", COLIMA, ready=lambda e: False, launch=missing, wait=boom, echo=lambda m: None)
    assert selection.mode == "native" and "could not start colima" in selection.reason


def test_elevation_needed_explains_the_command():
    rootful = Engine("docker", "docker", ("docker", "compose"), None, "sudo systemctl start docker")
    selection = select_runtime("auto", rootful, ready=lambda e: False, launch=boom, wait=boom)
    assert selection.mode == "native" and "sudo systemctl start docker" in selection.reason


def test_engine_without_compose_falls_back():
    no_compose = Engine("podman", "podman", (), ("podman", "machine", "start"))
    assert select_runtime("auto", no_compose, ready=boom, launch=boom, wait=boom).mode == "native"


def test_pinned_docker_raises_when_unavailable():
    with pytest.raises(RuntimeUnavailable, match="no container engine"):
        select_runtime("docker", None, ready=boom, launch=boom, wait=boom)
