"""Real MCP v2 stdio roundtrips with no hardware or network."""

import json
import os
import sys
from pathlib import Path

import anyio
import pytest

pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.anyio
async def test_stdio_lifecycle():
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).with_name("mcp_fake_server.py"))],
        env=env,
    )
    async with (
        stdio_client(params) as (reader, writer),
        ClientSession(reader, writer) as session,
    ):
        await session.initialize()
        names = {tool.name for tool in (await session.list_tools()).tools}
        assert {"head_move", "play_asset", "device_stop", "action_cancel"} <= names

        async def call(name, args=None):
            return await session.call_tool(name, args or {})

        assert (await call("device_info")).structured_content["id"] == "fake"
        assert (await call("device_state")).structured_content[
            "feedback"
        ] == "commanded"
        page = (await call("list_assets", {"limit": 2})).structured_content
        assert len(page["assets"]) == 2 and page["next_offset"] == 2
        assert (
            len((await call("list_assets", {"offset": 2})).structured_content["assets"])
            == 1
        )
        assert (await call("list_assets", {"limit": 101})).is_error
        assert (await call("action_lookup", {"action_id": "missing"})).is_error
        args = {"action_id": "one", "caller_id": "c", "session_id": "s", "angle": 1}
        assert not (await call("head_move", args)).is_error
        assert not (await call("head_move", args)).is_error
        assert (await call("head_move", {**args, "angle": 2})).is_error
        assert (
            await call(
                "play_asset",
                {
                    "action_id": "bad",
                    "caller_id": "c",
                    "session_id": "s",
                    "asset_id": "fake:0",
                    "revision": "bad",
                },
            )
        ).is_error
        async with anyio.create_task_group() as tg:

            async def lookup():
                assert not (await call("action_lookup", {"action_id": "one"})).is_error

            tg.start_soon(lookup)
            assert (await call("device_stop")).structured_content["confirmed"]
        assert (await call("action_lookup", {"action_id": "one"})).structured_content[
            "state"
        ] == "cancelled"
        play = {
            "action_id": "two",
            "caller_id": "c",
            "session_id": "s",
            "asset_id": "fake:0",
            "revision": "sha256:fixed",
        }
        assert not (await call("play_asset", play)).is_error
        assert (await call("action_cancel", {"action_id": "two"})).structured_content[
            "confirmed"
        ]
        await call("test_permission", {"allowed": False})
        assert (await call("head_move", {**args, "action_id": "three"})).is_error
        assert (await call("device_info")).is_error
        assert (await call("device_stop")).structured_content["confirmed"]


@pytest.mark.anyio
async def test_stdio_restart_quarantines_interrupted_action(tmp_path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    journal = tmp_path / "actions.sqlite3"
    env["FORGE_PUPPETEER_ACTION_JOURNAL"] = str(journal)
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).with_name("mcp_fake_server.py"))],
        env=env,
    )
    args = {"action_id": "interrupted", "caller_id": "c", "session_id": "s", "angle": 1}

    # Close the first stdio process with an action running. Server lifespan must
    # wait for cancellation persistence and close the journal before process exit.
    async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as first:
        await first.initialize()
        result = await first.call_tool("head_move", args)
        assert not result.is_error
    assert journal.is_file()

    async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as second:
        await second.initialize()
        replay = await second.call_tool("head_move", args)
        assert not replay.is_error
        recovered = replay.structured_content or json.loads(replay.content[0].text)
        assert recovered["state"] == "unknown"
        blocked = await second.call_tool(
            "head_move", {**args, "action_id": "other"}
        )
        assert blocked.is_error
        assert "stop_unconfirmed" in str(blocked)
        stopped = await second.call_tool("device_stop")
        assert stopped.structured_content["confirmed"]
        assert not (
            await second.call_tool("head_move", {**args, "action_id": "after-stop"})
        ).is_error


@pytest.mark.anyio
async def test_stdio_control_ownership_serializes_distinct_callers():
    """Exercise caller arbitration and active-action release over the MCP wire."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).with_name("mcp_fake_server.py"))],
        env=env,
    )
    async with (
        stdio_client(params) as (reader, writer),
        ClientSession(reader, writer) as session,
    ):
        await session.initialize()
        async def call(name, args=None):
            return await session.call_tool(name, args or {})

        owner = {"caller_id": "caller-a", "session_id": "session-a"}
        outsider = {"caller_id": "caller-b", "session_id": "session-b"}
        assert (await call("control_acquire", owner)).structured_content == {"acquired": True}
        denied = await call("head_move", {
            "action_id": "outsider-action", **outsider, "angle": 0.5,
        })
        assert denied.is_error and "ownership_busy" in str(denied)

        accepted = await call("head_move", {
            "action_id": "owner-action", **owner, "angle": 0.5,
        })
        assert not accepted.is_error
        blocked_release = await call("control_release", owner)
        assert blocked_release.is_error and "performer_busy" in str(blocked_release)

        stopped = await call("action_cancel", {"action_id": "owner-action"})
        assert stopped.structured_content["confirmed"] is True
        assert (await call("action_lookup", {"action_id": "owner-action"})).structured_content[
            "state"
        ] == "cancelled"
        assert (await call("control_release", owner)).structured_content == {"released": True}
        assert (await call("control_acquire", outsider)).structured_content == {"acquired": True}
