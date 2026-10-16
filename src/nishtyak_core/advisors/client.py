from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class AdvisorUnavailable(Exception):
    pass


class InvalidAdvisorResponse(Exception):
    pass


@dataclass(frozen=True)
class DeepSeekConfig:
    model: str = ""
    api_key: str = field(default="", repr=False)
    endpoint: str = "https://api.deepseek.com"
    timeout: float = 15.0
    max_response_bytes: int = 131_072
    max_tokens: int = 2_048

    def __post_init__(self) -> None:
        endpoint = urlsplit(self.endpoint)
        if (
            endpoint.scheme != "https"
            or not endpoint.hostname
            or endpoint.username
            or endpoint.password
            or endpoint.query
            or endpoint.fragment
        ):
            raise ValueError("Advisor endpoint must be an HTTPS URL without credentials")
        if not 0 < self.timeout <= 60:
            raise ValueError("Advisor timeout must be between 0 and 60 seconds")
        if not 0 < self.max_response_bytes <= 1_048_576:
            raise ValueError("Advisor response limit must be between 1 and 1048576 bytes")
        if not 0 < self.max_tokens <= 8_192:
            raise ValueError("Advisor token limit must be between 1 and 8192")

    @classmethod
    def from_env(cls) -> DeepSeekConfig:
        return cls(
            model=os.environ.get("DEEPSEEK_MODEL", ""),
            api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
            endpoint=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        )


@dataclass(frozen=True)
class HttpRequest:
    url: str
    headers: dict[str, str] = field(repr=False)
    body: bytes = field(repr=False)
    timeout: float
    max_response_bytes: int


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes = field(repr=False)


class Transport(Protocol):
    def send(self, request: HttpRequest) -> HttpResponse: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class UrllibTransport:
    def send(self, request: HttpRequest) -> HttpResponse:
        outgoing = Request(request.url, request.body, request.headers, method="POST")
        try:
            opener = build_opener(ProxyHandler({}), _NoRedirect())
            with opener.open(outgoing, timeout=request.timeout) as response:
                return HttpResponse(response.status, response.read(request.max_response_bytes + 1))
        except HTTPError as error:
            return HttpResponse(error.code, b"")
        except (URLError, TimeoutError, OSError) as error:
            raise AdvisorUnavailable("transport_unavailable") from error


class DeepSeekClient:
    def __init__(self, config: DeepSeekConfig, transport: Transport | None = None) -> None:
        self.config = config
        self.transport = transport or UrllibTransport()

    @classmethod
    def from_env(cls, transport: Transport | None = None) -> DeepSeekClient:
        return cls(DeepSeekConfig.from_env(), transport)

    def complete(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        return self._send(messages)

    def complete_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        tool_choice: str = "auto",
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if tool_choice not in {"auto", "none"}:
            raise ValueError("Unsupported tool choice")
        return self._send(
            messages,
            {"tools": tools, "tool_choice": tool_choice, "thinking": {"type": "disabled"}},
            timeout,
        )

    def _send(
        self,
        messages: list[dict[str, Any]],
        extensions: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if not self.config.api_key:
            raise AdvisorUnavailable("missing_api_key")
        if not self.config.model:
            raise AdvisorUnavailable("missing_model")
        payload = {
            "model": self.config.model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "max_tokens": self.config.max_tokens,
        }
        payload.update(extensions or {})
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(body) > 131_072:
            raise InvalidAdvisorResponse("request_too_large")
        request = HttpRequest(
            url=self.config.endpoint.rstrip("/") + "/chat/completions",
            headers={
                "Authorization": "Bearer " + self.config.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            body=body,
            timeout=min(self.config.timeout, timeout)
            if timeout is not None
            else self.config.timeout,
            max_response_bytes=self.config.max_response_bytes,
        )
        try:
            response = self.transport.send(request)
        except (TimeoutError, ConnectionError, OSError) as error:
            raise AdvisorUnavailable("transport_unavailable") from error
        if response.status != 200:
            raise AdvisorUnavailable("http_" + str(response.status))
        if len(response.body) > self.config.max_response_bytes:
            raise InvalidAdvisorResponse("response_too_large")
        try:
            document = json.loads(response.body)
        except (ValueError, UnicodeDecodeError, RecursionError) as error:
            raise InvalidAdvisorResponse("invalid_envelope") from error
        if not isinstance(document, dict):
            raise InvalidAdvisorResponse("invalid_envelope")
        return document
