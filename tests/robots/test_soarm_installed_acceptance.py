"""Real installed MCP/SDK simulation acceptance; never connects physical hardware.

Set SOARM_TEST_PYTHON to the clean wheel environment's Python. The two fault
cases inject ownership contention or a lost STOP receipt in the launcher,
while execution and STOP still use the real installed SDK simulator.
SDK simulation advances virtual time; a delayed completion receipt supplies
the deterministic in-flight window. These checks do not measure motor drift.
"""
import json
import math
import os
import socket
import sqlite3
from dataclasses import asdict
from pathlib import Path

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("scenario", [
    "coalesce", "gesture", "interrupt", "disconnect", "heartbeat", "busy", "lost_stop", "startup", "parking",
])
async def test_installed_soarm_boundaries(tmp_path, scenario):
    import anyio
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from soarm101_motion.choreography import (
        ChoreographyPolicy,
        JointChoreography,
        JointFrame,
        JointTarget,
    )

    python = os.environ.get("SOARM_TEST_PYTHON")
    if not python:
        pytest.skip("set SOARM_TEST_PYTHON to a matching installed-wheel environment")
    daemon = Path(python).with_name("soarm101-sim-daemon")
    assert daemon.is_file()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    programs = []
    for name, duration in (("idle", 8.0), ("speaking", 1.0), ("yes", 1.0)):
        frames = tuple(JointFrame(duration * i / 100,
            (.002 * (1 - math.cos(2 * math.pi * i / 100)), 0., 0., 0., 0.))
            for i in range(101))
        programs.append(JointChoreography.author(name, "so101", "simulation", frames))
    target = JointTarget.author("sleep2" if scenario == "parking" else "wake_to_neutral", "so101", "simulation", (0.,) * 5)
    if scenario == "parking":
        programs = []
    pins = tuple(p.revision for p in programs) + ((target.revision,) if scenario in {"startup", "parking"} else ())
    policy = ChoreographyPolicy("so101", "simulation", pins, max_duration_s=9)
    catalog = {"policy": asdict(policy), "programs": [asdict(p) for p in programs]}
    if scenario == "startup":
        catalog["startup_target"] = asdict(target)
    if scenario == "parking":
        catalog["targets"] = [asdict(target)]
    (tmp_path / "catalog.json").write_text(json.dumps(catalog))
    state_dir = tmp_path / "sdk-state"
    provider = {
        "instance_id": "arm", "mode": "sdk_simulation_daemon",
        "broker_url": f"http://127.0.0.1:{port}", "token_env": "ACCEPTANCE_TOKEN",
        "trusted_host_token_env": "ACCEPTANCE_HOST_TOKEN",
        "choreography_revisions": {p.name: p.revision for p in programs},
        "choreography_durations_s": {p.name: p.frames[-1].at_s for p in programs},
        "trusted_choreography_catalog": str(tmp_path / "catalog.json"),
        "simulation_state_dir": str(state_dir), "simulation_port": port,
        "sdk_simulation_executable": str(daemon),
        "allowed_tools": ["robot_capabilities", "robot_state", "stop"],
    }
    if scenario == "startup":
        provider["startup_target_revision"] = target.revision
    if scenario == "parking":
        provider["pose_targets"] = {target.name: target.revision}
    (tmp_path / "robot.json").write_text(json.dumps({"kind": "soarm101", "id": "arm", "provider": provider}))
    room = {
        "schema_version": 4, "physical": False,
        "robots": [{"kind": "soarm101", "id": "arm", "file": "robot.json"}],
        "action_journal": str(tmp_path / "actions.db"),
        "ownership_db": str(tmp_path / "owners.db"),
        "presence_ttl_s": 1 if scenario == "heartbeat" else 30,
    }
    room_file = tmp_path / "room.json"
    room_file.write_text(json.dumps(room))
    stop_log = tmp_path / "stops"
    # Assert the child really uses built packages, not the parent's source path.
    code = """
import pathlib
import forge_puppeteer, soarm101_motion
import anyio
assert 'site-packages' in forge_puppeteer.__file__, forge_puppeteer.__file__
assert 'site-packages' in soarm101_motion.__file__, soarm101_motion.__file__
from forge_puppeteer.devices.soarm101_trusted_http import SOARM101TrustedChoreographyHTTP as Transport
from forge_puppeteer.execution import StopResult
original_stop = Transport.stop
async def traced_stop(self):
    with open(STOP_LOG, 'a') as output:
        output.write('stop\\n')
    result = await original_stop(self)
    return StopResult(False, 'injected lost STOP receipt') if LOST_STOP else result
Transport.stop = traced_stop
original_execute = Transport.execute
async def delayed_receipt(self, request):
    result = await original_execute(self, request)
    if request.asset_id.endswith('/idle'):
        await anyio.sleep(8)
    return result
Transport.execute = delayed_receipt
if BUSY:
    from forge_puppeteer.ensemble import build as ensemble_build, config as ensemble_config
    original_build = ensemble_build.build_ensemble
    def busy_build(*args, **kwargs):
        runtime, packs, connected, extras = original_build(*args, **kwargs)
        runtime.acquire_control('arm', 'foreign-controller', 'foreign-session')
        return runtime, packs, connected, extras
    ensemble_build.build_ensemble = busy_build
from forge_puppeteer.ensemble.launcher import main
main()
"""
    code = (f"STOP_LOG={str(stop_log)!r}\nLOST_STOP={scenario == 'lost_stop'!r}\nBUSY={scenario == 'busy'!r}\n" + code)
    params = StdioServerParameters(command=python, args=["-c", code, "--config", str(room_file)],
        env={"PATH": str(Path(python).parent) + os.pathsep + os.defpath, "PYTHONPATH": ""})

    def records():
        with sqlite3.connect(tmp_path / "actions.db") as db:
            return [(json.loads(req), json.loads(rec)) for req, rec in db.execute("SELECT request, record FROM actions")]

    async def wait_record(name, expected):
        with anyio.fail_after(15):
            while True:
                matched = [r for req, r in records() if (req.get("asset_id") or "").endswith("/" + name)]
                if matched and matched[-1]["state"] == expected:
                    return
                await anyio.sleep(.025)

    async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as client:
        await client.initialize()
        sequence = 0

        async def presence(state="idle", interrupted=False):
            nonlocal sequence
            sequence += 1
            result = await client.call_tool("ensemble_presence", {
                "session_id": "acceptance", "event_id": f"event-{sequence}",
                "sequence": sequence, "state": state, "source": "manual", "interrupted": interrupted})
            assert not result.is_error, result
            return result.structured_content

        await presence()
        if scenario == "parking":
            assert records() == [], "presence must not wake or animate a parking-only room"
            result = await client.call_tool("ensemble_intent", {"intent": "sleep2",
                "action_id": "park-once", "performer_ids": ["arm"]})
            assert result.structured_content["outcome"] == "accepted"
            await wait_record("sleep2", "completed")
            assert len(records()) == 1
            await presence("disconnected")
        elif scenario == "busy":
            denied = await client.call_tool("ensemble_expression", {"expression": "yes",
                "action_id": "busy-gesture", "performer_ids": ["arm"]})
            assert denied.structured_content["outcome"] == "denied"
            assert records() == []
            await presence("disconnected")
            assert not stop_log.exists(), "foreign ownership must never trigger STOP"
        else:
            if scenario == "startup":
                await wait_record("wake_to_neutral", "completed")
            await wait_record("idle", "running")
            if scenario in {"coalesce", "gesture"}:
                start = anyio.current_time()
                await presence("speaking")
                await presence("idle")
                await presence("speaking")
                assert anyio.current_time() - start < 2, "normal transition blocked behind 8s motion"
                if scenario == "gesture":
                    start = anyio.current_time()
                    result = await client.call_tool("ensemble_expression", {"expression": "yes",
                        "action_id": "queued-gesture", "performer_ids": ["arm"]})
                    assert result.structured_content["outcome"] == "accepted"
                    assert anyio.current_time() - start < 2
                    await wait_record("yes", "completed")
                await wait_record("speaking", "completed")
                completed_idle = [r for req, r in records() if req["asset_id"].endswith("/idle") and r["state"] == "completed"]
                assert len(completed_idle) == 1, "superseded idle transition was replayed"
                assert not stop_log.exists(), "normal transition must not issue STOP"
            elif scenario == "heartbeat":
                with anyio.move_on_after(5):
                    while not stop_log.exists():
                        await anyio.sleep(.025)
                result = await client.call_tool("ensemble_state", {})
                assert stop_log.exists(), (result.structured_content, records())
                assert result.structured_content["state"] == "disconnected"
                assert result.structured_content["stale"] is True
            elif scenario in {"interrupt", "disconnect", "lost_stop"}:
                start = anyio.current_time()
                result = await presence("disconnected" if scenario == "disconnect" else "listening", True)
                assert anyio.current_time() - start < 3, "urgent STOP waited for routine completion"
                assert stop_log.exists()
                if scenario == "lost_stop":
                    assert result["performers"]["arm"]["status"] == "blocked"
                    count = len(records())
                    await presence("speaking")
                    await anyio.sleep(.2)
                    assert len(records()) == count, "uncertain STOP automatically rearmed"
            await presence("disconnected")
    assert not (state_dir / "daemon.json").exists()


