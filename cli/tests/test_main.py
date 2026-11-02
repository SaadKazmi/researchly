import os
import sys

import pytest
from researchly.security.keychain import KeychainUnavailable
from typer.testing import CliRunner

from researchly_cli import docker, engines, main, native, state
from researchly_cli.main import app

runner = CliRunner()
DESKTOP = engines.Engine("docker-desktop", "docker", ("docker", "compose"), ("open", "-a", "Docker"))


def boom(*_args, **_kwargs):
    raise AssertionError("must not be called")


@pytest.fixture
def healthy(monkeypatch):
    ports: set[int] = set()
    monkeypatch.setattr(state, "healthy", lambda port: port in ports)
    return ports


@pytest.fixture(autouse=True)
def web_built(monkeypatch, tmp_path):
    """Default: a built web UI and no frontend sources, so no test ever runs a real pnpm build."""
    (tmp_path / "index.html").write_text("<!doctype html>")
    monkeypatch.setattr(main, "WEB_DIR", tmp_path)
    monkeypatch.setattr(main, "FRONTEND_DIR", tmp_path / "no-frontend")


def _native_start(monkeypatch):
    monkeypatch.setattr(main, "_select", lambda: engines.Selection("native", "runtime pinned to native"))
    monkeypatch.setattr(native, "start", lambda secrets: state.State(mode="native", port=9999))


def test_start_native_writes_state_and_prints_url(monkeypatch, healthy):
    _native_start(monkeypatch)
    result = runner.invoke(app, ["start"])
    assert result.exit_code == 0, result.output
    assert f"http://127.0.0.1:9999/?token={state.load_secrets().local_token}" in result.stdout
    assert "not built" not in result.output
    assert state.read_state() == state.State(mode="native", port=9999)
    assert state.load_config()["last_mode"] == "native"


def test_start_native_warns_when_the_web_ui_is_not_built(monkeypatch, healthy, tmp_path):
    monkeypatch.setattr(main, "WEB_DIR", tmp_path / "missing")
    monkeypatch.setattr(main.shutil, "which", lambda name: None)
    _native_start(monkeypatch)
    result = runner.invoke(app, ["start"])
    assert result.exit_code == 0 and "pnpm -C frontend build" in result.output


def _fake_pnpm(monkeypatch, tmp_path, failing=()):
    web, frontend = tmp_path / "web", tmp_path / "frontend"
    (frontend / "src").mkdir(parents=True)
    (frontend / "package.json").write_text("{}")
    monkeypatch.setattr(main, "WEB_DIR", web)
    monkeypatch.setattr(main, "FRONTEND_DIR", frontend)
    monkeypatch.setattr(main.shutil, "which", lambda name: "pnpm")
    calls: list[list[str]] = []

    def run(argv, check):
        calls.append(argv)
        step = argv[-1] if argv[-1] == "build" else "install"
        if step in failing:
            return type("R", (), {"returncode": 1})()
        if step == "build":
            web.mkdir(exist_ok=True)
            (web / "index.html").write_text("<!doctype html>")
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(main.subprocess, "run", run)
    return calls, frontend


def test_start_native_builds_the_web_ui_once(monkeypatch, healthy, tmp_path):
    calls, frontend = _fake_pnpm(monkeypatch, tmp_path)
    _native_start(monkeypatch)
    result = runner.invoke(app, ["start"])
    assert result.exit_code == 0, result.output
    assert calls == [
        ["pnpm", "-C", str(frontend), "install", "--frozen-lockfile"],
        ["pnpm", "-C", str(frontend), "build"],
    ]
    assert "Building the web UI" in result.output and "not built" not in result.output


def test_start_native_survives_a_failed_web_build(monkeypatch, healthy, tmp_path):
    calls, _ = _fake_pnpm(monkeypatch, tmp_path, failing=("install",))
    _native_start(monkeypatch)
    result = runner.invoke(app, ["start"])
    assert result.exit_code == 0 and len(calls) == 1 and "not built" in result.output


