"""No-hardware regression for Puppeteer's direct SO-ARM101 broker transport."""

from __future__ import annotations

import sys
from types import ModuleType

import pytest

from forge_puppeteer.devices.soarm101_broker_http import (
    BrokerHTTPError,
    SOARM101BrokerHTTP,
)
from forge_puppeteer.mcp.launcher import BrokerProvider, ServerConfig


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