@pytest.mark.anyio
@pytest.mark.parametrize("operator_only", [False, True])
async def test_installed_broad_mcp_respects_sdk_operator_mode(tmp_path, operator_only):
    """Keep the same advertised grant while the real SDK fences agent writes.

    The fixture switches the SDK admission flag after simulated connection,
    because constructing a physical session on a simulator is prohibited.
    """
    import secrets
    import subprocess
    import urllib.request

    import anyio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    python = os.environ.get("SOARM_TEST_PYTHON")
    if not python:
        pytest.skip("set SOARM_TEST_PYTHON to a matching installed-wheel environment")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    token = secrets.token_urlsafe(24)
    host_token = secrets.token_urlsafe(24)
    fixture = Path(__file__).with_name("soarm_trusted_sim_broker.py")
    env = {"PATH": str(Path(python).parent) + os.pathsep + os.defpath, "PYTHONPATH": "",
        "SOARM101_BROKER_TOKEN": token, "SOARM101_TRUSTED_HOST_TOKEN": host_token,
        "SOARM_TEST_OPERATOR_ONLY": "1" if operator_only else "0"}
    # This fixture is instrumentation around installed SDK classes; it has no
    # physical backend. Neither server imports either repository's src tree.
    code = ("import runpy, soarm101_motion; "
        "assert 'site-packages' in soarm101_motion.__file__; "
        f"runpy.run_path({str(fixture)!r}, run_name='__main__')")
    broker = await anyio.to_thread.run_sync(lambda: subprocess.Popen(
        [python, "-c", code, str(port), str(tmp_path / "telemetry.jsonl")],
        env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE))
    url = f"http://127.0.0.1:{port}"

    def healthy():
        try:
            req = urllib.request.Request(url + "/v1/health", headers={"Authorization": "Bearer " + token})
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=.2) as res:
                return res.status == 200
        except OSError:
            return False

    try:
        with anyio.fail_after(8):
            while not await anyio.to_thread.run_sync(healthy):
                assert broker.poll() is None, broker.stderr.read().decode()
                await anyio.sleep(.05)
        cfg = {"backend": "soarm101_broker", "device": "soarm101", "instance_id": "arm",
            "simulation_only": True, "broker_url": url, "broker_token_env": "SOARM101_BROKER_TOKEN",
            "allowed_tools": ["robot_health", "robot_capabilities", "robot_state", "jog_joint", "stop"],
            "action_journal": str(tmp_path / "direct-actions.db"),
            "ownership_db": str(tmp_path / "direct-owners.db")}
        config = tmp_path / "direct.json"
        config.write_text(json.dumps(cfg))
        params = StdioServerParameters(command=str(Path(python).with_name("forge-puppeteer-mcp")),
            args=["--config", str(config)], env=env)
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as client:
            await client.initialize()
            result = await client.call_tool("jog_joint", {
                "action_id": "bounded-jog", "caller_id": "acceptance", "session_id": "broad",
                "parameters": {"joint": "shoulder_pan", "delta_deg": .5}})
            if result.is_error:
                assert operator_only, result
            else:
                with anyio.fail_after(8):
                    while True:
                        terminal = (await client.call_tool("action_lookup", {"action_id": "bounded-jog"})).structured_content
                        if terminal["state"] in {"completed", "failed", "unknown", "cancelled"}:
                            break
                        await anyio.sleep(.025)
                if operator_only:
                    # The HTTP adapter conservatively classifies an SDK error
                    # after dispatch as unknown; it must never claim movement.
                    assert terminal["state"] in {"failed", "unknown"}, terminal
                else:
                    assert terminal["state"] == "completed", terminal
            # Rejection must leave read access usable in both modes.
            state = await client.call_tool("device_state", {})
            assert not state.is_error
            if operator_only:
                assert all(value == 0 for value in state.structured_content["result"]["joint_positions_rad"].values())
                events = [json.loads(line) for line in (tmp_path / "telemetry.events.jsonl").read_text().splitlines()]
                assert any(event.get("error") and "operator" in str(event["error"]).lower() for event in events)
    finally:
        broker.terminate()
        await anyio.to_thread.run_sync(lambda: broker.wait(timeout=5))
        broker.stderr.close()
