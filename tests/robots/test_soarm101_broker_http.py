"""No-hardware regression for Puppeteer's direct SO-ARM101 broker transport."""

from __future__ import annotations

import sys
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import ModuleType

import pytest

from forge_puppeteer.devices.soarm101_broker_http import (
    BrokerHTTPError,
    SOARM101BrokerHTTP,
)
from forge_puppeteer.mcp.launcher import BrokerProvider, ServerConfig, _soarm_startup_grants


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def sdk_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    # The authoritative registry lives in the separately installed SDK.
    # Stub its *import*, not a second route mapping in the production code.
    sdk = ModuleType("soarm101_motion")
    sdk.__path__ = []  # type: ignore[attr-defined]
    policy = ModuleType("soarm101_motion.capability_profile")
    policy.BROKER_CAPABILITY_ROUTES = {  # type: ignore[attr-defined]
        "robot_capabilities": ("GET", "/v1/capabilities"),
        "robot_state": ("GET", "/v1/state"),
        "go_pose": ("POST", "/v1/go-pose"),
        "stop": ("POST", "/v1/stop"),
    }
    monkeypatch.setitem(sys.modules, "soarm101_motion", sdk)
    monkeypatch.setitem(sys.modules, "soarm101_motion.capability_profile", policy)


@pytest.fixture
def fake_http(
    monkeypatch: pytest.MonkeyPatch, sdk_policy: None,
) -> list[tuple[str, str, dict[str, object] | None]]:
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def request(self, method, path, body=None):
        calls.append((method, path, body))
        if path == "/v1/profile":
            return {"ok": True, "result": {"tools": ["robot_state", "go_pose", "stop",
                                                        "robot_capabilities"]}}
        if path == "/v1/state":
            return {"ok": True, "result": {
                "broker_session": {"mode": "simulation_actions", "id": "session-test"},
                "authority": {"armed": False},
            }}
        if path == "/v1/capabilities":
            return {"ok": True, "result": {"authority": {"armed": False}}}
        if path == "/v1/go-pose":
            return {"ok": True, "result": {"completed": True}}
        if path == "/v1/stop":
            return {"ok": True, "result": {"completed": True, "holding": True}}
        raise AssertionError("unknown route")

    monkeypatch.setattr(SOARM101BrokerHTTP, "_request_sync", request)
    return calls


@pytest.mark.anyio
async def test_direct_broker_uses_sdk_registry_and_live_permission_check(fake_http):
    client = SOARM101BrokerHTTP("http://127.0.0.1:8765", "fixture-not-real")
    names = {tool.name for tool in (await client.list_tools()).tools}
    assert names == {"robot_capabilities", "robot_state", "go_pose", "stop"}
    response = await client.call_tool("go_pose", {"name": "ready"})
    assert response.structured_content["result"] == {"completed": True}
    assert fake_http[-1] == ("POST", "/v1/go-pose", {"name": "ready"})
    assert fake_http[-2][1] == "/v1/profile"
    with pytest.raises(BrokerHTTPError, match="unsupported"):
        await client.call_tool("override_calibration", {})
    assert all(url != "/v1/calibrate" for _, url, _ in fake_http)


@pytest.mark.anyio
async def test_broker_profile_revocation_fails_closed(fake_http, monkeypatch):
    original = SOARM101BrokerHTTP._request_sync

    def revoked(self, method, path, body=None):
        if path == "/v1/profile":
            return {"ok": True, "result": {"tools": ["robot_state", "stop"]}}
        return original(self, method, path, body)

    monkeypatch.setattr(SOARM101BrokerHTTP, "_request_sync", revoked)
    client = SOARM101BrokerHTTP("http://localhost:8765", "fixture")
    with pytest.raises(BrokerHTTPError, match="denied"):
        await client.call_tool("go_pose", {"name": "ready"})
    assert not any(path == "/v1/go-pose" for _, path, _ in fake_http)


@pytest.mark.parametrize("url", [
    "http://remote.example:8765",
    "https://127.0.0.1:8765",
    "http://user:password@localhost:8765",
    "http://localhost:8765/another",
    "http://localhost:8765?x=1",
])
def test_only_explicit_local_broker_urls(url, sdk_policy):
    with pytest.raises(ValueError, match="loopback"):
        SOARM101BrokerHTTP(url, "fixture")


