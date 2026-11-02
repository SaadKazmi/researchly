"""Native runtime (spec §4.1, §4.2): pg0-embedded PostgreSQL 18 plus a supervised app process."""

import contextlib
import hashlib
import os
import sys
from pathlib import Path

from pg0 import Pg0
from researchly.db.provision import setup as setup_database

from researchly_cli import searxng, state
from researchly_cli.supervise import spawn, supervisor_cmd, terminate_tree

PG_PORT = 54329  # spec §16.1 default; another free port when it is taken (the actual one is read back from info)
APP_PORT = 8765


class NativeStartError(RuntimeError):
    pass


class NativePostgres:
    """pg0 runs PostgreSQL as a detached daemon, so stop() is explicit. Never call Pg0.drop(): it deletes the data."""

    def __init__(self, data_dir: Path, password: str) -> None:
        pgdata = (data_dir / "pg" / "18" / "native").resolve()
        try:
            pgdata.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise NativeStartError(
                f"cannot create {pgdata} ({exc.strerror}); it may be owned by root from a Docker-mode run"
            ) from exc
        name = "researchly-" + hashlib.sha256(str(pgdata).encode()).hexdigest()[:8]  # one pg0 instance per data dir
        self._password = password
        # pg0 exposes no path for its instance dir, where instance.json keeps the superuser password
        self.instance_dir = Path.home() / ".pg0" / "instances" / name
        # pg0 stores the superuser password and passes it on its start argv: accepted, mitigated (ADR 0002)
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
        if sys.platform != "win32":
            self.instance_dir.chmod(0o700)
        if info.port is None:
            raise NativeStartError("pg0 reported no port for the running instance")
        return f"postgresql+psycopg://postgres:{self._password}@127.0.0.1:{info.port}/postgres", info.port

    def stop(self) -> None:
        self.pg.stop()


def start(secrets: state.Secrets, searxng_home: Path | None = None) -> state.State:
    pg = NativePostgres(state.data_dir(), secrets.pg_superuser)
    supervisor = None
    sx_supervisor = None
    try:
        superuser_url, pg_port = pg.start()
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
        sx_port = None
        if searxng_home is not None:  # web search is optional: the app runs without it
            sx_port = state.free_port(searxng.PORT)
            sx_env = searxng.env(searxng_home, searxng_home / "settings.yml", secrets.searxng)
            sx_cmd = supervisor_cmd(logs / "searxng.log", searxng.command(searxng_home, sx_port))
            sx_supervisor = spawn(sx_cmd, logs / "supervisor.log", sx_env)
        run = state.State(
            mode="native",
            port=port,
            supervisor_pid=supervisor.pid,
            supervisor_created=supervisor.create_time(),
            pg_port=pg_port,
            searxng_port=sx_port,
            searxng_pid=sx_supervisor.pid if sx_supervisor else None,
            searxng_created=sx_supervisor.create_time() if sx_supervisor else None,
        )
        if not state.wait_until(lambda: state.healthy(port), 60):
            raise NativeStartError(f"the app did not become healthy within 60 s; see {logs / 'app.log'}")
        return run
    except BaseException as exc:  # a failed or interrupted start leaves nothing running
        for sup in (supervisor, sx_supervisor):
            with contextlib.suppress(Exception):
                if sup is not None:
                    terminate_tree(sup.pid, sup.create_time())
        with contextlib.suppress(Exception):
            pg.stop()
        if isinstance(exc, Exception) and not isinstance(exc, NativeStartError):
            # type and first line only: later lines can carry SQL, and the CLI prints this message
            raise NativeStartError(f"{type(exc).__name__}: {next(iter(str(exc).splitlines()), '')}") from exc
        raise


def stop(run: state.State, secrets: state.Secrets) -> None:
    if run.supervisor_pid is not None and run.supervisor_created is not None:  # a PID alone may have been reused
        terminate_tree(run.supervisor_pid, run.supervisor_created)
    if run.searxng_pid is not None and run.searxng_created is not None:
        terminate_tree(run.searxng_pid, run.searxng_created)
    NativePostgres(state.data_dir(), secrets.pg_superuser).stop()
