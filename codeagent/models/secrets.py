"""Secret resolution for model clients."""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
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
    def __init__(
        self,
        *,
        process_env: Mapping[str, str] | None = None,
        user_env_reader: Callable[[str], str | None] | None = None,
    ) -> None:
        self.process_env = os.environ if process_env is None else process_env
        self.user_env_reader = user_env_reader or _read_user_environment_variable

    def resolve(self, env_var: str) -> ResolvedSecret:
        if not _is_valid_env_var_name(env_var):
            raise MissingModelSecretError(
                "Invalid model API key environment variable name. "
                "Use a name such as OPENROUTER_API_KEY."
            )
        value = self.process_env.get(env_var) or self.user_env_reader(env_var)
        if not value:
            raise MissingModelSecretError(
                f"Missing model API key. Set environment variable {env_var}."
            )
        return ResolvedSecret(env_var=env_var, value=value)


def _is_valid_env_var_name(value: str) -> bool:
    return re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value) is not None


def _read_user_environment_variable(env_var: str) -> str | None:
    if os.name != "nt":
        return None
    try:
        import winreg
    except ImportError:  # pragma: no cover - non-Windows Python builds
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _value_type = winreg.QueryValueEx(key, env_var)
    except OSError:
        return None
    return value if isinstance(value, str) and value else None