def test_start_native_rebuilds_a_stale_web_ui_only(monkeypatch, healthy, tmp_path):
    calls, frontend = _fake_pnpm(monkeypatch, tmp_path)
    _native_start(monkeypatch)
    (tmp_path / "web").mkdir()
    built = tmp_path / "web" / "index.html"
    built.write_text("<!doctype html>")
    (frontend / "src" / "main.tsx").write_text("")
    os.utime(built, (1, 1))  # sources pulled after the last build
    assert "Building the web UI" in runner.invoke(app, ["start"]).output and len(calls) == 2
    calls.clear()
    state.clear_state()
    assert "Building the web UI" not in runner.invoke(app, ["start"]).output and calls == []


@pytest.mark.parametrize(
    ("platform", "found", "expected"),
    [
        ("win32", {"pnpm": "shim", "pnpm.cmd": "pnpm.cmd"}, "pnpm.cmd"),
        ("win32", {"pnpm": "shim", "pnpm.exe": "pnpm.exe"}, "pnpm.exe"),
        ("win32", {"pnpm": "shim"}, None),
        ("linux", {"pnpm": "pnpm"}, "pnpm"),
    ],
)
def test_pnpm_lookup_skips_the_windows_shell_shim(monkeypatch, platform, found, expected):
    monkeypatch.setattr(main.sys, "platform", platform)
    monkeypatch.setattr(main.shutil, "which", found.get)
    assert main._pnpm() == expected


def test_auto_runtime_switch_warns_about_the_separate_database(monkeypatch, healthy):
    state.save_config({"last_mode": "native"})
    monkeypatch.setattr(main, "_select", lambda: engines.Selection("docker", "colima is ready", DESKTOP))
    monkeypatch.setattr(
        docker, "start", lambda secrets, compose: state.State(mode="docker", port=9996, compose=tuple(compose))
    )
    result = runner.invoke(app, ["start"])
    assert result.exit_code == 0
    assert "separate database" in result.stderr and "researchly config set runtime native" in result.stderr
    assert state.load_config()["last_mode"] == "docker"


@pytest.mark.parametrize("config", [{}, {"last_mode": "native"}, {"runtime": "native", "last_mode": "docker"}])
def test_no_runtime_switch_warning_on_first_run_same_mode_or_a_pin(monkeypatch, healthy, config):
    state.save_config(config)
    monkeypatch.setattr(main, "_select", lambda: engines.Selection("native", "pinned"))
    monkeypatch.setattr(native, "start", lambda secrets: state.State(mode="native", port=9999))
    result = runner.invoke(app, ["start"])
    assert result.exit_code == 0 and result.stderr == ""


@pytest.mark.parametrize("command", ["start", "stop", "setup"])
def test_locked_keychain_fails_without_a_traceback(monkeypatch, command):
    state.write_state(state.State(mode="native", port=9999))

    def locked():
        raise KeychainUnavailable("OS keychain unavailable: locked")

    monkeypatch.setattr(state, "load_secrets", locked)
    monkeypatch.setattr(state, "healthy", lambda port: False)
    monkeypatch.setattr(main, "_select", lambda: engines.Selection("native", "pinned"))
    result = runner.invoke(app, [command])
    assert result.exit_code == 1 and "keychain unavailable" in result.stderr
    assert isinstance(result.exception, SystemExit)


def test_stop_reports_a_native_stop_failure(monkeypatch):
    state.write_state(state.State(mode="native", port=9999))

    def fail(run, secrets):
        raise native.NativeStartError("cannot create the PostgreSQL data dir")

    monkeypatch.setattr(native, "stop", fail)
    result = runner.invoke(app, ["stop"])
    assert result.exit_code == 1 and "cannot create" in result.stderr


