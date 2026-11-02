import os
import platform
import sys
import time
from pathlib import Path

import psutil
import pytest
from sqlalchemy import create_engine, text

from researchly_cli import native, state


@pytest.fixture
def fakes(monkeypatch):
    """native.start/stop with pg0, provisioning and the supervisor faked; returns the events in order."""
    events: list[str] = []

    class Postgres:
        def __init__(self, data_dir, password):
            pass

        def start(self):
            events.append("pg start")
            return "postgresql+psycopg://postgres:x@127.0.0.1:5432/postgres", 5432

        def stop(self):
            events.append("pg stop")

    class Supervisor:
        pid = 4242

        def create_time(self):
            return 1.0

    monkeypatch.setattr(native, "NativePostgres", Postgres)
    monkeypatch.setattr(native, "setup_database", lambda url, app, ro: "postgresql+psycopg://app")
    monkeypatch.setattr(native, "spawn", lambda cmd, log, env: events.append("spawn") or Supervisor())
    monkeypatch.setattr(native, "terminate_tree", lambda pid, created: events.append(f"terminate {pid}"))
    return events


def test_failed_provisioning_stops_postgres_and_hides_detail(fakes, monkeypatch):
    def fail(url, app, ro):
        raise RuntimeError("could not connect\nCREATE ROLE researchly_app PASSWORD 'hunter2'")

    monkeypatch.setattr(native, "setup_database", fail)
    with pytest.raises(native.NativeStartError) as caught:
        native.start(state.load_secrets())
    assert str(caught.value) == "RuntimeError: could not connect"
    assert fakes == ["pg start", "pg stop"]


def test_interrupted_health_wait_stops_everything(fakes, monkeypatch):
    def interrupted(check, timeout):
        raise KeyboardInterrupt

    monkeypatch.setattr(state, "wait_until", interrupted)
    with pytest.raises(KeyboardInterrupt):
        native.start(state.load_secrets())
    assert fakes == ["pg start", "spawn", "terminate 4242", "pg stop"]


def test_stop_skips_a_supervisor_without_a_create_time(fakes):
    native.stop(state.State(mode="native", port=8765, supervisor_pid=4242), state.load_secrets())
    assert fakes == ["pg stop"]  # without the create time a reused PID cannot be told apart


def test_uncreatable_pg_dir_names_the_likely_root_owner(monkeypatch):
    def denied(self, *args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "mkdir", denied)
    with pytest.raises(native.NativeStartError, match="owned by root"):
        native.NativePostgres(state.data_dir(), "x")


def _report(line: str) -> None:
    """Spike measurements (spec §26 risk 1) go to the CI job summary for ADR 0002."""
    print(line)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"- {sys.platform}/{platform.machine()}: {line}\n")


@pytest.fixture
def secrets():
    s = state.load_secrets()
    yield s
    pg = native.NativePostgres(state.data_dir(), s.pg_superuser).pg
    pg.stop()
    pg.drop()  # test teardown only: drop() deletes the data dir


@pytest.mark.native
def test_native_start_serves_the_app_on_pg0_and_stops_cleanly(secrets):
    began = time.monotonic()
    run = native.start(secrets)
    cold = time.monotonic() - began
    try:
        assert state.healthy(run.port)
        assert run.pg_port is not None
        engine = create_engine(
            f"postgresql+psycopg://researchly_app:{secrets.db_app}@127.0.0.1:{run.pg_port}/researchly"
        )
        with engine.connect() as conn:
            exts = set(conn.scalars(text("SELECT extname FROM pg_extension")))
            bm25 = conn.scalar(text("SELECT value FROM capability WHERE key = 'bm25_engine'"))
        engine.dispose()
        assert {"vector", "pg_trgm"} <= exts
        assert bm25 == "tsvector"
        info = native.NativePostgres(state.data_dir(), secrets.pg_superuser).pg.info()
        assert info.data_dir is not None
        assert Path(info.data_dir).resolve() == (state.data_dir() / "pg" / "18" / "native").resolve()

        # the supervisor restarts a crashed app
        sup = psutil.Process(run.supervisor_pid)
        # the venv launcher's real supervisor process also has "uvicorn" in its command line: spare it
        apps = [
            p
            for p in sup.children(recursive=True)
            if "uvicorn" in (c := " ".join(p.cmdline())) and "researchly_cli.supervise" not in c
        ]
        assert apps
        for p in apps:
            p.kill()
        assert state.wait_until(lambda: not state.healthy(run.port), 10, 0.2)
        assert state.wait_until(lambda: state.healthy(run.port), 30, 0.5)
    finally:
        native.stop(run, secrets)
    assert not state.healthy(run.port)
    assert not native.NativePostgres(state.data_dir(), secrets.pg_superuser).pg.running

    began = time.monotonic()
    warm_run = native.start(secrets)
    warm = time.monotonic() - began
    native.stop(warm_run, secrets)
    _report(f"pg0 native start: cold {cold:.1f} s (initdb + provision + migrate + app), warm {warm:.1f} s")
