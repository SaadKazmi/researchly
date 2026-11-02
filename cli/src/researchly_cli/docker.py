"""Docker runtime (spec §4.1): the compose stack in infra/, driven through the engine's compose command."""

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

from researchly_cli import state

# ponytail: works from a source checkout only; ship the compose file with the release images (step 10)
COMPOSE_FILE = Path(__file__).resolve().parents[3] / "infra" / "compose.yaml"
APP_PORT = 8765


class DockerStartError(RuntimeError):
    pass


def compose_env(secrets: state.Secrets, port: int) -> dict[str, str]:
    """Secrets reach compose only through the environment of the compose process (spec §16.1)."""
    return {
        **os.environ,
        "RESEARCHLY_DATA_DIR": str(state.data_dir().resolve()),
        "RESEARCHLY_PORT": str(port),
        "RESEARCHLY_PG_SUPERUSER_PASSWORD": secrets.pg_superuser,
        "RESEARCHLY_DB_APP_PASSWORD": secrets.db_app,
        "RESEARCHLY_DB_RO_PASSWORD": secrets.db_ro,
        "RESEARCHLY_LOCAL_TOKEN": secrets.local_token,
        "RESEARCHLY_SEARXNG_SECRET": secrets.searxng,
    }


def _compose(compose: Sequence[str], args: Sequence[str], env: dict[str, str]) -> None:
    if not COMPOSE_FILE.is_file():
        raise DockerStartError(
            f"{COMPOSE_FILE} not found: Docker mode needs a source checkout until release images ship"
        )
    cmd = [*compose, "-f", str(COMPOSE_FILE), *args]
    if subprocess.run(cmd, env=env, check=False).returncode != 0:  # noqa: S603 - fixed argv; output streams to the user
        raise DockerStartError(f"`{' '.join(cmd)}` failed")


def build(secrets: state.Secrets, compose: Sequence[str]) -> None:
    _compose(compose, ["build"], compose_env(secrets, APP_PORT))


def start(secrets: state.Secrets, compose: Sequence[str]) -> state.State:
    port = state.free_port(APP_PORT)
    env = compose_env(secrets, port)
    (state.data_dir() / "pg").mkdir(parents=True, exist_ok=True)
    _compose(compose, ["build"], env)
    _compose(compose, ["up", "-d", "--wait", "postgres"], env)
    _compose(compose, ["run", "--rm", "migrate"], env)  # spec §16.1: a failed migration aborts the start
    _compose(compose, ["up", "-d", "--wait", "app", "searxng"], env)
    return state.State(mode="docker", port=port, compose=tuple(compose))


def stop(run: state.State, secrets: state.Secrets) -> None:
    _compose(run.compose, ["down"], compose_env(secrets, run.port))
