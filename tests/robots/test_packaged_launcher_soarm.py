"""End-to-end packaged launcher delegation using a fake broker subprocess."""
import json
import os
import socket
import sys
import time
import urllib.request
from pathlib import Path

import anyio
import pytest

pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from forge_puppeteer.mcp.launcher import BrokerProvider, ServerConfig


def _broker_health_status(request: urllib.request.Request) -> bool:
    try:
        with urllib.request.urlopen(request, timeout=0.5) as response:
            return response.status == 200
    except OSError:
        return False


def _simulation_watchdog_heartbeat(url: str, token: str) -> None:
    request = urllib.request.Request(
        url, data=b"{}", method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        assert response.status == 200


@pytest.mark.parametrize("authority", [None, "", "I_AUTHORIZE_CONTROL", "none"])
def test_broker_provider_requires_exact_physical_authority(authority):
    config = ServerConfig(
        backend="soarm101_broker", device="soarm101", instance_id="arm",
        allowed_tools=["go_pose"], broker_command=sys.executable,
        physical_authority=authority,
    )
    with pytest.raises(ValueError, match="explicit physical_authority"):
        BrokerProvider(config)


def test_simulation_provider_requires_state_and_rejects_physical_authority():
    config = ServerConfig(
        backend="soarm101_broker", device="soarm101", instance_id="arm",
        simulation_only=True, allowed_tools=["go_pose"], broker_command=sys.executable,
    )
    with pytest.raises(ValueError, match="requires robot_state"):
        BrokerProvider(config)
    config.allowed_tools.append("robot_state")
    config.physical_authority = "I_AUTHORIZE_BROKER_CONTROL"
    with pytest.raises(ValueError, match="cannot declare physical_authority"):
        BrokerProvider(config)


@pytest.mark.anyio
async def test_packaged_launcher_delegates_only_to_configured_fake_broker(tmp_path):
    root = Path(__file__).resolve().parents[2]
    config_path = tmp_path / "server.json"
    config_path.write_text(json.dumps({
        "backend": "soarm101_broker", "device": "soarm101", "instance_id": "arm",
        "physical_authority": "I_AUTHORIZE_BROKER_CONTROL",
        "broker_command": sys.executable,
        "broker_args": [str(Path(__file__).with_name("soarm_broker_process_fake.py"))],
        "broker_env": {"PYTHONPATH": str(root / "src")},
        "allowed_tools": ["robot_capabilities", "robot_state", "list_assets", "go_pose", "stop"],
    }))
    params = StdioServerParameters(command=sys.executable,
        args=["-m", "forge_puppeteer.mcp.launcher", "--config", str(config_path)],
        env={**os.environ, "PYTHONPATH": str(root / "src")})
    async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
        await session.initialize()
        names = {tool.name for tool in (await session.list_tools()).tools}
        assert "go_pose" in names and "jog_cartesian" not in names
        info = (await session.call_tool("device_info", {})).structured_content
        assert info["authority"]["motion"] == "test-granted"
        result = await session.call_tool("go_pose", {"action_id": "fake-1", "caller_id": "op",
            "session_id": "s", "parameters": {"name": "ready"}})
        assert not result.is_error
        stopped = await session.call_tool("device_stop", {})
        assert stopped.structured_content["confirmed"] is True


@pytest.mark.anyio
async def test_packaged_launcher_to_real_sdk_broker_in_simulation(tmp_path):
    """Exercise Forge MCP -> SDK simulation broker directly; never hardware."""
    forge_root = Path(__file__).resolve().parents[2]
    sdk_root = forge_root.parent / "soarm101-motion-sdk"
    if not (sdk_root / "src/soarm101_motion/broker.py").is_file():
        pytest.skip("sibling soarm101-motion-sdk checkout is unavailable")
    sdk_python = sdk_root / ".venv/bin/python"
    if not sdk_python.is_file():
        pytest.skip("soarm101-motion-sdk simulation environment is unavailable")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    token = "forge-simulation-integration-token"
    profile = tmp_path / "simulation-profile.json"
    profile.write_text(json.dumps({
        "schema_version": 1,
        "name": "forge-e2e-simulation",
        "tools": ["robot_health", "robot_capabilities", "robot_state", "jog_joint",
                  "list_assets", "replay_sequence", "stop"],
        "cameras": [],
        "limits": {"max_joint_delta_deg": 1},
    }))
    broker_log = (tmp_path / "sdk-broker.log").open("w+")
    broker_env = os.environ.copy()
    broker_env.update({"SOARM101_BROKER_TOKEN": token,
                       "PYTHONPATH": str(sdk_root / "src")})
    fixture_script = Path(__file__).with_name("prepare_sdk_replay_fixture.py")
    await anyio.run_process([str(sdk_python), str(fixture_script), str(tmp_path)],
                            cwd=sdk_root, env=broker_env, check=True)
    broker = await anyio.open_process(
        [str(sdk_python), "-m", "soarm101_motion.broker", "--host", "127.0.0.1",
         "--port", str(port), "--sdk-simulation-actions", "--profile", str(profile),
         "--catalog-poses", str(tmp_path / "poses.json"),
         "--catalog-trajectories", str(tmp_path / "recordings"),
         "--catalog-sequences", str(tmp_path / "sequences.json"),
         "--replay-policy", str(tmp_path / "replay-policy.json"),
         "--watchdog-timeout-s", "10",
         "--events", str(tmp_path / "broker-events.jsonl")],
        cwd=sdk_root, env=broker_env, stdout=broker_log, stderr=broker_log,
    )
    try:
        health = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/health",
            headers={"Authorization": f"Bearer {token}"},
        )
        deadline = time.monotonic() + 15
        while True:
            if broker.returncode is not None:
                broker_log.flush()
                pytest.fail(f"SDK simulation broker exited early:\n{(tmp_path / 'sdk-broker.log').read_text()}")
            if await anyio.to_thread.run_sync(_broker_health_status, health):
                break
            if time.monotonic() >= deadline:
                pytest.fail(f"SDK simulation broker did not start:\n{(tmp_path / 'sdk-broker.log').read_text()}")
            await anyio.sleep(0.05)

        child_env = os.environ.copy()
        child_env["PYTHONPATH"] = os.pathsep.join((str(forge_root / "src"), str(sdk_root / "src")))
        # Trusted Puppeteer launcher receives only its own broker credential.
        # The value must not enter the JSON operator config or any agent tool.
        child_env["SOARM101_BROKER_TOKEN"] = token
        config_path = tmp_path / "simulation-server.json"
        config_path.write_text(json.dumps({
            "backend": "soarm101_broker", "device": "soarm101", "instance_id": "sim-arm",
            "simulation_only": True,
            "broker_url": f"http://127.0.0.1:{port}",
            "broker_token_env": "SOARM101_BROKER_TOKEN",
            "allowed_tools": ["robot_health", "robot_capabilities", "robot_state", "jog_joint",
                              "list_assets", "replay_sequence", "stop"],
        }))
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "forge_puppeteer.mcp.launcher", "--config", str(config_path)],
            env=child_env,
        )
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert {"device_info", "device_state", "jog_joint", "list_assets",
                    "replay_sequence", "device_stop"} <= names
            assert "jog_cartesian" not in names
            info = (await session.call_tool("device_info", {})).structured_content
            assert info["ready"] is True
            state = (await session.call_tool("device_state", {})).structured_content
            evidence = state["result"]["broker_session"]
            assert evidence["mode"] == "simulation_actions"
            assert state["result"]["authority"]["armed"] is False
            assets = (await session.call_tool("list_assets", {"kind": "sequence"})).structured_content
            asset = next(item for item in assets["assets"]
                         if item["id"] == "soarm101/sequence/stdio-e2e")
            assert asset["revision"].startswith("sha256:")
            # This is the trusted test supervisor heartbeat for this loopback
            # simulation process; it never arms physical authority.
            await anyio.to_thread.run_sync(
                _simulation_watchdog_heartbeat,
                f"http://127.0.0.1:{port}/v1/supervisor/heartbeat", token,
            )
            stale = await session.call_tool("replay_sequence", {
                "action_id": "sim-replay-stale", "caller_id": "integration",
                "session_id": "forge-take-1",
                "parameters": {"asset_id": asset["id"],
                               "revision": "sha256:" + "0" * 64, "speed_scale": 1.0},
            })
            assert stale.is_error  # Exact revision is rejected before runtime dispatch.
            replay = await session.call_tool("replay_sequence", {
                "action_id": "sim-replay-e2e", "caller_id": "integration",
                "session_id": "forge-take-1",
                "parameters": {"asset_id": asset["id"], "revision": asset["revision"],
                               "speed_scale": 1.0},
            })
            assert not replay.is_error
            with anyio.fail_after(5):
                while True:
                    replay_status = (await session.call_tool("action_lookup", {
                        "action_id": "sim-replay-e2e",
                    })).structured_content
                    if replay_status["state"] in {"completed", "failed", "unknown"}:
                        break
                    await anyio.sleep(0.02)
            assert replay_status["state"] == "completed", replay_status
            assert replay_status["asset_id"] == asset["id"]
            assert replay_status["asset_revision"] == asset["revision"]
            action = await session.call_tool("jog_joint", {
                "action_id": "sim-jog-1", "caller_id": "integration", "session_id": "sim-session",
                "parameters": {"joint": "shoulder_pan", "delta_deg": 0.5},
            })
            assert not action.is_error
            with anyio.fail_after(5):
                while True:
                    status = (await session.call_tool("action_lookup", {
                        "action_id": "sim-jog-1",
                    })).structured_content
                    if status["state"] in {"completed", "failed", "unknown"}:
                        break
                    await anyio.sleep(0.02)
            assert status["state"] == "completed"
            stopped = await session.call_tool("device_stop", {})
            stop_result = stopped.structured_content
            assert stop_result["confirmed"] is True
            # The real SDK simulation broker reports completion plus HOLD;
            # the Forge provider must preserve that confirmation contract.
            assert stop_result["evidence"] == "broker_stop_completed_and_holding"
    finally:
        if broker.returncode is None:
            broker.terminate()
        try:
            with anyio.fail_after(5):
                await broker.wait()
        except TimeoutError:
            broker.kill()
            with anyio.fail_after(5):
                await broker.wait()
        broker_log.close()


def test_observation_only_mcp_needs_no_physical_motion_grant():
    config = ServerConfig(
        backend="soarm101_broker", device="soarm101", instance_id="observe",
        simulation_only=False, physical_authority=None,
        broker_url="http://127.0.0.1:8765",
        allowed_tools=["robot_health", "robot_capabilities", "robot_state",
                       "capture_camera"],
    )
    provider = BrokerProvider(config)
    assert provider.config.physical_authority is None
    config.allowed_tools.append("jog_joint")
    with pytest.raises(ValueError, match="physical_authority"):
        BrokerProvider(config)
    config.allowed_tools.remove("jog_joint")
    config.allowed_tools.append("stop")
    with pytest.raises(ValueError, match="physical_authority"):
        BrokerProvider(config)
