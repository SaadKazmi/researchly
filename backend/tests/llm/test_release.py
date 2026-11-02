import shutil
import subprocess
import tomllib
import zipfile
from pathlib import Path

from langchain_litellm import ChatLiteLLM

from researchly.config import Settings
from researchly.llm.dev.claude_code import ClaudeCodeChatModel
from researchly.llm.record_replay import RecordReplayChatModel
from researchly.llm.registry import ProviderSpec, chat_model

BACKEND = Path(__file__).resolve().parents[2]
ANTHROPIC = [ProviderSpec("anthropic")]


def test_dev_provider_only_in_development():
    dev = Settings(env="development", dev_provider="claude-code")
    assert isinstance(chat_model("mid", ANTHROPIC, dev), ClaudeCodeChatModel)
    prod = Settings(env="production", dev_provider="claude-code")
    assert isinstance(chat_model("mid", ANTHROPIC, prod), ChatLiteLLM)


def test_record_mode_wraps_dev_provider(tmp_path):
    s = Settings(env="development", dev_provider="claude-code", llm_fixtures="record", llm_fixtures_dir=tmp_path)
    model = chat_model("cheap", ANTHROPIC, s)
    assert isinstance(model, RecordReplayChatModel) and isinstance(model.inner, ClaudeCodeChatModel)


def test_record_mode_refuses_real_providers(tmp_path):
    import pytest

    with pytest.raises(ValueError, match="RESEARCHLY_DEV_PROVIDER"):
        chat_model("cheap", ANTHROPIC, Settings(env="test", llm_fixtures="record", llm_fixtures_dir=tmp_path))


def test_release_wheel_excludes_dev_provider(tmp_path):
    deps = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    assert not any(d.startswith("claude-agent-sdk") for d in deps)
    uv = shutil.which("uv")
    assert uv, "uv must be on PATH"
    subprocess.run([uv, "build", "--wheel", "--out-dir", str(tmp_path)], cwd=BACKEND, check=True, capture_output=True)  # noqa: S603
    names = zipfile.ZipFile(next(tmp_path.glob("*.whl"))).namelist()
    assert "researchly/llm/registry.py" in names
    assert not [n for n in names if n.startswith("researchly/llm/dev/")]
