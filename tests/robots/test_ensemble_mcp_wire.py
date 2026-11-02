"""Exercise real MCP tool schemas and shared room events through stdio."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import anyio
import pytest

pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.anyio
async def test_real_stdio_mcp_one_room_controls_three_mock_devices():
    root = Path(__file__).resolve().parents[2]
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(root / "tests/robots/mcp_fake_ensemble_server.py")],
        env={**os.environ, "PYTHONPATH": str(root / "src")},
    )
    async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
        await session.initialize()
        tools = await session.list_tools()
        names = {tool.name for tool in tools.tools}
        assert {"ensemble_state", "ensemble_presence", "ensemble_expression"} <= names
        snapshot = await session.call_tool("ensemble_state", {})
        assert not snapshot.is_error
        assert snapshot.structured_content["state"] == "disconnected"
        accepted = await session.call_tool("ensemble_presence", {
            "session_id": "4g-room", "event_id": "e0",
            "sequence": 0, "state": "speaking",
            "source": "agent_state",
        })
        assert not accepted.is_error
        assert accepted.structured_content["outcome"] == "accepted"
        assert set(accepted.structured_content["performers"]) == {
            "reachy", "stackchan", "soarm101",
        }
        await anyio.sleep(0.04)
        yes = await session.call_tool("ensemble_expression", {
            "expression": "yes", "action_id": "yes-0",
        })
        assert not yes.is_error
        assert yes.structured_content["outcome"] == "accepted"
        duplicate = await session.call_tool("ensemble_expression", {
            "expression": "yes", "action_id": "yes-0",
        })
        assert duplicate.structured_content["outcome"] == "duplicate"
        conflict = await session.call_tool("ensemble_presence", {
            "session_id": "wrong-room", "event_id": "e1",
            "sequence": 1, "state": "listening",
        })
        assert not conflict.is_error
        assert conflict.structured_content["outcome"] == "denied"
        disconnected = await session.call_tool("ensemble_presence", {
            "session_id": "4g-room", "event_id": "e1",
            "sequence": 1, "state": "disconnected",
        })
        assert disconnected.structured_content["state"] == "disconnected"


@pytest.fixture
def anyio_backend():
    return "asyncio"
