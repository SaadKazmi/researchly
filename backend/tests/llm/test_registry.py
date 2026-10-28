import pytest
from langchain_litellm import ChatLiteLLM

from researchly.config import Settings
from researchly.llm import registry
from researchly.llm.capabilities import load_table
from researchly.llm.registry import NoModelForRole, ProviderSpec, chat_model, cost_usd, model_info, resolve_model
from researchly.security.keychain import set_secret

T = load_table()
ANTHROPIC = ProviderSpec("anthropic", key_ref="anthropic")
OPENAI = ProviderSpec("openai", key_ref="openai")


def test_resolve_role_defaults_and_overrides():
    assert resolve_model("mid", [ANTHROPIC], T) == (ANTHROPIC, "anthropic/claude-sonnet-5-5")
    custom = ProviderSpec("anthropic", models={"mid": "anthropic/claude-opus-5-5"})
    assert resolve_model("mid", [custom], T)[1] == "anthropic/claude-opus-5-5"


def test_critic_prefers_a_different_provider():
    assert resolve_model("critic", [ANTHROPIC, OPENAI], T) == (OPENAI, "openai/gpt-6-astra")
    assert resolve_model("critic", [ANTHROPIC], T) == (ANTHROPIC, "anthropic/claude-opus-5-5")


def test_missing_role_model_is_explicit():
    with pytest.raises(NoModelForRole):
        resolve_model("strong", [ProviderSpec("ollama")], T)
    with pytest.raises(NoModelForRole):
        resolve_model("strong", [], T)


def test_model_info_applies_overrides():
    haiku = model_info("anthropic/claude-haiku-5-5", T)  # absent from LiteLLM 1.105.1
    assert (haiku.input_per_mtok, haiku.output_per_mtok, haiku.context_window) == (0.10, 0.50, 1_000_000)
    assert model_info("anthropic/claude-sonnet-5-5", T).cached_per_mtok == 0.10


def test_cost_usd_splits_cached_tokens():
    # 600 uncached * $2 + 400 cached * $0.10 + 200 out * $10, per MTok
    assert cost_usd("anthropic/claude-sonnet-5-5", 1000, 400, 200, T) == pytest.approx(0.00324)


def test_chat_model_reads_key_at_construction_and_never_from_settings():
    set_secret("anthropic", "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWX")
    model = chat_model("cheap", [ANTHROPIC], Settings(env="test"), T)
    assert isinstance(model, ChatLiteLLM)
    assert model.model == "anthropic/claude-haiku-5-5"
    assert model.api_key == "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWX"
    assert "sk-ant" not in repr(model)


def test_chat_model_key_never_appears_in_dumps():
    key = "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWX"
    set_secret("anthropic", key)
    model = chat_model("cheap", [ANTHROPIC], Settings(env="test"), T)
    assert isinstance(model, ChatLiteLLM)
    assert key not in str(model.model_dump())  # model_dump_json() can't serialize ChatLiteLLM's client at all
    assert model.api_key == key  # still usable for calls


def test_litellm_cost_map_is_local():
    import os

    assert os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] == "True"
    assert registry.litellm.model_cost  # bundled map loaded without network
