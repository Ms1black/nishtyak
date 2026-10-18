from __future__ import annotations

import re
import unicodedata

REMOVED_INSTRUCTION = "[UNTRUSTED INSTRUCTION REMOVED]"


class PromptGuard:
    """Remove obvious instruction attempts from untrusted text, preserving lines.

    This bounded heuristic is an additional filter, not an injection-proof trust
    boundary. Read-only tools, strict response schemas and independent checks
    must still constrain the model. Normalization is used only for detection.
    """

    _window = 4096
    _overlap = 512
    _instructions = tuple(
        re.compile(pattern, re.I | re.M)
        for pattern in (
            r"<\|(?:im_start|im_end|start_header_id|end_header_id|eot_id|begin_of_text|"
            r"end_of_text|system|developer|assistant|user|tool)\|>|"
            r"\[/?inst\]|\[/?(?:system|developer|assistant)\]|<<?/?sys>>?|"
            r"</?(?:system|developer|assistant|tool_result)>",
            r"\b(?:ignore|disregard|forget|override|bypass)\b[\s\S]{0,96}"
            r"\b(?:instructions?|directives?|system\s+prompt|developer\s+message|"
            r"(?:previous|prior|all|system|safety)\s+(?:rules?|policies|restrictions?|constraints?))\b",
            r"\b(?:do\s+not|never|don't)\s+(?:follow|obey)\b[\s\S]{0,64}"
            r"\b(?:system|developer|previous|prior)\b",
            r"\b(?:treat|use|consider)\b[\s\S]{0,48}\b(?:this|the)\s+"
            r"(?:file|text|readme|comment|document)\b[\s\S]{0,48}"
            r"\b(?:system|developer|higher.priority)\b",
            r"\b(?:you\s+are|act\s+as|pretend\s+to\s+be)\b[\s\S]{0,64}"
            r"\b(?:now|chatgpt|assistant|unrestricted|jailbroken|system|developer)\b",
            r"\b(?:from\s+now\s+on|follow\s+these\s+instructions|"
            r"respond\s+only\s+with|output\s+exactly)\b",
            r"^[ #>*]{0,12}(?:system|developer|assistant)\s{0,4}:\s{0,8}"
            r"(?:you|ignore|must|follow|override|instructions?|теперь|ты|вы|игнорируй)\b",
            r"\b(?:игнорируй(?:те)?|забудь(?:те)?|обойди(?:те)?|отмени(?:те)?)\b"
            r"[\s\S]{0,96}(?:предыдущ\w{0,10}|прежн\w{0,10}|системн\w{0,10}|"
            r"инструкц\w{0,10}|правил\w{0,10}|ограничен\w{0,10})",
            r"\b(?:(?:ты|вы)\s{1,8}(?:теперь|больше\s+не)|теперь\s+(?:ты|вы))\b[\s\S]{0,64}"
            r"(?:ассистент\w{0,8}|модель|помощник\w{0,8}|системн\w{0,8})",
            r"\b(?:следуй(?:те)?|выполняй(?:те)?)\b[\s\S]{0,48}"
            r"\b(?:только|нов\w{0,8})\b[\s\S]{0,32}инструкц\w{0,10}",
            r"\b(?:ответь(?:те)?|отвечай(?:те)?|выведи(?:те)?)\b[\s\S]{0,40}"
            r"\b(?:только|строго)\b",
            r"\b(?:assistant|chatgpt|llm|ai(?:\s+agent)?|ии|ассистент\w{0,8}|"
            r"нейросет\w{0,8}|помощник\w{0,8})\b[\s,:!—-]{1,8}"
            r"(?:(?:you\s+)?(?:must|should|please|need\s+to)\s{1,8})?"
            r"(?:install|download|execute|run|send|upload|exfiltrate|"
            r"установи(?:те)?|скачай(?:те)?|выполни(?:те)?|запусти(?:те)?|отправь(?:те)?)\b",
            r"\b(?:model|agent|модель|агент\w{0,8})\b"
            r"(?:[,:!—-]\s{0,8}|\s{1,8}(?:must|should|need\s+to)\s{1,8})"
            r"(?:(?:you\s+)?(?:must|should|please)\s{1,8})?"
            r"(?:install|download|execute|run|send|upload|exfiltrate|"
            r"установи(?:те)?|скачай(?:те)?|выполни(?:те)?|запусти(?:те)?|отправь(?:те)?)\b",
            r"\b(?:before|to)\s+(?:answering|responding|continuing)\b[\s\S]{0,80}"
            r"\b(?:install|download|run|execute|send|upload)\b",
            r"\b(?:reveal|print|send|expose|leak|exfiltrate|upload|transmit)\b"
            r"[\s\S]{0,64}\b(?:your\s+(?:secrets?|tokens?|credentials?|passwords?)|"
            r"system\s+prompt|developer\s+(?:message|prompt)|all\s+(?:api\s+keys?|"
            r"secrets?|tokens?|credentials?|environment\s+variables))\b",
            r"\b(?:отправь(?:те)?|передай(?:те)?|раскрой(?:те)?|покажи(?:те)?)\b"
            r"[\s\S]{0,64}(?:системн\w{0,8}\s+(?:промпт|инструкц)|секрет\w{0,8}|"
            r"токен\w{0,8}|парол\w{0,8}|ключ\w{0,8}\s+api)",
        )
    )
    _output = tuple(
        re.compile(pattern, re.I | re.M)
        for pattern in (
            r"<\s{0,8}/?\s{0,8}[a-z][^>\n]{0,512}>|```|~~~|\]\s{0,8}\(|"
            r"^\s{0,12}(?:\$|#!|#{1,6}\s)",
            r"\b[a-z][a-z0-9+.-]{1,15}://|\b(?:https?|javascript):|"
            r"\b(?:data|file|mailto):[^\s]|"
            r"\bwww\.|\bgit@",
            r"\b(?:curl|wget|sudo)\s|\bpip3?\s+(?:install|download|uninstall)\b|"
            r"\buv\s+(?:pip\s+)?(?:install|download|add|sync|run|tool)\b|"
            r"\b(?:npm|pnpm|yarn)\s+(?:install|add|exec|run|dlx)\b|"
            r"\b(?:apt(?:-get)?|brew|dnf|yum|conda|mamba)\s+(?:install|update|remove|run)\b",
            r"\bpython(?:[0-9.]{0,7})\s+(?:-[a-z]|[\w./\\-]{1,120}\.py\b)|"
            r"\b(?:ba|z|fi)?sh\s+(?:-|[\w./\\-]{1,120}\.sh\b)|"
            r"\b(?:cmd(?:\.exe)?|powershell|pwsh)\s+(?:/c\b|-)|"
            r"\b(?:rm|chmod|chown|eval|exec|source)\s+(?:-|[./~])|\$\(",
            r"\b(?:node|ruby|perl)\s+-e\b|\bphp\s+-r\b|"
            r"\b(?:invoke-webrequest|invoke-expression|start-process)\b|"
            r"\b(?:cat|tee|touch|mkdir|mv|cp|base64|nc|netcat|ssh|scp)\s+(?:-|[./~])|"
            r"\b(?:echo|printf)\s+[^\n]{0,160}[|>]|&&|\|\||"
            r"\|\s{0,8}(?:ba|z)?sh\b",
            r"(?:^|[.!?;:]\s{1,8})(?:please\s+)?(?:install|download|execute|run|upload|"
            r"send|open\s+a\s+terminal|copy\s+and\s+paste)\b|"
            r"\b(?:установи(?:те)?|скачай(?:те)?|загрузи(?:те)?|выполни(?:те)?|"
            r"запусти(?:те)?|отправь(?:те)?|скопируй(?:те)?)\b",
            r"\b(?:you\s+(?:must|should|need\s+to)|please)\s+"
            r"(?:install|download|run|execute|upload|send)\b|"
            r"\b(?:нужно|следует|необходимо|требуется|рекомендуется)\s+"
            r"(?:установить|скачать|загрузить|выполнить|запустить|отправить)\b",
        )
    )

    @staticmethod
    def _normalize(text: str) -> str:
        normalized = unicodedata.normalize("NFKC", text).casefold()
        normalized = "".join(char for char in normalized if unicodedata.category(char) != "Cf")
        return re.sub(r"[^\S\n]+", " ", normalized)

    @classmethod
    def _spans(cls, normalized: str) -> list[tuple[int, int]]:
        spans = set()
        for offset in range(0, len(normalized), cls._window):
            start = max(0, offset - cls._overlap)
            chunk = normalized[start : offset + cls._window]
            for pattern in cls._instructions:
                spans.update(
                    (start + match.start(), start + match.end())
                    for match in pattern.finditer(chunk)
                )
        return sorted(spans)

    @classmethod
    def redact(cls, text: str) -> str:
        normalized = cls._normalize(text)
        spans = cls._spans(normalized)
        if not spans:
            return text
        merged: list[tuple[int, int]] = []
        for start, end in spans:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        removed, previous, line = set(), 0, 0
        for start, end in merged:
            line += normalized.count("\n", previous, start)
            last = line + normalized.count("\n", start, max(start, end - 1))
            removed.update(range(line, last + 1))
            line += normalized.count("\n", start, end)
            previous = end
        return "\n".join(
            REMOVED_INSTRUCTION + ("\r" if value.endswith("\r") else "")
            if index in removed
            else value
            for index, value in enumerate(text.split("\n"))
        )

    @classmethod
    def unsafe_output(cls, text: str) -> bool:
        normalized = cls._normalize(text)
        return (
            REMOVED_INSTRUCTION.casefold() in normalized
            or bool(cls._spans(normalized))
            or any(pattern.search(normalized) for pattern in cls._output)
        )
