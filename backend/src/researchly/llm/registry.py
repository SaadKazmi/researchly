"""Provider registry (spec §7.1–7.3). The only module that imports litellm (AGENTS.md, spec §19.7)."""

import os

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"  # never fetch the cost map over the network

from collections.abc import Mapping, Sequence  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from typing import Literal  # noqa: E402

import litellm  # noqa: E402
from langchain_core.language_models import BaseChatModel  # noqa: E402
from langchain_litellm import ChatLiteLLM  # noqa: E402
from pydantic import Field  # noqa: E402

from researchly.config import Settings  # noqa: E402
from researchly.llm.capabilities import CapabilityTable, ModelOverride, load_table  # noqa: E402
from researchly.security.keychain import get_secret  # noqa: E402

Role = Literal["strong", "mid", "cheap", "critic"]


litellm.suppress_debug_info = True  # no "Provider List" banner on stdout


class _KeyedChatLiteLLM(ChatLiteLLM):
    """ChatLiteLLM whose key never reaches model_dump() (no keys in state/checkpoints/traces)."""

    api_key: str | None = Field(default=None, repr=False, exclude=True)


class NoModelForRole(LookupError):
    pass


class ModelInfo(ModelOverride):
    pass


@dataclass(frozen=True)
class ProviderSpec:
    provider: str
    key_ref: str | None = None
    base_url: str | None = None
    models: Mapping[str, str] = field(default_factory=dict)


def resolve_model(role: Role, providers: Sequence[ProviderSpec], table: CapabilityTable) -> tuple[ProviderSpec, str]:
    if not providers:
        raise NoModelForRole("no LLM provider configured; add one in Settings")
    spec, lookup = providers[0], role
    if role == "critic" and "critic" not in spec.models:
        others = [p for p in providers[1:] if p.provider != spec.provider]
        spec, lookup = (others[0] if others else spec), "strong"
    model = spec.models.get(lookup) or table.roles.get(spec.provider, {}).get(lookup)
    if model is None:
        raise NoModelForRole(f"{spec.provider} has no model for role {role!r}; set one in Settings")
    return spec, model


def model_info(model: str, table: CapabilityTable | None = None) -> ModelInfo:
    try:
        raw = litellm.get_model_info(model)
    except Exception:  # unknown to LiteLLM's map: overrides only
        raw = {}
    base = ModelInfo(
        input_per_mtok=(raw.get("input_cost_per_token") or 0) * 1e6 or None,
        cached_per_mtok=(raw.get("cache_read_input_token_cost") or 0) * 1e6 or None,
        output_per_mtok=(raw.get("output_cost_per_token") or 0) * 1e6 or None,
        context_window=raw.get("max_input_tokens"),
        max_output=raw.get("max_output_tokens"),
        tools=raw.get("supports_function_calling"),
        json_schema=raw.get("supports_response_schema"),
        vision=raw.get("supports_vision"),
        prompt_cache=raw.get("supports_prompt_caching"),
    )
    override = (table or load_table()).models.get(model)
    return base.model_copy(update=override.model_dump(exclude_none=True)) if override else base


def cost_usd(model: str, in_tok: int, cached_tok: int, out_tok: int, table: CapabilityTable | None = None) -> float:
    info = model_info(model, table)
    input_price = info.input_per_mtok or 0.0
    cached_price = info.cached_per_mtok if info.cached_per_mtok is not None else input_price
    return (
        (in_tok - cached_tok) * input_price + cached_tok * cached_price + out_tok * (info.output_per_mtok or 0.0)
    ) / 1e6


def chat_model(
    role: Role, providers: Sequence[ProviderSpec], settings: Settings, table: CapabilityTable | None = None
) -> BaseChatModel:
    if settings.llm_fixtures == "replay":
        from researchly.llm.record_replay import RecordReplayChatModel, fixtures_path

        return RecordReplayChatModel(
            mode="replay",
            path=fixtures_path(settings),
            role=role,
            model_alias=f"claude-code/{settings.dev_provider_model}",
        )
    if settings.env == "development" and settings.dev_provider == "claude-code":
        from researchly.llm.dev.claude_code import ClaudeCodeChatModel  # absent from release wheels

        dev = ClaudeCodeChatModel(model=settings.dev_provider_model)
        if settings.llm_fixtures == "record":
            from researchly.llm.record_replay import RecordReplayChatModel, fixtures_path

            return RecordReplayChatModel(
                inner=dev,
                mode="record",
                path=fixtures_path(settings),
                role=role,
                model_alias=f"claude-code/{settings.dev_provider_model}",
            )
        return dev
    if settings.llm_fixtures == "record":
        raise ValueError("recording fixtures needs RESEARCHLY_ENV=development and RESEARCHLY_DEV_PROVIDER=claude-code")
    table = table or load_table()
    spec, name = resolve_model(role, providers, table)
    api_key = get_secret(spec.key_ref) if spec.key_ref else None  # fetched per construction, never stored
    window = model_info(name, table).context_window
    return _KeyedChatLiteLLM(
        model=name,
        api_key=api_key,
        api_base=spec.base_url,
        profile={"max_input_tokens": window} if window else None,  # Deep Agents summarises against it (§6.6)
    )
