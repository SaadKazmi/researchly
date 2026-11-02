import pytest
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


def test_start_native_writes_state_and_prints_url(monkeypatch, healthy):
    monkeypatch.setattr(main, "_select", lambda: engines.Selection("native", "runtime pinned to native"))
    monkeypatch.setattr(native, "start", lambda secrets: state.State(mode="native", port=9999))
    result = runner.invoke(app, ["start"])
    assert result.exit_code == 0, result.output
    assert "http://127.0.0.1:9999" in result.stdout
    assert state.read_state() == state.State(mode="native", port=9999)


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


def test_start_cleans_up_a_crashed_run_first(monkeypatch, healthy):
    state.write_state(state.State(mode="native", port=9999, supervisor_pid=1))
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
    try:
        assert runner.invoke(app, ["start"]).exit_code == 0
        assert runner.invoke(app, ["status"]).exit_code == 0
        assert runner.invoke(app, ["stop"]).exit_code == 0
        assert runner.invoke(app, ["status"]).exit_code == 1
    finally:
        pg = native.NativePostgres(state.data_dir(), state.load_secrets().pg_superuser).pg
        pg.stop()
        pg.drop()  # test teardown only
