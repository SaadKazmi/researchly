import re
import subprocess
import urllib.request
import uuid

import pytest

from researchly_cli import docker, engines, state


def test_compose_env_supplies_every_required_variable():
    required = set(re.findall(r"\$\{(RESEARCHLY_[A-Z_]+):\?", docker.COMPOSE_FILE.read_text("utf-8")))
    env = docker.compose_env(state.load_secrets(), 8765)
    assert required and required <= env.keys()


def test_compose_file_pins_every_image():
    text = docker.COMPOSE_FILE.read_text("utf-8")
    for image in re.findall(r"image:\s*(\S+)", text):
        assert image.endswith(":dev") or re.search(r":\d", image), image  # local builds or pinned tags, never latest


@pytest.fixture
def runs(monkeypatch):
    """A fake `subprocess.run` that records every compose call."""
    calls: list[tuple[list[str], dict]] = []

    def fake(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake)
    return calls


def test_compose_runs_from_the_repo_root(runs):
    docker.build(state.load_secrets(), ("docker", "compose"))
    assert runs[0][1]["cwd"] == docker.COMPOSE_FILE.parents[1]  # bake reads only inside its cwd


def test_start_creates_the_pg_dir_as_the_user(runs):
    docker.start(state.load_secrets(), ("docker", "compose"))
    assert (state.data_dir() / "pg" / "18" / "docker").is_dir()  # else rootful Docker creates it root-owned


def test_stop_covers_every_profile(runs):
    docker.stop(state.State(mode="docker", port=8765, compose=("docker", "compose")), state.load_secrets())
    assert runs[0][0][-3:] == ["--profile", "*", "down"]  # grobid too


def test_failed_start_takes_the_stack_down(monkeypatch):
    calls: list[list[str]] = []

    def compose(cmd, args, env):
        calls.append(list(args))
        if args[:3] == ["up", "-d", "--wait"]:
            raise docker.DockerStartError("`docker compose up` failed")

    monkeypatch.setattr(docker, "_compose", compose)
    with pytest.raises(docker.DockerStartError):
        docker.start(state.load_secrets(), ("docker", "compose"))
    assert calls[-1] == ["--profile", "*", "down"]


def test_app_image_builds_the_frontend():
    text = docker.COMPOSE_FILE.read_text("utf-8")
    assert "additional_contexts" in text and "frontend: ../frontend" in text


@pytest.mark.docker
def test_docker_stack_serves_health_with_bm25(monkeypatch):
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", f"researchly-test-{uuid.uuid4().hex[:6]}")
    engine = engines.detect()
    assert engine is not None and engine.compose and engines.ready(engine)
    secrets = state.load_secrets()
    run = docker.start(secrets, engine.compose)
    try:
        assert state.healthy(run.port)
        with urllib.request.urlopen(f"{run.url}/") as page:  # noqa: S310 - fixed local URL
            assert page.status == 200 and b'<div id="root">' in page.read()

        def psql(sql: str) -> str:
            cmd = [
                *engine.compose,
                "-f",
                str(docker.COMPOSE_FILE),
                "exec",
                "-T",
                "postgres",
                "psql",
                "-U",
                "postgres",
                "-d",
                "researchly",
                "-tAc",
                sql,
            ]
            env = docker.compose_env(secrets, run.port)
            return subprocess.run(cmd, env=env, capture_output=True, text=True, check=True).stdout.strip()  # noqa: S603

        assert {"vector", "pg_trgm", "pg_textsearch"} <= set(
            psql("SELECT string_agg(extname, ',') FROM pg_extension").split(",")
        )
        assert psql("SELECT value #>> '{}' FROM capability WHERE key = 'bm25_engine'") == "pg_textsearch"
    finally:
        docker.stop(run, secrets)
    assert not state.healthy(run.port)
