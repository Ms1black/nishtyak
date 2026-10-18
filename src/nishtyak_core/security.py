from __future__ import annotations

from .models import Finding, ProjectSnapshot
from .redaction import TextRedactor


class SecretScanner(TextRedactor):
    def scan(self, snapshot: ProjectSnapshot) -> tuple[Finding, ...]:
        findings = []
        for file in snapshot.files:
            text = file.content.decode("utf-8", errors="replace")
            matches = (pattern.search(text) for pattern in self.patterns)
            first = min(
                (match for match in matches if match is not None),
                key=lambda match: match.start(),
                default=None,
            )
            if first is not None:
                findings.append(
                    Finding(
                        "SECRET_DETECTED",
                        "Potential credential found; clean or explicitly exclude this file",
                        path=file.path,
                        line=text.count("\n", 0, first.start()) + 1,
                        severity="error",
                    )
                )
        return tuple(findings)