def test_start_continues_past_a_leftover_run_that_cannot_be_stopped(monkeypatch, healthy):
    state.write_state(state.State(mode="docker", port=9996, compose=("docker", "compose")))

    def engine_gone(run, secrets):
        raise docker.DockerStartError("`docker compose down` failed")

    monkeypatch.setattr(docker, "stop", engine_gone)
    monkeypatch.setattr(main, "_select", lambda: engines.Selection("native", "pinned"))
    monkeypatch.setattr(native, "start", lambda secrets: state.State(mode="native", port=9997))
    result = runner.invoke(app, ["start"])
    assert result.exit_code == 0, result.output
    assert "could not stop" in result.stderr
    assert state.read_state() == state.State(mode="native", port=9997)


def test_start_docker_uses_the_engine_compose_command(monkeypatch, healthy):
    podman = engines.Engine("podman", "podman", ("podman", "compose"), None)
    monkeypatch.setattr(main, "_select", lambda: engines.Selection("docker", "podman is ready", podman))
    seen = []
    monkeypatch.setattr(
        docker,
        "start",
        lambda secrets, compose: seen.append(compose) or state.State(mode="docker", port=9998, compose=tuple(compose)),
    )
    assert runner.invoke(app, ["start"]).exit_code == 0
    assert seen == [("podman", "compose")]


def test_start_is_a_noop_when_already_healthy(monkeypatch, healthy):
    state.write_state(state.State(mode="native", port=9999))
    healthy.add(9999)
    monkeypatch.setattr(main, "_select", boom)
    result = runner.invoke(app, ["start"])
    assert result.exit_code == 0 and "Already running" in result.stdout
    assert f"/?token={state.load_secrets().local_token}" in result.stdout


def test_status_never_prints_the_token(healthy):
    state.write_state(state.State(mode="native", port=9999))
    healthy.add(9999)
    result = runner.invoke(app, ["status"])
    assert "http://127.0.0.1:9999" in result.stdout and "token" not in result.stdout


@pytest.mark.parametrize(("built", "expected"), [(True, "web ui: built"), (False, "web ui: not built")])
def test_doctor_reports_the_web_ui_build(monkeypatch, tmp_path, built, expected):
    monkeypatch.setattr(engines, "detect", lambda: None)
    monkeypatch.setattr(main, "WEB_DIR", tmp_path if built else tmp_path / "missing")
    assert expected in runner.invoke(app, ["doctor"]).stdout


def test_start_cleans_up_a_crashed_run_first(monkeypatch, healthy):
    state.write_state(state.State(mode="native", port=9999, supervisor_pid=1, supervisor_created=1.0))
    calls = []
    monkeypatch.setattr(native, "stop", lambda run, secrets: calls.append(("stop", run.port)))
    monkeypatch.setattr(main, "_select", lambda: engines.Selection("native", "pinned"))
    monkeypatch.setattr(
        native, "start", lambda secrets: calls.append(("start", 0)) or state.State(mode="native", port=9997)
    )
    assert runner.invoke(app, ["start"]).exit_code == 0
    assert calls == [("stop", 9999), ("start", 0)]


def test_start_reports_a_failed_start(monkeypatch, healthy):
    monkeypatch.setattr(main, "_select", lambda: engines.Selection("native", "pinned"))

    def fail(secrets):
        raise native.NativeStartError("the app did not become healthy within 60 s")

    monkeypatch.setattr(native, "start", fail)
    result = runner.invoke(app, ["start"])
    assert result.exit_code == 1 and "did not become healthy" in result.output
    assert state.read_state() is None


def test_pinned_docker_without_an_engine_fails(monkeypatch):
    state.save_config({"runtime": "docker"})
    monkeypatch.setattr(engines, "detect", lambda: None)
    result = runner.invoke(app, ["start"])
    assert result.exit_code == 1 and "no container engine" in result.output


