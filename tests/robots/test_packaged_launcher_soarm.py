"""End-to-end packaged launcher delegation using a fake broker subprocess."""
import json
import os
import socket
import subprocess
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


def test_direct_check_config_rejects_ungranted_control_without_connecting(tmp_path):
    path = tmp_path / "broker.json"
    config = {"backend": "soarm101_broker", "device": "soarm101", "instance_id": "arm",
              "broker_url": "http://127.0.0.1:1", "allowed_tools": ["robot_state", "jog_joint"]}
    path.write_text(json.dumps(config))
    command = [sys.executable, "-m", "forge_puppeteer.mcp.launcher", "--config", str(path), "--check-config"]
    denied = subprocess.run(command, text=True, capture_output=True, check=False)
    assert denied.returncode != 0 and "physical_authority" in denied.stderr
    config["simulation_only"] = True
    path.write_text(json.dumps(config))
    valid = subprocess.run(command, text=True, capture_output=True, check=True)
    result = json.loads(valid.stdout)
    assert result["outcome"] == "static_config_valid"
    assert result["connections_opened"] is False and result["live_authority_verified"] is False


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


@pytest.mark.anyio
@pytest.mark.parametrize("end_mode", ["stale", "interrupt", "unknown_stop", "busy", "operator_only"])
async def test_soarm_only_v4_stdio_runs_pinned_programs_through_real_sdk_broker(tmp_path, end_mode):
    """Real Puppeteer stdio -> trusted SDK simulation choreography, no hardware."""
    forge_root = Path(__file__).resolve().parents[2]
    sdk_root = forge_root.parent / "soarm101-motion-sdk"
    if not (sdk_root / "src/soarm101_motion/broker.py").is_file():
        pytest.skip("sibling soarm101-motion-sdk checkout is unavailable")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    token, trusted_token = "e2e-agent-token", "e2e-trusted-host-token"
    telemetry = tmp_path / "sdk-trusted-requests.jsonl"
    child_env = os.environ.copy()
    child_env.update({
        "SOARM101_BROKER_TOKEN": token,
        "SOARM101_TRUSTED_HOST_TOKEN": trusted_token,
    })
    if os.environ.get("SOARM_TEST_INSTALLED_WHEELS") == "1":
        child_env.pop("PYTHONPATH", None)
        child_env["PYTHONNOUSERSITE"] = "1"
    else:
        child_env["PYTHONPATH"] = os.pathsep.join((str(forge_root / "src"), str(sdk_root / "src")))
    if end_mode in {"stale", "unknown_stop"}:
        child_env["SOARM_TEST_SPEAKING_DELAY"] = "3.5"
    if end_mode == "unknown_stop":
        child_env["SOARM_TEST_LOSE_STOP_ACK"] = "1"
    if end_mode == "operator_only":
        child_env["SOARM_TEST_OPERATOR_ONLY"] = "1"
    broker_log_path = tmp_path / "sdk-trusted-broker.log"
    broker_log = broker_log_path.open("w+")
    broker = await anyio.open_process(
        [sys.executable, str(Path(__file__).with_name("soarm_trusted_sim_broker.py")),
         str(port), str(telemetry)],
        cwd=sdk_root, env=child_env, stdout=broker_log, stderr=broker_log,
    )
    try:
        health = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/health",
            headers={"Authorization": f"Bearer {token}"},
        )
        deadline = time.monotonic() + 15
        while not await anyio.to_thread.run_sync(_broker_health_status, health):
            if broker.returncode is not None:
                broker_log.flush()
                pytest.fail(f"SDK simulation broker exited early:\n{broker_log_path.read_text()}")
            if time.monotonic() >= deadline:
                pytest.fail(f"SDK simulation broker did not start:\n{broker_log_path.read_text()}")
            await anyio.sleep(0.05)

        # The helper publishes the exact generated hashes through its private
        # catalog. Discover only that trusted catalog to bind the room pins.
        catalog_request = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/trusted/catalog",
            headers={"Authorization": f"Bearer {trusted_token}"},
        )
        with urllib.request.urlopen(catalog_request, timeout=3) as response:  # noqa: ASYNC210 - test helper, blocking is fine
            catalog = json.loads(response.read())["result"]["programs"]
        revisions = {item["name"]: item["revision"] for item in catalog}
        durations = {item["name"]: item["duration_s"] for item in catalog}
        if end_mode == "operator_only":
            direct = tmp_path / "direct.json"
            direct.write_text(json.dumps({
                "backend": "soarm101_broker", "device": "soarm101", "instance_id": "arm",
                "simulation_only": True, "broker_url": f"http://127.0.0.1:{port}",
                "broker_token_env": "SOARM101_BROKER_TOKEN",
                "allowed_tools": ["robot_health", "robot_capabilities", "robot_state", "jog_joint", "stop"],
            }))
            direct_params = StdioServerParameters(command=sys.executable,
                args=["-m", "forge_puppeteer.mcp.launcher", "--config", str(direct)], env=child_env)
            async with stdio_client(direct_params) as (r, w), ClientSession(r, w) as direct_session:
                await direct_session.initialize()
                response = await direct_session.call_tool("jog_joint", {
                    "action_id": "denied-jog", "caller_id": "test", "session_id": "test",
                    "parameters": {"joint": "shoulder_pan", "delta_deg": 0.5},
                })
                if not response.is_error:
                    with anyio.fail_after(5):
                        while True:
                            result = (await direct_session.call_tool("action_lookup", {"action_id": "denied-jog"})).structured_content
                            if result["state"] in {"failed", "completed", "unknown"}:
                                break
                            await anyio.sleep(.02)
                    # A broker HTTP error is conservatively represented as an
                    # unknown dispatched outcome by the Forge HTTP transport.
                    assert result["state"] in {"failed", "unknown"}, result
                    events = (telemetry.with_suffix(".events.jsonl")).read_text()
                    assert "operator choreography mode denies" in events, events
                else:
                    assert "operator choreography mode denies" in str(response), response
            return
        robot_path = tmp_path / "soarm.json"
        robot_path.write_text(json.dumps({
            "kind": "soarm101", "id": "arm",
            "provider": {
                "instance_id": "arm", "mode": "existing_broker", "broker_url": f"http://127.0.0.1:{port}",
                "sleep_check": False,  # simulated broker: no physical sleep pose
                # These cases exercise the urgent STOP path, so no grace to finish a gesture.
                "finish_on_interrupt_s": 0,
                "token_env": "SOARM101_BROKER_TOKEN",
                "trusted_host_token_env": "SOARM101_TRUSTED_HOST_TOKEN",
                "choreography_revisions": revisions,
                "choreography_durations_s": durations,
                "allowed_tools": ["robot_health", "robot_capabilities", "robot_state", "stop"],
            },
        }))
        room_path = tmp_path / "room.json"
        room_path.write_text(json.dumps({
            "schema_version": 4,
            "physical": True,
            "robots": [{"kind": "soarm101", "id": "arm", "file": str(robot_path)}],
            "action_journal": str(tmp_path / "actions.sqlite"),
            "ownership_db": str(tmp_path / "ownership.sqlite"),
            "presence_ttl_s": 2,
        }))
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "forge_puppeteer.ensemble.launcher", "--config", str(room_path)],
            env=child_env,
        )
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
            await session.initialize()
            capabilities = (await session.call_tool("ensemble_capabilities", {})).structured_content
            assert set(capabilities["performers"]) == {"arm"}
            assert "yes" in capabilities["performers"]["arm"]["semantic_intents"]
            if end_mode == "busy":
                # A second installed room shares the ownership database while
                # this room owns the performer. Its denied admission must not
                # send STOP to the first room's controller.
                first = await session.call_tool("ensemble_presence", {
                    "session_id": "owner", "event_id": "owner-0", "sequence": 0,
                    "state": "idle", "source": "manual", "interrupted": False,
                })
                assert not first.is_error
                with anyio.fail_after(5):
                    while not telemetry.exists():
                        await anyio.sleep(.02)
                before = [json.loads(line) for line in telemetry.read_text().splitlines()]
                async with stdio_client(params) as (r2, w2), ClientSession(r2, w2) as other:
                    await other.initialize()
                    denied = (await other.call_tool("ensemble_presence", {
                        "session_id": "contender", "event_id": "contender-0", "sequence": 0,
                        "state": "idle", "source": "manual", "interrupted": False,
                    })).structured_content
                    assert denied["performers"]["arm"]["status"] == "unavailable", denied
                    assert denied["performers"]["arm"]["evidence"] == "runtime_owner_or_resources_busy", denied
                after = [json.loads(line) for line in telemetry.read_text().splitlines()]
                assert not any(row["path"].endswith("/stop") for row in after[len(before):])
                return
            states = ("idle", "speaking") if end_mode == "stale" else ("idle", "speaking", "disconnected")
            for sequence, state in enumerate(states):
                result = await session.call_tool("ensemble_presence", {
                    "session_id": "trusted-simulation-room", "event_id": f"event-{sequence}",
                    "sequence": sequence, "state": state, "source": "manual",
                    "interrupted": state == "disconnected",
                })
                assert not result.is_error, result
                assert result.structured_content["state"] == state
                if state != "disconnected":
                    wanted_phase = "received" if state == "speaking" else "completed"
                    with anyio.fail_after(5):
                        while True:
                            records = ([json.loads(line) for line in telemetry.read_text().splitlines()]
                                       if telemetry.exists() else [])
                            matching = [
                                row for row in records
                                if row["path"].endswith("/choreography")
                                and row["request"].get("name") == state
                                and row["phase"] == wanted_phase
                            ]
                            if len(matching) >= (2 if state == "idle" and sequence == 2 else 1):
                                break
                            await anyio.sleep(0.02)

                    if state == "idle":
                        assert any(
                            row["path"].endswith("/choreography")
                            and row["phase"] == "completed"
                            and row["request"].get("name") == state
                            and row["status"] == 200
                            and row["body"]["result"]["revision"] == revisions[state]
                            and row["body"]["result"]["session_id"] == row["executor_session_id"]
                            and row["sdk_connect_count"] == 1
                            and row["body"]["result"]["preflight"]["gripper"] == "held_at_measured_start"
                            for row in records
                        ), records

                if state == "speaking" and end_mode not in {"stale", "unknown_stop"}:
                    yes = await session.call_tool("ensemble_intent", {
                        "intent": "yes", "action_id": "trusted-yes-0", "performer_ids": ["arm"],
                    })
                    assert not yes.is_error, yes
                    assert yes.structured_content["outcome"] == "accepted"
                    with anyio.fail_after(5):
                        while not any(
                            row["path"].endswith("/choreography")
                            and row["request"].get("name") == "yes"
                            and row["phase"] == "completed"
                            for row in ([json.loads(line) for line in telemetry.read_text().splitlines()]
                                        if telemetry.exists() else [])
                        ):
                            await anyio.sleep(0.02)

            # Let the real room heartbeat expire while its SDK-backed presence
            # loop is active; it must enter the stale state. STOP is required
            # only when work is potentially dispatched at expiration.
            if end_mode == "stale":
                with anyio.fail_after(6):
                    while not (await session.call_tool("ensemble_state", {})).structured_content["stale"]:
                        await anyio.sleep(0.05)
            if end_mode == "unknown_stop":
                state = (await session.call_tool("ensemble_state", {})).structured_content
                assert state["performers"]["arm"]["status"] == "blocked", state
                prior = [json.loads(line) for line in telemetry.read_text().splitlines()]
                assert any(row["path"].endswith("/stop") and row.get("status") == 503
                           for row in prior), prior
                retry = (await session.call_tool("ensemble_presence", {
                    "session_id": "new-room", "event_id": "retry", "sequence": 0,
                    "state": "idle", "source": "manual", "interrupted": False,
                })).structured_content
                assert retry["performers"]["arm"]["status"] == "blocked", retry
                intent = (await session.call_tool("ensemble_intent", {
                    "intent": "yes", "action_id": "no-rearm", "performer_ids": ["arm"],
                })).structured_content
                assert intent["outcome"] == "denied", intent
                await anyio.sleep(.2)
                later = [json.loads(line) for line in telemetry.read_text().splitlines()]
                assert sum(row["phase"] == "received" and row["path"].endswith("/choreography")
                           for row in later) == sum(
                               row["phase"] == "received" and row["path"].endswith("/choreography")
                               for row in prior)

        records = [json.loads(line) for line in telemetry.read_text().splitlines()]
        completed = [row for row in records if row["path"].endswith("/choreography")
                     and row["phase"] == "completed" and row["status"] == 200]
        if end_mode == "interrupt":
            assert {row["request"]["name"] for row in completed} >= {"idle", "speaking", "yes"}
        assert all(row["body"]["result"]["revision"] == revisions[row["request"]["name"]]
                   for row in completed)
        assert len({row["executor_session_id"] for row in records}) == 1
        assert {row["sdk_connect_count"] for row in records} == {1}
        assert {row["gripper_write_count"] for row in records} == {0}
        if end_mode in {"interrupt", "stale"}:
            assert any(row["path"].endswith("/stop") and row.get("status") == 200
                       and row["phase"] == "completed" for row in records)
        # The SDK trajectory path is explicitly joint-only; the private
        # choreography request has no gripper field and reports it held.
        assert all("gripper" not in row["request"] for row in completed)
        assert all(row["body"]["result"]["preflight"]["gripper"] == "held_at_measured_start"
                   for row in completed)
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
