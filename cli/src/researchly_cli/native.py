"""Native runtime (spec §4.1, §4.2): pg0-embedded PostgreSQL 18 plus a supervised app process."""

import hashlib
import os
import sys
from pathlib import Path

from pg0 import Pg0
from researchly.db.provision import setup as setup_database

from researchly_cli import state
from researchly_cli.supervise import spawn, supervisor_cmd, terminate_tree

PG_PORT = 54329  # spec §16.1 default; another free port when it is taken (the actual one is read back from info)
APP_PORT = 8765


class NativeStartError(RuntimeError):
    pass


class NativePostgres:
    """pg0 runs PostgreSQL as a detached daemon, so stop() is explicit. Never call Pg0.drop(): it deletes the data."""

    def __init__(self, data_dir: Path, password: str) -> None:
        pgdata = (data_dir / "pg" / "18" / "native").resolve()
        pgdata.mkdir(parents=True, exist_ok=True)
        name = "researchly-" + hashlib.sha256(str(pgdata).encode()).hexdigest()[:8]  # one pg0 instance per data dir
        self._password = password
        self.pg = Pg0(
            name=name,
            port=state.free_port(PG_PORT),  # ignored when the instance is already running: start() reads info()
            username="postgres",
            password=password,
            database="postgres",
            data_dir=str(pgdata),
        )

    def start(self) -> tuple[str, int]:
        """Start the server, or reuse it if it is already running. Returns (superuser URL, port)."""
        info = self.pg.info() if self.pg.running else self.pg.start()
        if info.port is None:
            raise NativeStartError("pg0 reported no port for the running instance")
        return f"postgresql+psycopg://postgres:{self._password}@127.0.0.1:{info.port}/postgres", info.port

    def stop(self) -> None:
        self.pg.stop()


def start(secrets: state.Secrets) -> state.State:
    superuser_url, pg_port = NativePostgres(state.data_dir(), secrets.pg_superuser).start()
    app_url = setup_database(superuser_url, secrets.db_app, secrets.db_ro)
    port = state.free_port(APP_PORT)
    uvicorn = [
        sys.executable,
        "-m",
        "uvicorn",
        "--factory",
        "researchly.app:create_app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    env = {**os.environ, "RESEARCHLY_DATABASE_URL": app_url, "RESEARCHLY_LOCAL_TOKEN": secrets.local_token}
    logs = state.log_dir()
    supervisor = spawn(supervisor_cmd(logs / "app.log", uvicorn), logs / "supervisor.log", env)
    run = state.State(
        mode="native",
        port=port,
        supervisor_pid=supervisor.pid,
        supervisor_created=supervisor.create_time(),
        pg_port=pg_port,
    )
    if not state.wait_until(lambda: state.healthy(port), 60):
        stop(run, secrets)
        raise NativeStartError(f"the app did not become healthy within 60 s; see {logs / 'app.log'}")
    return run


def stop(run: state.State, secrets: state.Secrets) -> None:
    if run.supervisor_pid is not None:
        terminate_tree(run.supervisor_pid, run.supervisor_created)
    NativePostgres(state.data_dir(), secrets.pg_superuser).stop()