@pytest.mark.anyio
async def test_puppeteer_provider_no_longer_requires_sdk_mcp_subprocess(
    fake_http, monkeypatch,
):
    monkeypatch.setenv("SOARM101_BROKER_TOKEN", "fixture-not-real")
    config = ServerConfig(
        backend="soarm101_broker", device="soarm101", instance_id="arm",
        simulation_only=True,
        broker_url="http://127.0.0.1:8765",
        allowed_tools=["robot_capabilities", "robot_state", "go_pose", "stop"],
    )
    provider = BrokerProvider(config)
    async with provider.connected():
        assert await provider.permitted("go_pose")
        assert (await provider.state())["result"]["authority"]["armed"] is False
        assert (await provider.describe()).provider == "soarm101"
        response = await provider.go_pose("ready")
        assert response["result"]["completed"] is True
        stopped = await provider.stop()
        assert stopped.confirmed is True
    assert any(path == "/v1/stop" for _, path, _ in fake_http)


def test_direct_broker_forbids_credential_in_operator_config():
    config = ServerConfig(
        backend="soarm101_broker", device="soarm101", instance_id="arm",
        simulation_only=True, broker_url="http://127.0.0.1:8765",
        broker_env={"SOARM101_BROKER_TOKEN": "should-never-be-in-config"},
        allowed_tools=["robot_state"],
    )
    with pytest.raises(ValueError, match="inline credentials"):
        BrokerProvider(config)
    config.broker_env = {}
    config.broker_command = "soarm101-mcp"
    with pytest.raises(ValueError, match="exactly one"):
        BrokerProvider(config)


@pytest.mark.anyio
async def test_real_loopback_http_profile_and_command_never_use_sdk_mcp(
    sdk_policy, monkeypatch,
):
    """Real HTTP transport fixture; never connects a robot or SDK MCP server."""
    requests: list[tuple[str, str, str]] = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(("GET", self.path, self.headers.get("Authorization", "")))
            if self.path == "/v1/profile":
                body = {"ok": True, "result": {"tools": ["robot_capabilities", "go_pose", "stop"]}}
            else:
                body = {"ok": True, "result": {"authority": {"armed": False}}}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode("utf-8"))

        def do_POST(self):
            requests.append(("POST", self.path, self.headers.get("Authorization", "")))
            assert self.path == "/v1/go-pose"
            size = int(self.headers.get("Content-Length", "0"))
            assert json.loads(self.rfile.read(size)) == {"name": "ready"}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true, "result": {"completed": true}}')

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
        client = SOARM101BrokerHTTP(f"http://127.0.0.1:{server.server_port}", "fixture-token")
        response = await client.call_tool("go_pose", {"name": "ready"})
        assert response.structured_content["result"] == {"completed": True}
        assert [(m, p) for m, p, _ in requests] == [
            ("GET", "/v1/profile"), ("POST", "/v1/go-pose"),
        ]
        assert all(authorization == "Bearer fixture-token" for _, _, authorization in requests)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.anyio
async def test_redirect_never_forwards_broker_credential(sdk_policy):
    """One local redirect must be treated as an error, not an MCP result."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    received: list[str] = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(self.path)
            if self.path == "/v1/profile":
                self.send_response(307)
                self.send_header("Location", "http://127.0.0.1:1/untrusted")
                self.end_headers()
            else:
                raise AssertionError("redirect must not be followed")

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = SOARM101BrokerHTTP(f"http://127.0.0.1:{server.server_port}", "fixture-token")
        with pytest.raises(BrokerHTTPError, match="broker_connection_unavailable"):
            await client.list_tools()
        assert received == ["/v1/profile"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_tool_discovery_intersects_operator_and_live_broker_profile(fake_http, monkeypatch):
    monkeypatch.setenv("SOARM101_BROKER_TOKEN", "fixture")
    config = ServerConfig(
        backend="soarm101_broker", device="soarm101", instance_id="arm",
        simulation_only=True, broker_url="http://127.0.0.1:8765",
        allowed_tools=["robot_capabilities", "robot_state", "go_pose", "capture_camera"],
    )
    assert _soarm_startup_grants(config) == frozenset({
        "robot_capabilities", "robot_state", "go_pose",
    })
    assert fake_http[-1][1] == "/v1/profile"
