"""`researchly` command line (spec F10.1, §4)."""

import typer

from researchly_cli import state

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
