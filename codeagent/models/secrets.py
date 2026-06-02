"""Secret resolution for model clients."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass


class MissingModelSecretError(RuntimeError):
    """Raised when a required model API key is unavailable."""


@dataclass(frozen=True)
class ResolvedSecret:
    env_var: str
    value: str

    def __repr__(self) -> str:
        return f"ResolvedSecret(env_var={self.env_var!r}, value='<redacted>')"

    def to_record(self) -> dict[str, str]:
        return {"env_var": self.env_var, "value": "<redacted>"}


class SecretResolver:
    def resolve(self, env_var: str) -> ResolvedSecret:
        if not _is_valid_env_var_name(env_var):
            raise MissingModelSecretError(
                "Invalid model API key environment variable name. "
                "Use a name such as OPENROUTER_API_KEY."
            )
        value = os.environ.get(env_var)
        if not value:
            raise MissingModelSecretError(
                f"Missing model API key. Set environment variable {env_var}."
            )
        return ResolvedSecret(env_var=env_var, value=value)


def _is_valid_env_var_name(value: str) -> bool:
    return re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value) is not None
