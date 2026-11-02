import re
import subprocess
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


@pytest.mark.docker
def test_docker_stack_serves_health_with_bm25(monkeypatch):
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", f"researchly-test-{uuid.uuid4().hex[:6]}")
    engine = engines.detect()
    assert engine is not None and engine.compose and engines.ready(engine)
    secrets = state.load_secrets()
    run = docker.start(secrets, engine.compose)
    try:
        assert state.healthy(run.port)

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
