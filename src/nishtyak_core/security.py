from __future__ import annotations

from .models import Finding, ProjectSnapshot
from .redaction import TextRedactor


class SecretScanner(TextRedactor):
    def scan(self, snapshot: ProjectSnapshot) -> tuple[Finding, ...]:
        findings = []
        for file in snapshot.files:
            text = file.content.decode("utf-8", errors="replace")
            lines = set()
            for pattern in self.patterns:
                for match in pattern.finditer(text):
                    lines.add(text.count("\n", 0, match.start()) + 1)
            for line in sorted(lines):
                findings.append(
                    Finding(
                        "SECRET_DETECTED",
                        "Potential credential found; clean or explicitly exclude this file",
                        path=file.path,
                        line=line,
                        severity="error",
                    )
                )
        return tuple(findings)
