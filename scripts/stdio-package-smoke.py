"""Exercise all installed mock launch profiles over actual MCP stdio."""

import json
import os
import sys
import tempfile
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROFILES = {
    "stackchan": (
        "face_update", {"mouth": {"size": 4}}, "face-neutral",
    ),
    "reachy": (
        "antenna_targets", {"angles_rad": [0.1, -0.1], "duration_s": 0.5}, "example",
    ),
    "soarm101": ("sleep", None, "example"),
}


async def exercise(device, tool, parameters, asset_name, directory):
    config_path = Path(directory) / f"{device}.json"
    config_path.write_text(json.dumps({
        "backend": "fake",
        "device": device,
        "instance_id": f"{device}-mock",
        "allowed_tools": ["device_info", "device_state", "list_assets", "action_lookup", tool],
        "assets": [asset_name],
    }))
    params = StdioServerParameters(
        command="forge-puppeteer-mcp", args=["--config", str(config_path)],
    )
    async with (
        stdio_client(params) as (reader, writer),
        ClientSession(reader, writer) as session,
    ):
        await session.initialize()
        device_info = (await session.call_tool("device_info", {})).structured_content
        assert device_info["id"] == f"{device}-mock"
        assert device_info["provider"] == f"fake-{device}"
        assert device_info["authority"]["physical_authorization"] == "none"
        catalog = (await session.call_tool("list_assets", {})).structured_content
        assert catalog["assets"][0]["id"] == f"{device}:{asset_name}"
        arguments = {
            "action_id": f"{device}-deterministic-example",
            "caller_id": "smoke",
            "session_id": "package-smoke",
        }
        if parameters is not None:
            arguments["parameters"] = parameters
        result = await session.call_tool(tool, arguments)
        assert not result.is_error, result
        with anyio.fail_after(5):
            while True:
                action = (await session.call_tool(
                    "action_lookup", {"action_id": arguments["action_id"]}
                )).structured_content
                if action["state"] in {"completed", "failed", "unknown"}:
                    break
                await anyio.sleep(0.01)
        assert action["state"] == "completed", action
        assert action["completion_evidence"] == "deterministic fake action completed"


async def exercise_soarm_broker(directory):
    """Run the installed launcher against a separate fake MCP broker process."""
    root = Path(__file__).resolve().parents[1]
    config_path = Path(directory) / "soarm101-broker.json"
    config_path.write_text(json.dumps({
        "backend": "soarm101_broker",
        "device": "soarm101",
        "instance_id": "installed-wheel-arm",
        "physical_authority": "I_AUTHORIZE_BROKER_CONTROL",
        "broker_command": sys.executable,
        "broker_args": [str(root / "tests/robots/soarm_broker_process_fake.py")],
        "allowed_tools": ["robot_capabilities", "robot_state", "go_pose", "stop"],
    }))
    params = StdioServerParameters(command="forge-puppeteer-mcp",
                                   args=["--config", str(config_path)], env=os.environ.copy())
    async with (stdio_client(params) as (reader, writer),
                ClientSession(reader, writer) as session):
        await session.initialize()
        names = {tool.name for tool in (await session.list_tools()).tools}
        assert {"device_info", "device_state", "go_pose", "device_stop"} <= names
        assert "jog_cartesian" not in names
        info = (await session.call_tool("device_info", {})).structured_content
        assert info["id"] == "installed-wheel-arm"
        assert info["authority"]["motion"] == "test-granted"
        action_args = {"action_id": "installed-wheel-pose", "caller_id": "smoke",
                       "session_id": "wheel-smoke", "parameters": {"name": "ready"}}
        result = await session.call_tool("go_pose", action_args)
        assert not result.is_error, result
        with anyio.fail_after(5):
            while True:
                status = (await session.call_tool("action_lookup", {
                    "action_id": action_args["action_id"]
                })).structured_content
                if status["state"] in {"completed", "failed", "unknown"}:
                    break
                await anyio.sleep(0.01)
        assert status["state"] == "completed", status
        assert status["completion_evidence"] == "fake broker completion"
        stopped = (await session.call_tool("device_stop", {})).structured_content
        assert stopped["confirmed"] is True, stopped


async def main():
    with tempfile.TemporaryDirectory() as directory:
        for device, (tool, parameters, asset_name) in PROFILES.items():
            await exercise(device, tool, parameters, asset_name, directory)
        await exercise_soarm_broker(directory)


anyio.run(main)
