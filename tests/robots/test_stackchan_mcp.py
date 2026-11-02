"""Real stdio roundtrip through the typed StackChan provider and fake facade."""

import json
import os
import sys
from pathlib import Path

import anyio
import pytest

pytest.importorskip("mcp")
pytest.importorskip("stackchan_control.external_control")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.anyio
async def test_stackchan_mcp_typed_operations_and_asset(tmp_path):
    root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")
    (tmp_path / "face.json").write_text(json.dumps({
        "schema_version": 1, "id": "face-neutral", "kind": "pose", "steps": [
            {"at_s": 0, "operation": "face", "parameters": {"mouth": {"size": 0}}}
        ],
    }))
    params = StdioServerParameters(
        command=sys.executable,
        env=env,
        args=[
            str(Path(__file__).with_name("stackchan_mcp_fake_server.py")),
            str(tmp_path),
        ],
    )
    async with (
        stdio_client(params) as (reader, writer),
        ClientSession(reader, writer) as session,
    ):
        await session.initialize()
        tools = {tool.name for tool in (await session.list_tools()).tools}
        assert {
            "head_move",
            "face_update",
            "lights_update",
            "play_asset",
            "neohex_update",
            "device_stop",
        } <= tools
        assets = (await session.call_tool("list_assets", {})).structured_content[
            "assets"
        ]
        face = next(
            asset for asset in assets if asset["id"] == "stackchan/face-neutral"
        )
        result = await session.call_tool(
            "face_update",
            {
                "action_id": "face",
                "caller_id": "c",
                "session_id": "s",
                "parameters": {"mouth": {"size": 4}},
            },
        )
        assert not result.is_error
        with anyio.fail_after(2):
            while True:
                action = (
                    await session.call_tool("action_lookup", {"action_id": "face"})
                ).structured_content
                if action["state"] in ("completed", "failed", "unknown"):
                    break
                await anyio.sleep(0.01)
        assert action["state"] == "completed"
        neohex = await session.call_tool(
            "neohex_update",
            {
                "action_id": "neohex",
                "caller_id": "c",
                "session_id": "s",
                "parameters": {
                    "pixels": ["#00ff55"] * 37,
                    "brightness": 12,
                },
            },
        )
        assert not neohex.is_error
        with anyio.fail_after(2):
            while True:
                light_action = (
                    await session.call_tool("action_lookup", {"action_id": "neohex"})
                ).structured_content
                if light_action["state"] in ("completed", "failed", "unknown"):
                    break
                await anyio.sleep(0.01)
        assert light_action["state"] == "completed"
        bad_neohex = await session.call_tool(
            "neohex_update",
            {
                "action_id": "neohex-bad", "caller_id": "c", "session_id": "s",
                "parameters": {"pixels": ["#ffffff"] * 36, "brightness": 12},
            },
        )
        assert bad_neohex.is_error
        invalid = await session.call_tool(
            "face_update",
            {
                "action_id": "bad",
                "caller_id": "c",
                "session_id": "s",
                "parameters": {"mouth": {"size": 3}, "yaw": 40},
            },
        )
        assert invalid.is_error
        play = await session.call_tool(
            "play_asset",
            {
                "action_id": "play",
                "caller_id": "c",
                "session_id": "s",
                "asset_id": face["id"],
                "revision": face["revision"],
            },
        )
        assert not play.is_error
        with anyio.fail_after(2):
            while True:
                action = (
                    await session.call_tool("action_lookup", {"action_id": "play"})
                ).structured_content
                if action["state"] in ("completed", "failed", "unknown"):
                    break
                await anyio.sleep(0.01)
        assert action["state"] == "completed"
        assert (await session.call_tool("device_stop", {})).structured_content[
            "confirmed"
        ]