def test_docker_desktop_licence_note_is_shown_once(monkeypatch, healthy):
    monkeypatch.setattr(main, "_select", lambda: engines.Selection("docker", "ready", DESKTOP))
    monkeypatch.setattr(
        docker, "start", lambda secrets, compose: state.State(mode="docker", port=9996, compose=tuple(compose))
    )
    first = runner.invoke(app, ["start"]).stdout
    state.clear_state()
    second = runner.invoke(app, ["start"]).stdout
    assert "Docker Desktop" in first and "subscription" in first
    assert "subscription" not in second


def test_stop_clears_state(monkeypatch):
    state.write_state(state.State(mode="docker", port=9996, compose=("docker", "compose")))
    stopped = []
    monkeypatch.setattr(docker, "stop", lambda run, secrets: stopped.append(run.port))
    result = runner.invoke(app, ["stop"])
    assert result.exit_code == 0 and stopped == [9996] and state.read_state() is None


def test_stop_when_not_running():
    result = runner.invoke(app, ["stop"])
    assert result.exit_code == 0 and "not running" in result.stdout


def test_status_exit_codes(healthy):
    assert runner.invoke(app, ["status"]).exit_code == 1
    state.write_state(state.State(mode="native", port=9999))
    assert runner.invoke(app, ["status"]).exit_code == 1
    healthy.add(9999)
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0 and "native: running at http://127.0.0.1:9999" in result.stdout


def test_setup_builds_images_in_docker_mode(monkeypatch):
    monkeypatch.setattr(main, "_select", lambda: engines.Selection("docker", "colima is ready", DESKTOP))
    built = []
    monkeypatch.setattr(docker, "build", lambda secrets, compose: built.append(compose))
    result = runner.invoke(app, ["setup"])
    assert result.exit_code == 0 and built == [("docker", "compose")]
    assert "researchly start" in result.stdout


def test_doctor_lists_native_degradations(monkeypatch):
    monkeypatch.setattr(engines, "detect", lambda: None)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    for expected in ("container engine: none found", "secrets store: OS keychain", "tsvector", "status: stopped"):
        assert expected in result.stdout


def test_doctor_notes_docker_host_when_the_engine_is_not_ready(monkeypatch):
    monkeypatch.setattr(engines, "detect", lambda: DESKTOP)
    monkeypatch.setattr(engines, "ready", lambda engine: False)
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    assert "DOCKER_HOST" not in runner.invoke(app, ["doctor"]).stdout
    monkeypatch.setenv("DOCKER_HOST", "tcp://10.0.0.5:2375")
    assert "DOCKER_HOST is set" in runner.invoke(app, ["doctor"]).stdout


@pytest.mark.native
def test_native_cli_round_trip():
    state.save_config({"runtime": "native"})
    s = state.load_secrets()
    instance = native.NativePostgres(state.data_dir(), s.pg_superuser).instance_dir
    try:
        assert runner.invoke(app, ["start"]).exit_code == 0

        def leaked(files, values):
            return [f for f in files if f.is_file() and any(v.encode() in f.read_bytes() for v in values)]

        every = [s.pg_superuser, s.db_app, s.db_ro, s.local_token, s.searxng]
        cli_files = [state.data_dir() / "state.json", state.data_dir() / "config.json", *state.log_dir().rglob("*")]
        assert leaked(cli_files, every) == []
        # pg0 keeps the superuser password in its instance dir (ADR 0002); nothing else may land there
        assert instance.is_dir() and leaked(instance.rglob("*"), every[1:]) == []
        if sys.platform != "win32":
            assert instance.stat().st_mode & 0o777 == 0o700
        assert runner.invoke(app, ["status"]).exit_code == 0
        assert runner.invoke(app, ["stop"]).exit_code == 0
        assert runner.invoke(app, ["status"]).exit_code == 1
    finally:
        pg = native.NativePostgres(state.data_dir(), state.load_secrets().pg_superuser).pg
        pg.stop()
        pg.drop()  # test teardown only
