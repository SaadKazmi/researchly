"""The CLI's local state (spec §4.2, §16.1): data and log dirs, config.json, state.json, secrets, ports, health."""

import json
import os
import socket
import time
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import platformdirs
from researchly.config import Settings
from researchly.security.keychain import ensure_local_token, ensure_secret

RUNTIMES = ("auto", "docker", "native")


def data_dir() -> Path:
    return Settings().data_dir


def log_dir() -> Path:
    override = os.environ.get("RESEARCHLY_LOG_DIR")
    path = Path(override) if override else platformdirs.user_log_path("researchly", appauthor=False)
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_config() -> dict[str, str]:
    path = data_dir() / "config.json"
    return {"runtime": "auto", **(json.loads(path.read_text("utf-8")) if path.exists() else {})}


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

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def read_state() -> State | None:
    path = data_dir() / "state.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text("utf-8"))
    return State(**{**raw, "compose": tuple(raw.get("compose", ()))})


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
