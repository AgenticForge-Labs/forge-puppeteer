"""Authenticated SO-ARM101 broker client; no MCP-to-MCP subprocess.

This is the transport bridge between Puppeteer's existing execution provider
and the SDK-owned broker. The broker remains the only authority for permission,
motion, calibration, replay, watchdogs and STOP. Only the SDK's canonical route
registry can select a host route; caller input never supplies a path/method.

Temporary list_tools/call_tool compatibility methods let the existing
SOARM101Provider use this transport while its old MCP-client tests migrate.
They do NOT start an MCP server or emulate hardware authority.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit

import anyio


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Never forward broker Authorization outside the selected local origin."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class BrokerHTTPError(RuntimeError):
    """A sanitized broker transport/contract failure."""


@dataclass(frozen=True, slots=True)
class BrokerTool:
    name: str


@dataclass(frozen=True, slots=True)
class BrokerToolList:
    tools: tuple[BrokerTool, ...]


@dataclass(frozen=True, slots=True)
class BrokerResult:
    structured_content: dict[str, object]
    is_error: bool = False
    content: tuple[()] = ()


class SOARM101BrokerHTTP:
    """Authenticated local HTTP/JSON broker transport, no SDK motor access."""

    def __init__(
        self,
        url: str,
        token: str,
        *,
        timeout_s: float = 120.0,
    ) -> None:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.port is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("SO-ARM broker must use explicit loopback HTTP")
        if not token or not token.strip():
            raise ValueError("SO-ARM broker authentication is required")
        if not 0 < timeout_s <= 120:
            raise ValueError("invalid broker timeout")
        # Import only the SDK's policy registry; do NOT import its MCP facade
        # or use its serial/servo/controller implementations directly.
        try:
            from soarm101_motion.capability_profile import BROKER_CAPABILITY_ROUTES
        except ImportError as exc:
            raise RuntimeError("Install the standalone SO-ARM101 Motion SDK") from exc
        self._routes = dict(BROKER_CAPABILITY_ROUTES)
        self._url = url.rstrip("/")
        self._token = token
        self._timeout_s = timeout_s

    def _request_sync(
        self, method: str, path: str, body: dict[str, object] | None = None,
    ) -> dict[str, object]:
        payload = None if body is None else json.dumps(
            body, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            self._url + path,
            method=method,
            data=payload,
            headers={
                "Authorization": "Bearer " + self._token,
                "Content-Type": "application/json",
            },
        )
        # Avoid environment HTTP proxies and redirects: neither may receive
        # the broker bearer token or change which process we contact.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirects())
        try:
            with opener.open(request, timeout=self._timeout_s) as response:
                # Maximum includes large camera frames while preventing
                # unbounded tool content from entering the MCP process.
                data = response.read(16 * 1024 * 1024 + 1)
        except (urllib.error.URLError, OSError, TimeoutError):
            raise BrokerHTTPError("broker_connection_unavailable") from None
        if len(data) > 16 * 1024 * 1024:
            raise BrokerHTTPError("broker_response_too_large")
        try:
            value = json.loads(data)
        except (ValueError, UnicodeError):
            raise BrokerHTTPError("invalid_broker_json") from None
        if (
            not isinstance(value, dict)
            or value.get("ok") is not True
            or not isinstance(value.get("result"), dict)
        ):
            raise BrokerHTTPError("broker_request_denied_or_invalid") from None
        return value

    async def _request(
        self, method: str, path: str, body: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return await anyio.to_thread.run_sync(self._request_sync, method, path, body)

    async def list_tools(self) -> BrokerToolList:
        # Recheck the *live broker profile*, not an operator's stale cache.
        result = (await self._request("GET", "/v1/profile"))["result"]
        if not isinstance(result, dict):
            raise BrokerHTTPError("invalid_broker_capability_profile")
        names = result.get("tools")
        if (
            not isinstance(names, list)
            or any(not isinstance(name, str) for name in names)
            or len(names) != len(set(names))
            or set(names) - self._routes.keys()
        ):
            raise BrokerHTTPError("invalid_broker_capability_profile")
        return BrokerToolList(tuple(BrokerTool(name) for name in sorted(names)))

    async def call_tool(
        self, name: str, arguments: dict[str, object],
    ) -> BrokerResult:
        if name not in self._routes:
            raise BrokerHTTPError("unsupported_broker_capability")
        visible = {tool.name for tool in (await self.list_tools()).tools}
        if name not in visible:
            raise BrokerHTTPError("broker_capability_denied")
        method, path = self._routes[name]
        response = await self._request(
            method,
            path,
            arguments if method == "POST" else None,
        )
        return BrokerResult({
            "request_id": response.get("request_id"),
            "result": response["result"],
        })
