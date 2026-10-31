"""Packaged SO-ARM tools exercise a real MCP stdio session against a fake broker."""

import os
import sys
from pathlib import Path

import anyio
import pytest

pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from forge_puppeteer.mcp.client import MCPClient, ServerConfig


@pytest.mark.anyio
async def test_soarm101_stdio_discovery_actions_denial_and_stop():
    root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")
    params = StdioServerParameters(
        command=sys.executable,
        env=env,
        args=[str(Path(__file__).with_name("soarm101_mcp_fake_server.py"))],
    )
    async with (
        stdio_client(params) as (reader, writer),
        ClientSession(reader, writer) as session,
    ):
        await session.initialize()
        names = {tool.name for tool in (await session.list_tools()).tools}
        assert {"device_info", "device_state", "list_assets", "go_pose",
                "jog_cartesian", "sleep", "device_stop"} <= names
        info = (await session.call_tool("device_info", {})).structured_content
        assert info["provider"] == "soarm101" and info["ready"] is True
        submitted = await session.call_tool("go_pose", {
            "action_id": "pose-1", "caller_id": "c", "session_id": "s",
            "parameters": {"name": "ready", "speed_deg_s": 10},
        })
        assert not submitted.is_error
        with anyio.fail_after(2):
            while True:
                status = (await session.call_tool(
                    "action_lookup", {"action_id": "pose-1"}
                )).structured_content
                if status["state"] in {"completed", "failed", "unknown"}:
                    break
                await anyio.sleep(0.01)
        assert status["state"] == "completed"
        assert status["completion_evidence"] == "broker_completed"
        denied = await session.call_tool("jog_cartesian", {
            "action_id": "denied", "caller_id": "c", "session_id": "s",
            "parameters": {"frame": "tool", "x_mm": 1},
        })
        assert denied.is_error
        stopped = await session.call_tool("device_stop", {})
        assert stopped.structured_content["confirmed"] is True


@pytest.mark.anyio
async def test_soarm101_sleep_behavior_pack_executes_over_fake_stdio():
    root = Path(__file__).resolve().parents[2]
    manifest = root / "examples/robots/soarm101/behavior-packs.json"
    revision = "sha256:" + __import__("hashlib").sha256(manifest.read_bytes()).hexdigest()
    config = ServerConfig(
        command=sys.executable,
        args=(str(Path(__file__).with_name("soarm101_mcp_fake_server.py")),),
        env={**os.environ, "PYTHONPATH": str(root / "src")},
    )
    client = MCPClient({"arm": config}, behavior_packs_path=manifest)
    plan = client.compile_behavior_pack("sleep", revision, "sleep-pack-take", server="arm")
    assert plan.cues[0].tool == "sleep"
    results = await client.execute(plan)
    assert results[0]["cue_id"] == "sleep:0"
    assert not results[0]["is_error"]


@pytest.mark.anyio
async def test_soarm101_stdio_requires_holding_evidence_to_recover_unknown_action():
    root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")
    params = StdioServerParameters(
        command=sys.executable,
        env=env,
        args=[str(Path(__file__).with_name("soarm101_mcp_fake_server.py")), "failure-recovery"],
    )
    async with (
        stdio_client(params) as (reader, writer),
        ClientSession(reader, writer) as session,
    ):
        await session.initialize()
        first = await session.call_tool("go_pose", {
            "action_id": "uncertain", "caller_id": "c", "session_id": "s",
            "parameters": {"name": "ready"},
        })
        assert not first.is_error
        with anyio.fail_after(2):
            while True:
                action = (await session.call_tool(
                    "action_lookup", {"action_id": "uncertain"}
                )).structured_content
                if action["state"] in {"unknown", "failed", "completed"}:
                    break
                await anyio.sleep(0.01)
        assert action["state"] == "unknown"

        weak_stop = (await session.call_tool("device_stop", {})).structured_content
        assert weak_stop == {"confirmed": False, "evidence": "completed"}
        denied_retry = await session.call_tool("go_pose", {
            "action_id": "blocked", "caller_id": "c", "session_id": "s",
            "parameters": {"name": "ready"},
        })
        assert denied_retry.is_error

        confirmed_stop = (await session.call_tool("device_stop", {})).structured_content
        assert confirmed_stop["confirmed"] is True
        recovered = await session.call_tool("go_pose", {
            "action_id": "recovered", "caller_id": "c", "session_id": "s",
            "parameters": {"name": "ready"},
        })
        assert not recovered.is_error
        with anyio.fail_after(2):
            while True:
                action = (await session.call_tool(
                    "action_lookup", {"action_id": "recovered"}
                )).structured_content
                if action["state"] in {"unknown", "failed", "completed"}:
                    break
                await anyio.sleep(0.01)
        assert action["state"] == "completed"
