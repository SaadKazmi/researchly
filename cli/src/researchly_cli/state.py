"""The CLI's local state (spec §4.2, §16.1): data and log dirs, config.json, state.json, secrets, ports, health."""

import json
import os
import socket
import time
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import platformdirs
from researchly.config import Settings
from researchly.security.keychain import ensure_local_token, ensure_secret

RUNTIMES = ("auto", "docker", "native")
COMPOSE_CLIS = ("docker", "podman", "docker-compose", "podman-compose")


def data_dir() -> Path:
    return Settings().data_dir


def log_dir() -> Path:
    override = os.environ.get("RESEARCHLY_LOG_DIR")
    path = Path(override) if override else platformdirs.user_log_path("researchly", appauthor=False)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_json(name: str) -> Any:
    """The parsed file in the data dir; None when it is missing or not JSON (a hand edit, a crash mid-write)."""
    try:
        return json.loads((data_dir() / name).read_text("utf-8"))
    except (OSError, ValueError):
        return None


def load_config() -> dict[str, str]:
    raw = _read_json("config.json")
    stored = raw if isinstance(raw, dict) else {}
    return {"runtime": "auto", **{k: v for k, v in stored.items() if isinstance(v, str)}}


def save_config(config: dict[str, str]) -> None:
    path = data_dir() / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2), "utf-8")


@dataclass(frozen=True)
class State:
    """What `start` launched, so `stop` and `status` can find it. Never holds secrets."""

    mode: Literal["docker", "native"]
    port: int
    compose: tuple[str, ...] = ()
    supervisor_pid: int | None = None
    supervisor_created: float | None = None
    pg_port: int | None = None
    searxng_port: int | None = None
    searxng_pid: int | None = None
    searxng_created: float | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def _port(value: object) -> bool:
    return type(value) is int and 1 <= value <= 65535


def read_state() -> State | None:
    """The recorded run; None when there is none or state.json is corrupt or invalid. Unknown keys are dropped."""
    raw = _read_json("state.json")
    if not isinstance(raw, dict):
        return None
    keys = (
        "compose",
        "supervisor_pid",
        "supervisor_created",
        "pg_port",
        "searxng_port",
        "searxng_pid",
        "searxng_created",
    )
    compose, pid, created, pg_port, sx_port, sx_pid, sx_created = (raw.get(k) for k in keys)
    compose = compose or []
    valid = (
        raw.get("mode") in ("native", "docker")
        and _port(raw.get("port"))
        and isinstance(compose, list)
        and all(isinstance(c, str) for c in compose)
        and (not compose or compose[0] in COMPOSE_CLIS)  # the argv `stop` will run
        and (pg_port is None or _port(pg_port))
        and (pid is None or (type(pid) is int and type(created) in (int, float)))  # the PID alone could be reused
        and (created is None or type(created) in (int, float))
        and (sx_port is None or _port(sx_port))
        and (sx_pid is None or (type(sx_pid) is int and type(sx_created) in (int, float)))
        and (sx_created is None or type(sx_created) in (int, float))
    )
    if not valid:
        return None
    return State(raw["mode"], raw["port"], tuple(compose), pid, created, pg_port, sx_port, sx_pid, sx_created)


def write_state(state: State) -> None:
    path = data_dir() / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(state), indent=2), "utf-8")


def clear_state() -> None:
    (data_dir() / "state.json").unlink(missing_ok=True)


@dataclass(frozen=True)
class Secrets:
    pg_superuser: str
    db_app: str
    db_ro: str
    local_token: str
    searxng: str


def load_secrets() -> Secrets:
    """Generated on first use and kept in the OS keychain (spec §16.1, §19.2)."""
    return Secrets(
        pg_superuser=ensure_secret("pg-superuser-password"),
        db_app=ensure_secret("db-app-password"),
        db_ro=ensure_secret("db-ro-password"),
        local_token=ensure_local_token(),
        searxng=ensure_secret("searxng-secret"),
    )


def free_port(preferred: int) -> int:
    """`preferred` if it is free on 127.0.0.1, else any free port."""
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", preferred))
        except OSError:
            s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:  # noqa: S310 - fixed local URL
            return response.status == 200
    except OSError:
        return False


def wait_until(
    check: Callable[[], bool],
    timeout: float,
    interval: float = 1.0,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    deadline = clock() + timeout
    while not check():
        if clock() >= deadline:
            return False
        sleep(interval)
    return True
