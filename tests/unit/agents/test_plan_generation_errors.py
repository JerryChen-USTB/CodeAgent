from __future__ import annotations

from codeagent.agents.plan_generation import _is_retryable_model_error


def test_openrouter_region_permission_error_is_not_retryable() -> None:
    exc = RuntimeError(
        "Error code: 403 - {'error': {'message': "
        "'This model is not available in your region.', 'code': 403}}"
    )

    assert _is_retryable_model_error(exc) is False


def test_transient_model_error_remains_retryable() -> None:
    exc = RuntimeError("temporary upstream timeout")

    assert _is_retryable_model_error(exc) is True
