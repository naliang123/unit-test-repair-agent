"""Environment-backed runtime configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


class ConfigError(ValueError):
    pass


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class Settings:
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    max_steps: int = 12
    test_timeout: int = 45
    max_tool_output_chars: int = 6000
    verify_all: bool = True
    approve_edits: bool = False
    input_price_per_million: float = 0.0
    output_price_per_million: float = 0.0

    @classmethod
    def from_env(cls, require_key: bool = True) -> "Settings":
        load_dotenv()
        defaults = cls()
        settings = cls(
            api_key=os.getenv("LLM_API_KEY", os.getenv("DEEPSEEK_API_KEY", "")),
            base_url=os.getenv("LLM_BASE_URL", defaults.base_url),
            model=os.getenv("MODEL_ID", defaults.model),
            max_steps=int(os.getenv("MAX_STEPS", str(defaults.max_steps))),
            test_timeout=int(os.getenv("TEST_TIMEOUT_SECONDS", str(defaults.test_timeout))),
            max_tool_output_chars=int(
                os.getenv("MAX_TOOL_OUTPUT_CHARS", str(defaults.max_tool_output_chars))
            ),
            verify_all=_as_bool(os.getenv("VERIFY_ALL_TESTS"), True),
            approve_edits=_as_bool(os.getenv("APPROVE_EDITS"), False),
            input_price_per_million=float(os.getenv("INPUT_PRICE_PER_MILLION", "0")),
            output_price_per_million=float(os.getenv("OUTPUT_PRICE_PER_MILLION", "0")),
        )
        if require_key and not settings.api_key:
            raise ConfigError("Missing LLM_API_KEY. Copy .env.example to .env first.")
        if settings.max_steps < 1 or settings.test_timeout < 1:
            raise ConfigError("MAX_STEPS and TEST_TIMEOUT_SECONDS must be positive.")
        return settings
