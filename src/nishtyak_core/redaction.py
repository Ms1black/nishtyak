from __future__ import annotations

import re
from typing import Any


class TextRedactor:
    patterns = (
        re.compile(r"(?i)https?://[^\s/\"'<>]+@[^\s/\"'<>]+"),
        re.compile(r"\bsk[-_][A-Za-z0-9_-]{16,}\b"),
        re.compile(r"\b(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}\b"),
        re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*"),
        re.compile(
            r"(?i)(?<![a-z0-9])(?:api[_-]?key|access[_-]?token|auth[_-]?token|"
            r"secret|password)\b[\"']?\s*[:=]\s*[\"'][^\"'\r\n]{8,}[\"']"
        ),
        re.compile(
            r"(?m)^(?=[A-Z0-9_]*\s*=)(?=[A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD))"
            r"[A-Z0-9_]+\s*=\s*"
            r"[A-Za-z0-9+/=_-]{8,}(?=\s|$)"
        ),
    )

    def redact(self, text: str) -> str:
        for pattern in self.patterns:
            text = pattern.sub("[REDACTED]", text)
        return text

    def redact_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, dict):
            return {self.redact_value(key): self.redact_value(item) for key, item in value.items()}
        if isinstance(value, tuple):
            return tuple(self.redact_value(item) for item in value)
        if isinstance(value, list):
            return [self.redact_value(item) for item in value]
        return value
