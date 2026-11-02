import json
import socket

import pytest

from researchly_cli import state


def test_state_roundtrip():
    run = state.State(mode="docker", port=8765, compose=("docker", "compose"))
    state.write_state(run)
    assert state.read_state() == run
    assert run.url == "http://127.0.0.1:8765"
    state.clear_state()
    assert state.read_state() is None


def _write(name: str, text: str) -> None:
    state.data_dir().mkdir(parents=True, exist_ok=True)
    (state.data_dir() / name).write_text(text, "utf-8")


def test_read_state_drops_unknown_keys():
    _write("state.json", json.dumps({"mode": "native", "port": 8765, "from_a_newer_cli": 1}))
    assert state.read_state() == state.State(mode="native", port=8765)


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "[]",
        json.dumps({"mode": "kubernetes", "port": 8765}),
        json.dumps({"mode": "docker", "port": 8765, "compose": ["rm", "-rf"]}),
        json.dumps({"mode": "native", "port": 0}),
        json.dumps({"mode": "native", "port": "8765"}),
        json.dumps({"mode": "native", "port": 8765, "pg_port": 70000}),
        json.dumps({"mode": "native", "port": 8765, "supervisor_pid": 42}),
    ],
)
def test_read_state_rejects_corrupt_or_invalid_content(content):
    _write("state.json", content)
    assert state.read_state() is None


@pytest.mark.parametrize("content", ["{not json", "[1]", json.dumps({"runtime": "native", "n": 1, "x": None})])
def test_load_config_ignores_corrupt_or_non_string_entries(content):
    _write("config.json", content)
    config = state.load_config()
    assert config["runtime"] == ("native" if "runtime" in content else "auto")
    assert all(isinstance(v, str) for v in config.values()) and config.keys() <= {"runtime"}


def test_config_defaults_and_persists():
    assert state.load_config() == {"runtime": "auto"}
    state.save_config({"runtime": "native"})
    assert state.load_config()["runtime"] == "native"


def test_secrets_are_generated_once_and_distinct():
    first = state.load_secrets()
    assert state.load_secrets() == first
    values = [first.pg_superuser, first.db_app, first.db_ro, first.local_token, first.searxng]
    assert len(set(values)) == 5
    assert all(len(v) == 64 for v in values)


def test_free_port_prefers_requested_then_falls_back():
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        taken = busy.getsockname()[1]
        assert state.free_port(taken) != taken
    assert state.free_port(taken) == taken


def test_healthy_is_false_when_nothing_listens():
    assert not state.healthy(state.free_port(0))


def test_wait_until_polls_until_true_or_timeout():
    now = [0.0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    answers = iter([False, False, True])
    assert state.wait_until(lambda: next(answers), 10, sleep=sleep, clock=lambda: now[0])
    assert now[0] == 2.0
    assert not state.wait_until(lambda: False, 3, sleep=sleep, clock=lambda: now[0])
