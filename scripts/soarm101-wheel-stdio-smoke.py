"""Installed-wheel Forge Puppeteer MCP -> SDK authenticated simulation broker smoke."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import anyio
import numpy as np
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from soarm101_motion.api import SOARM101
from soarm101_motion.asset_catalog import discover_assets
from soarm101_motion.config import SOARM101Config
from soarm101_motion.constants import ARM_JOINTS
from soarm101_motion.poses import PoseLibrary
from soarm101_motion.sequences import MotionSequence, SequenceLibrary, SequenceStep
from soarm101_motion.trajectories import Trajectory, TrajectoryLibrary

from forge_puppeteer.mcp.client import MCPClient, PreflightError
from forge_puppeteer.mcp.client import ServerConfig as ClientServerConfig


def _healthy(request: urllib.request.Request) -> bool:
    try:
        with urllib.request.urlopen(request, timeout=0.5) as response:
            return response.status == 200
    except OSError:
        return False


def _heartbeat(url: str, token: str) -> None:
    request = urllib.request.Request(
        url, data=b"{}", method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        assert response.status == 200


async def _terminal(session: ClientSession, action_id: str) -> dict:
    with anyio.fail_after(10):
        while True:
            status = (await session.call_tool(
                "action_lookup", {"action_id": action_id}
            )).structured_content
            if status["state"] in {"completed", "failed", "unknown"}:
                return status
            await anyio.sleep(0.02)


async def _recording_replay_in_fresh_session(directory: Path, asset_id: str,
                                             revision: str, token: str,
                                             broker_env: dict[str, str]) -> None:
    """Exercise recording replay via Puppeteer MCP and a new SDK simulation broker."""
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    profile = directory / "recording-profile.json"
    profile.write_text(json.dumps({
        "schema_version": 1, "name": "fresh-recording-simulation",
        "tools": ["robot_health", "robot_capabilities", "robot_state",
                  "list_assets", "replay_recording", "stop"],
        "cameras": [], "limits": {},
    }))
    log_path = directory / "recording-sdk-broker.log"
    with log_path.open("w+") as log:
        broker = await anyio.open_process([
            sys.executable, "-I", "-m", "soarm101_motion.broker",
            "--host", "127.0.0.1", "--port", str(port), "--sdk-simulation-actions",
            "--profile", str(profile), "--catalog-poses", str(directory / "poses.json"),
            "--catalog-trajectories", str(directory / "recordings"),
            "--catalog-sequences", str(directory / "sequences.json"),
            "--replay-policy", str(directory / "replay-policy.json"),
            "--watchdog-timeout-s", "10",
            "--events", str(directory / "recording-events.jsonl"),
        ], env=broker_env, stdout=log, stderr=log)
        try:
            health = urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/health",
                headers={"Authorization": f"Bearer {token}"},
            )
            deadline = time.monotonic() + 20
            while not _healthy(health):
                if broker.returncode is not None or time.monotonic() >= deadline:
                    log.flush()
                    raise RuntimeError(f"fresh recording broker did not start:\n{log_path.read_text()}")
                await anyio.sleep(0.05)
            config = directory / "recording-forge-server.json"
            config.write_text(json.dumps({
                "backend": "soarm101_broker", "device": "soarm101",
                "instance_id": "fresh-recording-sim", "simulation_only": True,
                "broker_url": f"http://127.0.0.1:{port}",
                "broker_token_env": "SOARM101_BROKER_TOKEN",
                "allowed_tools": ["robot_health", "robot_capabilities", "robot_state",
                                  "list_assets", "replay_recording", "stop"],
            }))
            parameters = StdioServerParameters(
                command=str(Path(sys.executable).with_name("forge-puppeteer-mcp")),
                args=["--config", str(config)],
                env={**os.environ, "PYTHONNOUSERSITE": "1",
                     "SOARM101_BROKER_TOKEN": token},
            )
            async with (stdio_client(parameters) as (reader, writer),
                        ClientSession(reader, writer) as session):
                await session.initialize()
                tools = {tool.name for tool in (await session.list_tools()).tools}
                assert {"list_assets", "replay_recording", "device_stop"} <= tools
                state = (await session.call_tool("device_state", {})).structured_content
                assert state["result"]["broker_session"]["mode"] == "simulation_actions"
                _heartbeat(f"http://127.0.0.1:{port}/v1/supervisor/heartbeat", token)
                catalog = (await session.call_tool(
                    "list_assets", {"kind": "recording"}
                )).structured_content
                asset = next(item for item in catalog["assets"] if item["id"] == asset_id)
                assert asset["revision"] == revision
                # Exercise the authored behavior-pack route through preflight,
                # MCP stdio, the Forge launcher, and the actual SDK broker.
                # A separate broker session is used because SDK replay is
                # deliberately one-shot until STOP.
                pack_path = directory / "recording-pack.json"
                def write_pack(pinned_revision: str) -> str:
                    pack_path.write_text(json.dumps({
                        "schema_version": 1, "performer": "soarm101",
                        "packs": [{"id": "fixture-recording",
                            "description": "generated bounded recording fixture",
                            "cues": [{"at": 0, "operation": "replay_recording",
                                "asset_id": asset_id, "asset_revision": pinned_revision,
                                "parameters": {"speed_scale": 1.0}}]}],
                    }, sort_keys=True) + "\n")
                    return "sha256:" + hashlib.sha256(pack_path.read_bytes()).hexdigest()

                client_config = ClientServerConfig(
                    command=str(Path(sys.executable).with_name("forge-puppeteer-mcp")),
                    args=("--config", str(config)), env={"PYTHONNOUSERSITE": "1", "SOARM101_BROKER_TOKEN": token},
                    simulation_only=True,
                )
                pack_revision = write_pack("sha256:" + "0" * 64)
                client = MCPClient({"soarm101": client_config},
                                   behavior_packs_path=pack_path, completion_timeout=10)
                stale_plan = client.compile_behavior_pack(
                    "fixture-recording", pack_revision, "stale-recording-take",
                    server="soarm101")
                try:
                    await client.preflight(stale_plan)
                except PreflightError as exc:
                    assert "stale_or_missing_asset" in str(exc), exc
                else:
                    raise AssertionError("stale recording pin passed MCPClient preflight")

                pack_revision = write_pack(revision)
                plan = client.compile_behavior_pack(
                    "fixture-recording", pack_revision, "pinned-recording-take",
                    server="soarm101")
                results = await client.execute(plan)
                assert len(results) == 1, results
                replay = results[0]["result"]
                assert replay["state"] == "completed", replay
                assert replay["asset_id"] == asset_id
                assert replay["asset_revision"] == revision
                stop = (await session.call_tool("device_stop", {})).structured_content
                assert stop["confirmed"] is True, stop
                assert stop["evidence"] == "broker_stop_completed_and_holding", stop
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


def _make_fixture(directory: Path) -> dict[str, tuple[str, str]]:
    poses_path = directory / "poses.json"
    sequences_path = directory / "sequences.json"
    recording_root = directory / "recordings"
    recording_root.mkdir()
    poses_path.write_text('{"schema_version":1,"robot_id":"so101","poses":{}}\n')
    sequences = SequenceLibrary("so101", path=sequences_path)
    sequences.save(MotionSequence(
        "wheel-stdio", (SequenceStep("gripper", {"position": 0.5}),
                        SequenceStep("wait", {"seconds": 0.01})),
        metadata={"source_robot_id": "so101", "source_calibration_id": "simulation"},
    ))
    trajectories = TrajectoryLibrary("so101", root=recording_root)
    simulated_arm = SOARM101.simulated(config=SOARM101Config(robot_id="so101"), realtime=False)
    simulated_arm.connect()
    start = simulated_arm.get_joint_positions().positions
    simulated_arm.disconnect()
    trajectories.save("wheel-recording", Trajectory(
        timestamps_s=np.array([0.0, 0.02]),
        joints_rad=np.array([[start[name] for name in ARM_JOINTS]] * 2),
        gripper=np.array([1.0, 1.0]),
        metadata={"source_robot_id": "so101", "source_calibration_id": "simulation"},
    ), kind="raw")
    assets = discover_assets(
        PoseLibrary("so101", path=poses_path),
        trajectories, sequences,
    )
    sequence = next(item for item in assets if item.id == "soarm101/sequence/wheel-stdio")
    recording = next(item for item in assets
                     if item.id == "soarm101/recording/raw/wheel-recording")
    (directory / "replay-policy.json").write_text(json.dumps({
        "schema_version": 1, "robot_id": "so101", "calibration_id": "simulation",
        "allowed_assets": {sequence.id: sequence.revision,
                           recording.id: recording.revision}, "max_speed_scale": 1.0,
        "max_steps": 4, "max_duration_s": 2.0,
        "max_start_error_rad": 0.05, "max_start_gripper_error": 0.05,
    }, sort_keys=True) + "\n")
    return {"sequence": (sequence.id, sequence.revision),
            "recording": (recording.id, recording.revision)}


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="soarm101-wheel-smoke-") as raw:
        directory = Path(raw)
        assets = _make_fixture(directory)
        asset_id, revision = assets["sequence"]
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        token = "wheel-simulation-smoke-token"
        profile = directory / "profile.json"
        profile.write_text(json.dumps({
            "schema_version": 1, "name": "installed-wheel-simulation",
            "tools": ["robot_health", "robot_capabilities", "robot_state", "jog_joint",
                      "list_assets", "replay_recording", "replay_sequence", "stop"],
            "cameras": [], "limits": {"max_joint_delta_deg": 1},
        }))
        broker_env = os.environ.copy()
        broker_env.update({"SOARM101_BROKER_TOKEN": token, "PYTHONNOUSERSITE": "1"})
        log_path = directory / "sdk-broker.log"
        with log_path.open("w+") as log:
            broker = await anyio.open_process([
                sys.executable, "-I", "-m", "soarm101_motion.broker",
                "--host", "127.0.0.1", "--port", str(port), "--sdk-simulation-actions",
                "--profile", str(profile), "--catalog-poses", str(directory / "poses.json"),
                "--catalog-trajectories", str(directory / "recordings"),
                "--catalog-sequences", str(directory / "sequences.json"),
                "--replay-policy", str(directory / "replay-policy.json"),
                "--watchdog-timeout-s", "10", "--events", str(directory / "events.jsonl"),
            ], env=broker_env, stdout=log, stderr=log)
            try:
                health = urllib.request.Request(
                    f"http://127.0.0.1:{port}/v1/health",
                    headers={"Authorization": f"Bearer {token}"},
                )
                deadline = time.monotonic() + 20
                while not _healthy(health):
                    if broker.returncode is not None:
                        log.flush()
                        raise RuntimeError(f"installed SDK broker exited:\n{log_path.read_text()}")
                    if time.monotonic() >= deadline:
                        log.flush()
                        raise RuntimeError(f"installed SDK broker did not start:\n{log_path.read_text()}")
                    await anyio.sleep(0.05)

                config = directory / "forge-server.json"
                config.write_text(json.dumps({
                    "backend": "soarm101_broker", "device": "soarm101",
                    "instance_id": "installed-wheel-sim", "simulation_only": True,
                    "broker_url": f"http://127.0.0.1:{port}",
                    "broker_token_env": "SOARM101_BROKER_TOKEN",
                    "allowed_tools": ["robot_health", "robot_capabilities", "robot_state",
                                      "jog_joint", "list_assets", "replay_recording",
                                      "replay_sequence", "stop"],
                }))
                parameters = StdioServerParameters(
                    command=str(Path(sys.executable).with_name("forge-puppeteer-mcp")),
                    args=["--config", str(config)],
                    env={**os.environ, "PYTHONNOUSERSITE": "1",
                     "SOARM101_BROKER_TOKEN": token},
                )
                async with (stdio_client(parameters) as (reader, writer),
                            ClientSession(reader, writer) as session):
                    await session.initialize()
                    tools = {tool.name for tool in (await session.list_tools()).tools}
                    assert {"list_assets", "replay_sequence", "device_stop"} <= tools
                    info = (await session.call_tool("device_info", {})).structured_content
                    assert info["ready"] is True
                    state = (await session.call_tool("device_state", {})).structured_content
                    assert state["result"]["broker_session"]["mode"] == "simulation_actions"
                    catalog = (await session.call_tool(
                        "list_assets", {"kind": "sequence"}
                    )).structured_content
                    asset = next(item for item in catalog["assets"] if item["id"] == asset_id)
                    assert asset["revision"] == revision
                    _heartbeat(f"http://127.0.0.1:{port}/v1/supervisor/heartbeat", token)

                    # Exercise the public MCPClient -> installed Forge launcher
                    # -> authenticated SDK broker directly (no nested SDK MCP).
                    client_config = ClientServerConfig(
                        command=str(Path(sys.executable).with_name("forge-puppeteer-mcp")),
                        args=("--config", str(config)),
                        env={"PYTHONNOUSERSITE": "1", "SOARM101_BROKER_TOKEN": token},
                        simulation_only=True,
                    )
                    pack_path = directory / "sequence-pack.json"
                    def write_pack(pinned_revision: str) -> str:
                        pack_path.write_text(json.dumps({
                            "schema_version": 1, "performer": "soarm101",
                            "packs": [{"id": "fixture-sequence", "description": "generated simulation fixture",
                                "cues": [{"at": 0, "operation": "replay_sequence",
                                    "asset_id": asset_id, "asset_revision": pinned_revision,
                                    "parameters": {"speed_scale": 1.0}}]}],
                        }, sort_keys=True) + "\n")
                        return "sha256:" + hashlib.sha256(pack_path.read_bytes()).hexdigest()

                    pack_revision = write_pack("sha256:" + "0" * 64)
                    client = MCPClient({"soarm101": client_config},
                                       behavior_packs_path=pack_path, completion_timeout=10)
                    stale_plan = client.compile_behavior_pack(
                        "fixture-sequence", pack_revision, "stale-take", server="soarm101")
                    try:
                        await client.preflight(stale_plan)
                    except PreflightError as exc:
                        assert "stale_or_missing_asset" in str(exc), exc
                    else:
                        raise AssertionError("stale replay pin passed MCPClient preflight")

                    pack_revision = write_pack(revision)
                    plan = client.compile_behavior_pack(
                        "fixture-sequence", pack_revision, "pinned-take", server="soarm101")
                    replay_results = await client.execute(plan)
                    assert len(replay_results) == 1, replay_results
                    replay = replay_results[0]["result"]
                    assert isinstance(replay, dict) and replay["state"] == "completed", replay
                    assert replay["asset_id"] == asset_id
                    assert replay["asset_revision"] == revision

                    moved = await session.call_tool("jog_joint", {
                        "action_id": "simulated-jog", "caller_id": "wheel-smoke",
                        "session_id": "wheel-smoke",
                        "parameters": {"joint": "shoulder_pan", "delta_deg": 0.5},
                    })
                    assert not moved.is_error, moved
                    assert (await _terminal(session, "simulated-jog"))["state"] == "completed"
                    stop = (await session.call_tool("device_stop", {})).structured_content
                    assert stop["confirmed"] is True, stop
                    assert stop["evidence"] == "broker_stop_completed_and_holding", stop
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
        await _recording_replay_in_fresh_session(
            directory, *assets["recording"], token, broker_env
        )
    print("installed Forge/SDK wheel sequence and fresh-session recording replay, stale rejection, and STOP smoke passed")


if __name__ == "__main__":
    anyio.run(main)
