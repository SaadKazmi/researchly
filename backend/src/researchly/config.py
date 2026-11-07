"""Runtime settings, read from RESEARCHLY_* environment variables."""

from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RESEARCHLY_")

    env: Literal["production", "development", "test"] = "production"
    database_url: str = "postgresql+psycopg://researchly_app@127.0.0.1:54329/researchly"
    dev_provider: Literal["claude-code"] | None = None
    dev_provider_model: Literal["sonnet", "haiku"] = "sonnet"
    llm_fixtures: Literal["record", "replay", "off"] = "off"
    llm_fixtures_dir: Path = Path("e2e/fixtures/llm")
    llm_fixtures_suite: str = "default"
    data_dir: Path = Path.home() / ".researchly"  # spec §16.1
    local_token: SecretStr | None = None  # injected by the CLI (spec §16.1); else read from the keychain
    searxng_url: str | None = None  # set by the CLI (ADR 0010); unset or empty: web search is off
