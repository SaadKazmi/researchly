import socket

from researchly_cli import state


def test_state_roundtrip():
    run = state.State(mode="docker", port=8765, compose=("docker", "compose"))
    state.write_state(run)
    assert state.read_state() == run
    assert run.url == "http://127.0.0.1:8765"
    state.clear_state()
    assert state.read_state() is None


def test_config_defaults_and_persists():
    assert state.load_config() == {"runtime": "auto"}
    state.save_config({"runtime": "native"})
    assert state.load_config()["runtime"] == "native"


def test_secrets_are_generated_once_and_distinct():
    first = state.load_secrets()
    assert state.load_secrets() == first
    values = [first.pg_superuser, first.db_app, first.db_ro, first.local_token, first.searxng]
    assert len(set(values)) == 5
    assert all(len(v) == 43 for v in values)


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
