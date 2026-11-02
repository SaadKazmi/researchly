"""`researchly` command line (spec F10.1, §4)."""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

import typer
from researchly.app import WEB_DIR
from researchly.security.keychain import KeychainUnavailable, store_name

from researchly_cli import docker, engines, native, state

DEGRADED_NATIVE = (
    "keyword search uses tsvector (pg_textsearch BM25 needs Docker mode)",
    "web search is not available natively yet (native SearXNG spike, plan 1e)",
    "GROBID reference parsing needs Docker mode",
)
LICENCE_NOTE = (
    "Note: Docker Desktop needs a paid subscription for companies with 250+ employees or $10M+ revenue. "
    "Researchly never requires it; any engine or native mode works."
)

BUILD_HINT = "run `pnpm -C frontend install` and `pnpm -C frontend build`"
FRONTEND_DIR = Path(__file__).resolve().parents[3] / "frontend"  # source checkouts only; release wheels ship the build

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Researchly: a local-first research assistant.")
config_app = typer.Typer(no_args_is_help=True, help="Read or change CLI settings.")
app.add_typer(config_app, name="config")

_CHOICES = {"runtime": state.RUNTIMES}


@app.callback()
def main() -> None:
    """Researchly: a local-first research assistant."""


def _known(key: str) -> tuple[str, ...]:
    if key not in _CHOICES:
        raise typer.BadParameter(f"unknown key {key!r}; known: {', '.join(_CHOICES)}")
    return _CHOICES[key]


@config_app.command("get")
def config_get(key: str) -> None:
    """Print a setting."""
    _known(key)
    typer.echo(state.load_config()[key])


@config_app.command("set")
def config_set(key: str, value: str) -> None:
    """Change a setting, e.g. `researchly config set runtime docker`."""
    if value not in _known(key):
        raise typer.BadParameter(f"{key} must be one of {', '.join(_known(key))}")
    state.save_config({**state.load_config(), key: value})
    typer.echo(f"{key} = {value}")


def _fail(message: str) -> NoReturn:
    typer.secho(message, err=True, fg=typer.colors.RED)
    raise typer.Exit(1)


def _select() -> engines.Selection:
    pin = state.load_config()["runtime"]
    engine = None if pin == "native" else engines.detect()
    try:
        return engines.select_runtime(
            pin,
            engine,
            ready=engines.ready,
            launch=engines.launch,
            wait=lambda check, timeout: state.wait_until(check, timeout, 2.0),
            echo=typer.echo,
        )
    except engines.RuntimeUnavailable as exc:
        _fail(str(exc))


def _announce(selection: engines.Selection) -> None:
    typer.echo(f"runtime: {selection.mode} ({selection.reason})")
    if selection.engine is not None and selection.engine.name == "docker-desktop":
        config = state.load_config()
        if not config.get("docker_desktop_note"):
            typer.echo(LICENCE_NOTE)
            state.save_config({**config, "docker_desktop_note": "shown"})


def _warn_on_runtime_switch(mode: str) -> None:
    config = state.load_config()
    last = config.get("last_mode")
    if config["runtime"] == "auto" and last and last != mode:
        typer.secho(
            f"WARNING: starting in {mode} mode, but the last run used {last} mode. Each mode keeps a separate "
            f"database, so your {last}-mode data will not be here. To keep using it, stop and run "
            f"`researchly config set runtime {last}`.",
            err=True,
            fg=typer.colors.YELLOW,
            bold=True,
        )


def _login_url(run: state.State, secrets: state.Secrets) -> str:
    """The one-time link: opening it sets the browser's auth cookie (ADR 0003). Only `start` prints it."""
    return f"{run.url}/?token={secrets.local_token}"


def _ensure_web_ui() -> None:
    """Native mode serves the SPA from the backend package; a source checkout builds it on first start."""
    # on Windows an extensionless `pnpm` shell shim sits beside pnpm.cmd and is not a Win32 executable
    pnpm = shutil.which("pnpm.cmd" if sys.platform == "win32" else "pnpm")
    if not WEB_DIR.is_dir() and pnpm and (FRONTEND_DIR / "package.json").is_file():
        typer.echo("Building the web UI (first start only)...")
        for args in (["install", "--frozen-lockfile"], ["build"]):
            if subprocess.run([pnpm, "-C", str(FRONTEND_DIR), *args], check=False).returncode:  # noqa: S603 - fixed argv
                break
    if not WEB_DIR.is_dir():
        typer.secho(f"warning: the web UI is not built; {BUILD_HINT}", err=True)


