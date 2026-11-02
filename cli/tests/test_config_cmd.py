from typer.testing import CliRunner

from researchly_cli.main import app

runner = CliRunner()


def test_runtime_defaults_to_auto():
    result = runner.invoke(app, ["config", "get", "runtime"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "auto"


def test_set_runtime_persists():
    assert runner.invoke(app, ["config", "set", "runtime", "native"]).exit_code == 0
    assert runner.invoke(app, ["config", "get", "runtime"]).stdout.strip() == "native"


def test_rejects_unknown_runtime_and_key():
    assert runner.invoke(app, ["config", "set", "runtime", "kubernetes"]).exit_code == 2
    assert runner.invoke(app, ["config", "get", "colour"]).exit_code == 2
