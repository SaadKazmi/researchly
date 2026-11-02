import json
import subprocess
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from researchly_cli import engines, main, native, searxng, state, supervise

runner = CliRunner()
REPO = Path(__file__).resolve().parents[2]


def test_native_settings_match_the_docker_settings():
    docker_settings = yaml.safe_load((REPO / "infra" / "searxng" / "settings.yml").read_text("utf-8"))
    assert yaml.safe_load(searxng.settings_text()) == docker_settings


def test_command_binds_loopback_with_one_worker(tmp_path):
    cmd = searxng.command(tmp_path, 9999)
    scripts = "Scripts/granian.exe" if sys.platform == "win32" else "bin/granian"
    assert Path(cmd[0]) == tmp_path / "venv" / scripts
    joined = " ".join(cmd)
    for expected in ("--interface wsgi", "--host 127.0.0.1", "--port 9999", "--workers 1"):
        assert expected in joined
    assert cmd[-1] == "searx.webapp:app"


def test_env_carries_the_secret_outside_argv(tmp_path):
    settings = tmp_path / "settings.yml"
    env = searxng.env(tmp_path, settings, "s3cret")
    assert env["SEARXNG_SECRET"] == "s3cret"  # noqa: S105
    assert env["SEARXNG_SETTINGS_PATH"] == str(settings)
    assert env["PYTHONPATH"] == str(tmp_path / "src")
    assert env["PYTHONUTF8"] == "1"
    assert "s3cret" not in " ".join(searxng.command(tmp_path, 1))


def test_install_rejects_a_tampered_download(tmp_path, monkeypatch):
    monkeypatch.setattr(searxng, "_download", lambda url, dest: dest.write_bytes(b"not the tarball"))
    home = tmp_path / "searxng" / "abc"
    with pytest.raises(searxng.SearxngError, match="sha256"):
        searxng.install(home)
    assert not (home / "ready").exists()
    assert "sha256" in (home.parent / "error.txt").read_text("utf-8")


def test_status_reports_ready_error_or_pending():
    assert "not installed yet" in searxng.status()
    searxng.home().parent.mkdir(parents=True)
    (searxng.home().parent / "error.txt").write_text("SearxngError: no network", "utf-8")
    assert searxng.status() == "off: SearxngError: no network"
    searxng.home().mkdir()
    (searxng.home() / "ready").touch()
    assert searxng.status() == f"ready (SearXNG {searxng.COMMIT[:9]})"


def test_the_pin_is_a_full_sha_with_a_sha256():
    assert len(searxng.COMMIT) == 40 and len(searxng.TARBALL_SHA256) == 64


# ---- native wiring ----


@pytest.fixture
def fakes(monkeypatch):
    """native.start/stop with pg0, provisioning and the supervisors faked; returns the spawn/terminate events."""
    events: list = []

    class Postgres:
        def __init__(self, data_dir, password):
            pass

        def start(self):
            return "postgresql+psycopg://postgres:x@127.0.0.1:5432/postgres", 5432

        def stop(self):
            events.append("pg stop")

    class Supervisor:
        def __init__(self, pid):
            self.pid = pid

        def create_time(self):
            return float(self.pid)

    def spawn(cmd, log, env):
        events.append(("spawn", cmd, log.name, env))
        return Supervisor(100 + len([e for e in events if e[0] == "spawn"]))

    monkeypatch.setattr(native, "NativePostgres", Postgres)
    monkeypatch.setattr(native, "setup_database", lambda url, app, ro: "postgresql+psycopg://app")
    monkeypatch.setattr(native, "spawn", spawn)
    monkeypatch.setattr(native, "terminate_tree", lambda pid, created: events.append(f"terminate {pid}"))
    monkeypatch.setattr(state, "wait_until", lambda check, timeout: True)
    return events


def test_native_start_supervises_searxng_and_records_it(fakes, tmp_path):
    secrets = state.load_secrets()
    run = native.start(secrets, searxng_home=tmp_path)
    assert run.searxng_port is not None
    spawns = [e for e in fakes if e[0] == "spawn"]
    assert len(spawns) == 2
    _, cmd, log, env = spawns[1]
    assert Path(cmd[cmd.index("-m") + 2]).name == "searxng.log"
    assert cmd[cmd.index("--") + 1 :] == searxng.command(tmp_path, run.searxng_port)
    assert env["SEARXNG_SECRET"] == secrets.searxng
    assert env["SEARXNG_SETTINGS_PATH"] == str(tmp_path / "settings.yml")
    assert secrets.searxng not in " ".join(cmd)
    assert (run.searxng_pid, run.searxng_created) == (102, 102.0)


def test_native_start_without_searxng_spawns_none(fakes):
    run = native.start(state.load_secrets(), searxng_home=None)
    assert len([e for e in fakes if e[0] == "spawn"]) == 1
    assert run.searxng_port is None and run.searxng_pid is None


def test_a_failed_start_terminates_both_trees(fakes, monkeypatch, tmp_path):
    monkeypatch.setattr(state, "wait_until", lambda check, timeout: False)
    with pytest.raises(native.NativeStartError):
        native.start(state.load_secrets(), searxng_home=tmp_path)
    assert {"terminate 101", "terminate 102"} <= {e for e in fakes if isinstance(e, str)}
    assert fakes[-1] == "pg stop"