def _secrets() -> state.Secrets:
    try:
        return state.load_secrets()
    except KeychainUnavailable as exc:
        _fail(str(exc))


def _stop(run: state.State, secrets: state.Secrets) -> str | None:
    """Stop `run`; the error message if that failed."""
    try:
        (docker.stop if run.mode == "docker" else native.stop)(run, secrets)
    except (docker.DockerStartError, native.NativeStartError) as exc:
        return str(exc)
    return None


@app.command()
def setup() -> None:
    """Choose the runtime (starting a container engine if needed) and prepare it."""
    selection = _select()
    _announce(selection)
    secrets = _secrets()
    if selection.mode == "docker" and selection.engine is not None:
        try:
            docker.build(secrets, selection.engine.compose)
        except docker.DockerStartError as exc:
            _fail(str(exc))
    typer.echo(f"secrets store: {store_name()}")
    typer.echo("Ready. Run `researchly start`.")


@app.command()
def start() -> None:
    """Start Researchly in the best available runtime and print its URL."""
    current = state.read_state()
    secrets = _secrets()
    if current is not None:
        if state.healthy(current.port):
            typer.echo(f"Already running ({current.mode}) at {_login_url(current, secrets)}")
            return
        if (error := _stop(current, secrets)) is not None:  # leftovers of a crashed run
            typer.secho(f"warning: could not stop the previous {current.mode} run ({error}); starting anyway", err=True)
        state.clear_state()
    selection = _select()
    _announce(selection)
    _warn_on_runtime_switch(selection.mode)
    if selection.mode == "native":
        _ensure_web_ui()  # the Docker image builds its own copy
    try:
        if selection.mode == "docker" and selection.engine is not None:
            run = docker.start(secrets, selection.engine.compose)
        else:
            run = native.start(secrets)
    except (docker.DockerStartError, native.NativeStartError) as exc:
        _fail(str(exc))
    state.write_state(run)
    state.save_config({**state.load_config(), "last_mode": run.mode})
    typer.echo(f"Researchly is running at {_login_url(run, secrets)}")


@app.command()
def stop() -> None:
    """Stop Researchly. Data is kept."""
    current = state.read_state()
    if current is None:
        typer.echo("Researchly is not running.")
        return
    if (error := _stop(current, _secrets())) is not None:
        _fail(error)
    state.clear_state()
    typer.echo("Stopped.")


@app.command()
def status() -> None:
    """Show whether Researchly is running; exits 1 when it is not healthy."""
    current = state.read_state()
    if current is None:
        typer.echo("stopped")
        raise typer.Exit(1)
    ok = state.healthy(current.port)
    typer.echo(f"{current.mode}: {'running' if ok else 'unhealthy'} at {current.url}")
    if not ok:
        raise typer.Exit(1)


@app.command()
def doctor() -> None:
    """Report runtime, container engine, secrets store, paths and degraded capabilities."""
    config = state.load_config()
    engine = engines.detect()
    typer.echo(f"runtime setting: {config['runtime']}")
    if engine is None:
        typer.echo("container engine: none found")
    else:
        ready = engines.ready(engine)
        detail = "ready" if ready else "stopped"
        typer.echo(f"container engine: {engine.name} ({detail}{'' if engine.compose else ', no compose support'})")
        if not ready and os.environ.get("DOCKER_HOST"):
            typer.echo(
                "note: DOCKER_HOST is set, so the engine is probed at that address; unset it to use the local one"
            )
    typer.echo(f"secrets store: {store_name()}")
    typer.echo(f"data dir: {state.data_dir()}")
    typer.echo(f"logs: {state.log_dir()}")
    typer.echo(f"web ui: {'built' if WEB_DIR.is_dir() else f'not built (native mode; {BUILD_HINT})'}")
    current = state.read_state()
    if current is None:
        typer.echo("status: stopped")
        mode = "native" if config["runtime"] == "native" or engine is None else "docker"
    else:
        typer.echo(
            f"status: {current.mode} at {current.url} ({'healthy' if state.healthy(current.port) else 'unhealthy'})"
        )
        mode = current.mode
    if mode == "native":
        typer.echo("degraded in native mode:")
        for line in DEGRADED_NATIVE:
            typer.echo(f"  - {line}")
