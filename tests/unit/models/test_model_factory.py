from __future__ import annotations

import pytest

from codeagent.config import defaults
from codeagent.config.schema import ModelConfig
from codeagent.models.factory import ModelClientFactory
from codeagent.models.secrets import MissingModelSecretError, SecretResolver


class FakeChatOpenAI:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def test_secret_resolver_reads_env_and_redacts_repr(monkeypatch) -> None:
    monkeypatch.setenv("CODEAGENT_TEST_KEY", "sk-test-secret")

    secret = SecretResolver().resolve("CODEAGENT_TEST_KEY")

    assert secret.value == "sk-test-secret"
    assert secret.env_var == "CODEAGENT_TEST_KEY"
    assert "sk-test-secret" not in repr(secret)
    assert "CODEAGENT_TEST_KEY" in repr(secret)
    assert secret.to_record() == {
        "env_var": "CODEAGENT_TEST_KEY",
        "value": "<redacted>",
    }


def test_secret_resolver_reads_user_environment_when_process_env_is_missing() -> None:
    resolver = SecretResolver(
        process_env={},
        user_env_reader=lambda name: "sk-user-secret"
        if name == "OPENROUTER_API_KEY"
        else None,
    )

    secret = resolver.resolve("OPENROUTER_API_KEY")

    assert secret.value == "sk-user-secret"
    assert secret.env_var == "OPENROUTER_API_KEY"
    assert "sk-user-secret" not in repr(secret)


def test_secret_resolver_missing_key_has_clear_redacted_error(monkeypatch) -> None:
    monkeypatch.delenv("CODEAGENT_MISSING_KEY", raising=False)

    with pytest.raises(MissingModelSecretError) as exc_info:
        SecretResolver().resolve("CODEAGENT_MISSING_KEY")

    message = str(exc_info.value)
    assert "CODEAGENT_MISSING_KEY" in message
    assert "API key" in message
    assert "sk-" not in message


def test_secret_resolver_does_not_echo_secret_like_env_var_names(monkeypatch) -> None:
    monkeypatch.delenv("sk-live-secret-value", raising=False)

    with pytest.raises(MissingModelSecretError) as exc_info:
        SecretResolver().resolve("sk-live-secret-value")

    message = str(exc_info.value)
    assert "sk-live-secret-value" not in message
    assert "environment variable name" in message


def test_model_factory_maps_model_config_to_chat_openai(monkeypatch) -> None:
    monkeypatch.setenv("CODEAGENT_TEST_KEY", "sk-test")
    monkeypatch.setattr("codeagent.models.factory.ChatOpenAI", FakeChatOpenAI)
    config = ModelConfig(
        provider="openai_compatible",
        model_name="anthropic/claude-opus-4.8",
        base_url="https://openrouter.ai/api/v1",
        api_key_env="CODEAGENT_TEST_KEY",
        temperature=0.1,
        timeout_seconds=33,
        max_retries=4,
        max_tokens=4096,
    )

    model = ModelClientFactory(secret_resolver=SecretResolver()).create(config)

    assert isinstance(model, FakeChatOpenAI)
    assert model.kwargs["model"] == "anthropic/claude-opus-4.8"
    assert model.kwargs["base_url"] == "https://openrouter.ai/api/v1"
    assert model.kwargs["api_key"] == "sk-test"
    assert model.kwargs["temperature"] == 0.1
    assert model.kwargs["timeout"] == 33
    assert model.kwargs["max_retries"] == 4
    assert model.kwargs["max_tokens"] == 4096


def test_model_factory_sets_default_max_tokens_for_openrouter_budgeting(
    monkeypatch,
) -> None:
    monkeypatch.setenv("CODEAGENT_TEST_KEY", "sk-test")
    monkeypatch.setattr("codeagent.models.factory.ChatOpenAI", FakeChatOpenAI)
    config = ModelConfig(api_key_env="CODEAGENT_TEST_KEY")

    model = ModelClientFactory(secret_resolver=SecretResolver()).create(config)

    assert model.kwargs["max_tokens"] == defaults.DEFAULT_MODEL_MAX_TOKENS


def test_model_factory_rejects_unsupported_provider(monkeypatch) -> None:
    monkeypatch.setenv("CODEAGENT_TEST_KEY", "sk-test")
    config = ModelConfig(provider="native_anthropic", api_key_env="CODEAGENT_TEST_KEY")

    with pytest.raises(ValueError, match="Unsupported model provider"):
        ModelClientFactory().create(config)
