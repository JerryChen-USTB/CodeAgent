"""Shared redaction helpers for model/provider error text."""

from __future__ import annotations

import re


def redact_sensitive_text(text: str) -> str:
    """Remove secrets and account-linked provider identifiers from audit text."""
    redacted = re.sub(r"sk(?:-or)?-[A-Za-z0-9_-]+", "<redacted>", text)
    redacted = re.sub(
        r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+",
        "Bearer <redacted>",
        redacted,
    )
    redacted = re.sub(
        r"(?i)\b(api[_-]?key|authorization|token|secret|password)"
        r"(\s*[:=]\s*)"
        r"([^\s,;]+)",
        r"\1\2<redacted>",
        redacted,
    )
    redacted = re.sub(
        r"https://openrouter\.ai/workspaces/[^\s'\"),}]+",
        "https://openrouter.ai/workspaces/<redacted>",
        redacted,
    )
    redacted = re.sub(r"\buser_[A-Za-z0-9]{8,}\b", "user_<redacted>", redacted)
    return redacted
