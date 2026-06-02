"""OpenAI-compatible chat model factory."""

from __future__ import annotations

from typing import Any

from langchain_openai import ChatOpenAI

from codeagent.config.schema import ModelConfig
from codeagent.models.secrets import SecretResolver


class ModelClientFactory:
    def __init__(self, *, secret_resolver: SecretResolver | None = None) -> None:
        self.secret_resolver = secret_resolver or SecretResolver()

    def create(self, config: ModelConfig) -> Any:
        if config.provider != "openai_compatible":
            raise ValueError(f"Unsupported model provider: {config.provider}")
        secret = self.secret_resolver.resolve(config.api_key_env)
        kwargs: dict[str, Any] = {
            "model": config.model_name,
            "base_url": config.base_url,
            "api_key": secret.value,
            "temperature": config.temperature,
            "timeout": config.timeout_seconds,
            "max_retries": config.max_retries,
        }
        if config.max_tokens is not None:
            kwargs["max_tokens"] = config.max_tokens
        return ChatOpenAI(**kwargs)