def test_stop_terminates_both_trees(fakes):
    run = state.State(
        mode="native", port=8765, supervisor_pid=1, supervisor_created=1.0, searxng_pid=2, searxng_created=2.0
    )
    native.stop(run, state.load_secrets())
    assert fakes == ["terminate 1", "terminate 2", "pg stop"]


# ---- main wiring ----


@pytest.fixture
def web_search(monkeypatch, tmp_path):
    """Native `start` with every collaborator faked; returns what native.start was given."""
    given: dict = {}
    monkeypatch.setattr(main, "_select", lambda: engines.Selection("native", "pinned"))
    monkeypatch.setattr(main, "_ensure_web_ui", lambda: None)
    monkeypatch.setattr(searxng, "home", lambda: tmp_path / "sx")
    monkeypatch.setattr(state, "wait_until", lambda check, timeout, *a: check())

    def fake_start(secrets, searxng_home=None):
        given["home"] = searxng_home
        return state.State(mode="native", port=9999, searxng_port=9998 if searxng_home else None)

    monkeypatch.setattr(native, "start", fake_start)
    monkeypatch.setattr(state, "healthy", lambda port: True)
    return given


def test_start_installs_web_search_and_passes_the_home(web_search, monkeypatch, tmp_path):
    installed = []
    monkeypatch.setattr(searxng, "install", lambda home: installed.append(home))
    monkeypatch.setattr(searxng, "healthy", lambda port: True)
    result = runner.invoke(main.app, ["start"])
    assert result.exit_code == 0, result.output
    assert installed == [tmp_path / "sx"] and web_search["home"] == tmp_path / "sx"
    assert "Installing web search" in result.output and "warning" not in result.output


def test_start_still_works_when_the_install_fails(web_search, monkeypatch):
    def fail(home):
        raise searxng.SearxngError("no network")

    monkeypatch.setattr(searxng, "install", fail)
    result = runner.invoke(main.app, ["start"])
    assert result.exit_code == 0, result.output
    assert "warning: web search is off: no network" in result.output
    assert web_search["home"] is None


def test_start_warns_when_searxng_never_gets_healthy(web_search, monkeypatch):
    monkeypatch.setattr(searxng, "install", lambda home: None)
    monkeypatch.setattr(searxng, "healthy", lambda port: False)
    result = runner.invoke(main.app, ["start"])
    assert result.exit_code == 0, result.output
    assert "web search is still starting or failed" in result.output and "searxng.log" in result.output


def test_doctor_reports_web_search_in_native_mode(monkeypatch):
    monkeypatch.setattr(engines, "detect", lambda: None)
    monkeypatch.setattr(searxng, "status", lambda: "ready (SearXNG 54ca1d9cf)")
    out = runner.invoke(main.app, ["doctor"]).stdout
    assert "web search: ready (SearXNG 54ca1d9cf)" in out
    assert not any("web search" in line for line in main.DEGRADED_NATIVE)


def test_state_roundtrips_the_searxng_fields_and_rejects_a_bad_port():
    run = state.State(mode="native", port=8765, searxng_port=8766, searxng_pid=7, searxng_created=1.5)
    state.write_state(run)
    assert state.read_state() == run
    state.write_state(state.State(mode="native", port=8765, searxng_port=70000))
    assert state.read_state() is None


# ---- real install ----


class _Mock(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(
            {"items": [{"link": "https://example.org/p1", "name": "Mock Paper One", "abstract": "offline fixture"}]}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


@pytest.mark.searxng
def test_installs_and_answers_json_from_a_mock_engine(tmp_path):
    mock = HTTPServer(("127.0.0.1", 0), _Mock)
    threading.Thread(target=mock.serve_forever, daemon=True).start()
    home = tmp_path / "sx"
    searxng.install(home)
    assert (home / "ready").exists()
    settings = yaml.safe_load(searxng.settings_text())
    settings["use_default_settings"] = {"engines": {"keep_only": []}}
    settings["engines"] = [
        {
            "name": "mock",
            "engine": "json_engine",
            "shortcut": "mk",
            "categories": "general",
            "search_url": f"http://127.0.0.1:{mock.server_port}/?q={{query}}",
            "results_query": "items",
            "url_query": "link",
            "title_query": "name",
            "content_query": "abstract",
            "disabled": False,
            "enable_http": True,  # plain http needs this
        }
    ]
    settings_file = tmp_path / "settings.yml"
    settings_file.write_text(yaml.safe_dump(settings), "utf-8")
    port = state.free_port(0)
    proc = subprocess.Popen(  # noqa: S603
        searxng.command(home, port), env=searxng.env(home, settings_file, "test-secret-0123456789abcdef")
    )
    try:
        assert state.wait_until(lambda: searxng.healthy(port), 120)
        url = f"http://127.0.0.1:{port}/search?q=hello&format=json&engines=mock"
        with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 - fixed loopback URL
            data = json.load(response)
        assert data["results"][0]["title"] == "Mock Paper One"
        assert data["unresponsive_engines"] == []
    finally:
        supervise.terminate_tree(proc.pid)
        mock.shutdown()
