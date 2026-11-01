"""Operator-configured StackChan launcher tests using only a fake transport."""

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from forge_puppeteer.mcp.launcher import ServerConfig, StackChanSDKProvider


class FakeRobot:
    def __init__(self):
        self.generation = 0
        self.calls = []

    def discover(self):
        return {"capabilities": ["system.state", "avatar.update", "motion.update.position_only", "lights.update"]}

    def state(self):
        return {"native_angle_limits_tenths": {"yaw": [-500, 500], "pitch": [30, 870]}}

    def _generation(self):
        return self.generation, None

    def update_face(self, update, **kwargs):
        self.calls.append(("face", update))
        return (SimpleNamespace(event="completed"),)

    def stop(self):
        self.generation += 1
        self.calls.append(("stop", None))
        return (SimpleNamespace(event="completed"),)


class FakeClient:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def config(**changes):
    values = {
        "backend": "stackchan_sdk", "device": "stackchan", "instance_id": "robot",
        "port": "fake-port", "sdk_version": "0.3.0",
        "physical_authority": "I_AUTHORIZE_STACKCHAN_CONTROL",
        "allowed_tools": ["device_info", "device_state", "face_update", "device_stop"],
    }
    values.update(changes)
    return ServerConfig(**values)


@pytest.mark.anyio
async def test_packaged_launcher_action_journal_is_visible_after_restart(tmp_path):
    """The real stdio launcher restores completed action identity from SQLite."""
    from contextlib import AsyncExitStack

    pytest.importorskip("mcp", reason="requires the optional Puppeteer MCP dependency")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    root = Path(__file__).resolve().parents[2]
    journal = tmp_path / "actions.sqlite3"
    config_path = tmp_path / "server.json"
    config_path.write_text(json.dumps({
        "backend": "fake", "device": "stackchan", "instance_id": "robot",
        "allowed_tools": ["device_info", "face_update", "action_lookup", "device_stop"],
        "action_journal": str(journal),
    }))
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "forge_puppeteer.mcp.launcher", "--config", str(config_path)],
        env={**os.environ, "PYTHONPATH": str(root / "src")},
    )

    async def call(method, arguments):
        async with AsyncExitStack() as stack:
            reader, writer = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(reader, writer))
            await session.initialize()
            return await session.call_tool(method, arguments)

    performed = await call("face_update", {
        "action_id": "durable-action", "caller_id": "operator", "session_id": "take",
        "parameters": {"left_eye": {"x": 1, "y": 0}},
    })
    assert not performed.is_error
    assert journal.is_file()

    restored = await call("action_lookup", {"action_id": "durable-action"})
    assert not restored.is_error
    assert restored.structured_content["state"] == "completed"
    assert restored.structured_content["caller_id"] == "operator"
    assert restored.structured_content["operation"] == "face"
    missing = await call("action_lookup", {"action_id": "not-in-journal"})
    assert missing.is_error
    assert "unknown_action" in str(missing.content)


def test_launcher_requires_explicit_authority_port_and_installed_exact_version(monkeypatch):
    import importlib.metadata

    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "0.3.0")
    with pytest.raises(ValueError, match="physical_authority"):
        StackChanSDKProvider(config(physical_authority=None))
    with pytest.raises(ValueError, match="USB port"):
        StackChanSDKProvider(config(port=None))
    with pytest.raises(ValueError, match="does not match"):
        StackChanSDKProvider(config(sdk_version="0.3.1"))
    with pytest.raises(ValueError, match="unsupported StackChan"):
        StackChanSDKProvider(config(allowed_tools=["raw_command"]))


@pytest.mark.anyio
async def test_packaged_provider_uses_fake_transport_and_closes_session(monkeypatch):
    import importlib.metadata

    pytest.importorskip("stackchan_control", reason="requires the external StackChan SDK")
    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "0.3.0")
    robot, client = FakeRobot(), FakeClient()
    opened = []

    def connector(port, *, timeout):
        opened.append((port, timeout))
        return client, robot

    provider = StackChanSDKProvider(config(), connector=connector)
    async with provider.connected():
        assert opened == [("fake-port", 8.0)]
        assert (await provider.describe()).ready
        assert await provider.permitted("face_update")
        assert not await provider.permitted("head_move")
        result = await provider.stop()
        assert result.confirmed
    assert client.closed
    assert robot.calls == [("stop", None)]
    with pytest.raises(RuntimeError, match="not active"):
        await provider.describe()


def test_read_only_stackchan_config_requires_no_motion_declaration(monkeypatch):
    import importlib.metadata

    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "0.3.0")
    read_only = config(
        physical_authority=None,
        allowed_tools=["device_info", "device_state", "list_assets", "action_lookup"],
    )
    provider = StackChanSDKProvider(read_only)
    assert provider.grants == frozenset(read_only.allowed_tools)
    with pytest.raises(ValueError, match="physical_authority"):
        StackChanSDKProvider(config(
            physical_authority=None,
            allowed_tools=["device_info", "device_stop"],
        ))
    with pytest.raises(ValueError, match="physical_authority"):
        StackChanSDKProvider(config(
            physical_authority=None,
            allowed_tools=["device_info", "neohex_update"],
        ))


def test_neohex_requires_explicit_known_tool_grant(monkeypatch):
    import importlib.metadata

    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "0.3.0")
    provider = StackChanSDKProvider(config(allowed_tools=[
        "device_info", "device_state", "neohex_update", "device_stop",
    ]))
    assert "neohex_update" in provider.grants
    with pytest.raises(ValueError, match="unsupported StackChan"):
        StackChanSDKProvider(config(allowed_tools=["arbitrary_serial_write"]))
