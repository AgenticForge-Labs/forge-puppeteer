"""Hardware-free schema-v4 file selection and admission checks."""
import json
import os
import sys
from contextlib import asynccontextmanager

import pytest

from forge_puppeteer.ensemble.launcher import config_readiness


def _sim_daemon_executable():
    """SOARM_TEST_SIM_DAEMON, else the daemon installed next to this Python."""
    import shutil
    from pathlib import Path
    explicit = os.environ.get("SOARM_TEST_SIM_DAEMON")
    if explicit:
        return explicit
    sibling = Path(sys.executable).parent / "soarm101-sim-daemon"
    return str(sibling) if sibling.is_file() else (shutil.which("soarm101-sim-daemon") or "")


def provider(kind, ident, tmp_path):
    if kind == "reachy":
        return {"backend": "reachy_sdk", "device": kind, "instance_id": ident,
                "allowed_tools": ["head_pose", "play_move", "device_stop"],
                "expected_daemon_hardware_id": "reviewed-hardware",
                "host": "127.0.0.1", "connection_mode": "network", "sdk_version": "1.0",
                "asset_root": str(tmp_path), "manifest": str(tmp_path / "manifest.json")}
    if kind == "stackchan":
        return {"backend": "stackchan_sdk", "device": kind, "instance_id": ident,
                "allowed_tools": ["head_move", "face_update", "device_stop"],
                "port": "/dev/ttyACM0", "sdk_version": "0.3.0"}
    return {"instance_id": ident, "mode": "existing_broker", "broker_url": "http://127.0.0.1:8765",
            "token_env": "SOARM_TOKEN",
            "allowed_tools": ["robot_capabilities", "robot_state", "stop"]}


@pytest.mark.parametrize("selected", [
    ("reachy",), ("stackchan",), ("soarm101",),
    ("reachy", "stackchan"), ("reachy", "soarm101"),
    ("stackchan", "soarm101"), ("reachy", "stackchan", "soarm101"),
])
def test_v4_config_accepts_all_selected_combinations_with_relative_robot_paths(tmp_path, selected):
    refs = []
    for kind in selected:
        ident = f"robot-{kind}"
        (tmp_path / f"{kind}.json").write_text(json.dumps(
            {"kind": kind, "id": ident, "provider": provider(kind, ident, tmp_path)}))
        refs.append({"kind": kind, "id": ident, "file": f"{kind}.json"})
    room = {"schema_version": 4, "physical": True,
            "robots": refs, "action_journal": str(tmp_path / "actions.db"),
            "ownership_db": str(tmp_path / "owners.db")}
    if "reachy" in selected:
        room["reachy_neutral_head"] = str(tmp_path / "neutral.json")
    path = tmp_path / "room.json"
    path.write_text(json.dumps(room))
    result = config_readiness(path)
    assert result["performers"] == [f"robot-{kind}" for kind in selected]


def test_v4_rejects_duplicate_kind_id_and_reference_identity_mismatch(tmp_path):
    ref = {"kind": "stackchan", "id": "stage", "file": "robot.json"}
    (tmp_path / "robot.json").write_text(json.dumps(
        {"kind": "stackchan", "id": "stage", "provider": provider("stackchan", "stage", tmp_path)}))
    room = {"schema_version": 4, "physical": True,
            "robots": [ref, ref], "action_journal": str(tmp_path / "a.db"),
            "ownership_db": str(tmp_path / "o.db")}
    path = tmp_path / "room.json"
    path.write_text(json.dumps(room))
    with pytest.raises(ValueError, match="unique"):
        config_readiness(path)
    room["robots"] = [{**ref, "id": "different"}]
    path.write_text(json.dumps(room))
    with pytest.raises(ValueError, match="identity mismatch"):
        config_readiness(path)


def test_stackchan_only_assembly_uses_one_provider_and_never_imports_soarm(tmp_path, monkeypatch):
    from forge_puppeteer.ensemble import build as ensemble_build
    from forge_puppeteer.ensemble import config as ensemble_config
    from forge_puppeteer.mcp import launcher
    from forge_puppeteer.mcp.launcher import ServerConfig

    config = ServerConfig.model_validate(provider("stackchan", "stage", tmp_path))
    room = ensemble_config.EnsembleConfig.model_validate({
        "schema_version": 4,
        "physical": True,
        "robots": [{"kind": "stackchan", "id": "stage", "file": "unused"}],
        "action_journal": str(tmp_path / "actions.db"),
        "ownership_db": str(tmp_path / "owners.db"),
    })

    class FakeProvider:
        def __init__(self, cfg):
            self.config = cfg

        @asynccontextmanager
        async def connected(self):
            yield

    monkeypatch.setattr(launcher, "StackChanSDKProvider", FakeProvider)
    before = "soarm101_motion.broker_sdk" in sys.modules
    runtime, packs, connected, _extras = ensemble_build.build_ensemble(room, {"stackchan": config})
    assert set(runtime._providers) == {"stage"}
    assert [pack.performer_id for pack in packs] == ["stage"]
    assert ("soarm101_motion.broker_sdk" in sys.modules) == before

    async def enter():
        async with connected():
            pass
    import anyio
    anyio.run(enter)


def test_soarm_only_assembly_selects_broker_provider_without_sdk_executor(tmp_path, monkeypatch):
    from forge_puppeteer.ensemble import build as ensemble_build
    from forge_puppeteer.ensemble import config as ensemble_config

    room = ensemble_config.EnsembleConfig.model_validate({
        "schema_version": 4,
        "physical": True,
        "robots": [{"kind": "soarm101", "id": "arm", "file": "unused"}],
        "action_journal": str(tmp_path / "actions.db"),
        "ownership_db": str(tmp_path / "owners.db"),
    })

    from forge_puppeteer.devices.soarm101 import SOARM101Provider
    from forge_puppeteer.devices.soarm101_broker_http import SOARM101BrokerHTTP
    from forge_puppeteer.ensemble.config import SOARMBrokerConfig
    config = SOARMBrokerConfig.model_validate(provider("soarm101", "arm", tmp_path))

    @asynccontextmanager
    async def connected():
        yield

    monkeypatch.setenv("SOARM_TOKEN", "software-test-token")
    runtime, packs, _connect, _extras = ensemble_build.build_ensemble(room, {"soarm101": config})
    assert set(runtime._providers) == {"arm"}
    assert len(packs) == 1
    assert set(packs[0].behaviors) == {"idle", "listening", "speaking"}
    assert all(pattern.mode == "hold" and not pattern.cues
               for pattern in packs[0].behaviors.values())
    assert isinstance(runtime._providers["arm"], SOARM101Provider)
    assert isinstance(runtime._providers["arm"].session, SOARM101BrokerHTTP)


def test_v4_rejects_saved_pose_presence_and_keeps_position_hold_patterns():
    from forge_puppeteer.ensemble.build import _soarm_broker_pack
    from forge_puppeteer.ensemble.config import SOARMBrokerConfig

    base = {"instance_id": "arm", "mode": "existing_broker", "broker_url": "http://127.0.0.1:8765",
            "token_env": "SOARM_TOKEN",
            "allowed_tools": ["robot_capabilities", "robot_state", "stop"]}
    with pytest.raises(ValueError, match="presence_poses"):
        SOARMBrokerConfig.model_validate({**base, "presence_poses": {
            "idle": "Idle", "listening": "Listen", "speaking": "Speak"}})

    config = SOARMBrokerConfig.model_validate(base)
    pack = _soarm_broker_pack(config)
    assert set(pack.behaviors) == {"idle", "listening", "speaking"}
    assert all(item.mode == "hold" and not item.cues for item in pack.behaviors.values())


def test_pinned_pose_intent_never_becomes_automatic_presence():
    from forge_puppeteer.ensemble.build import _soarm_broker_pack
    from forge_puppeteer.ensemble.config import SOARMBrokerConfig
    revision = "sha256:" + "c" * 64
    config = SOARMBrokerConfig.model_validate({"instance_id": "arm", "mode": "existing_broker",
        "broker_url": "http://127.0.0.1:8765", "token_env": "TOKEN", "trusted_host_token_env": "HOST",
        "allowed_tools": ["robot_capabilities", "robot_state", "stop"], "pose_targets": {"sleep2": revision}})
    pack = _soarm_broker_pack(config)
    assert pack.startup is None
    assert all(pack.behaviors[name].mode == "hold" for name in ("idle", "listening", "speaking"))
    assert pack.behaviors["sleep2"].mode == "once"
    assert pack.behaviors["sleep2"].cues[0].asset_revision == revision
    assert pack.behaviors["sleep2"].cues[0].operation == "play_joint_target"


def test_v4_presence_uses_only_explicit_private_host_revision_pins():
    from forge_puppeteer.ensemble.build import _soarm_broker_pack
    from forge_puppeteer.ensemble.config import SOARMBrokerConfig

    pin = "sha256:" + "a" * 64
    config = SOARMBrokerConfig.model_validate({
        "instance_id": "arm", "mode": "existing_broker", "broker_url": "http://127.0.0.1:8765",
        "token_env": "SOARM_TOKEN", "trusted_host_token_env": "SOARM_HOST_TOKEN",
        "choreography_revisions": {"idle": pin, "speaking": "sha256:" + "b" * 64},
        "choreography_durations_s": {"idle": 3.25, "speaking": 4.5},
        "allowed_tools": ["robot_capabilities", "robot_state", "stop"],
    })
    pack = _soarm_broker_pack(config)
    assert pack.behaviors["idle"].cues[0].asset_revision == pin
    assert pack.behaviors["idle"].period_s == 3.25
    assert pack.behaviors["speaking"].period_s == 4.5
    assert pack.behaviors["speaking"].cues[0].asset_id == "soarm101/choreography/speaking"
    assert pack.behaviors["listening"].cues[0].asset_id == "soarm101/choreography/idle"


def test_v4_explicit_expression_pins_become_one_shot_semantic_intents():
    from forge_puppeteer.ensemble.build import _soarm_broker_pack
    from forge_puppeteer.ensemble.config import SOARMBrokerConfig

    names = ("idle", "speaking", "yes", "no", "acknowledge", "neutral", "confused", "surprised")
    pins = {name: "sha256:" + format(index + 1, "064x") for index, name in enumerate(names)}
    config = SOARMBrokerConfig.model_validate({
        "instance_id": "arm", "mode": "existing_broker", "broker_url": "http://127.0.0.1:8765",
        "token_env": "SOARM_TOKEN", "trusted_host_token_env": "SOARM_HOST_TOKEN",
        "choreography_revisions": pins,
        "choreography_durations_s": {name: 0.4 for name in names},
        "allowed_tools": ["robot_capabilities", "robot_state", "stop"],
    })
    pack = _soarm_broker_pack(config)
    assert {name for name, pattern in pack.behaviors.items() if pattern.mode == "once"} == set(names[2:])
    for name in names[2:]:
        pattern = pack.behaviors[name]
        assert pattern.cues[0].asset_revision == pins[name]
        assert pattern.cues[0].asset_id == f"soarm101/choreography/{name}"
        assert pattern.max_duration_s == 0.4
    assert pack.behaviors["speaking"].mode == "loop"


def test_v4_rejects_unrecognized_choreography_names():
    from forge_puppeteer.ensemble.config import SOARMBrokerConfig
    base = {"instance_id": "arm", "mode": "existing_broker", "broker_url": "http://127.0.0.1:8765",
            "token_env": "SOARM_TOKEN", "trusted_host_token_env": "SOARM_HOST_TOKEN",
            "choreography_revisions": {"idle": "sha256:" + "a" * 64,
                                        "speaking": "sha256:" + "b" * 64,
                                        "dance": "sha256:" + "c" * 64},
            "choreography_durations_s": {"idle": 1, "speaking": 1, "dance": 1},
            "allowed_tools": ["robot_capabilities", "robot_state", "stop"]}
    with pytest.raises(ValueError, match="exact revision pins"):
        SOARMBrokerConfig.model_validate(base)


def test_v4_trusted_presence_requires_duration_for_each_pin():
    from forge_puppeteer.ensemble.config import SOARMBrokerConfig
    base = {"instance_id": "arm", "mode": "existing_broker", "broker_url": "http://127.0.0.1:8765",
            "token_env": "SOARM_TOKEN", "trusted_host_token_env": "SOARM_HOST_TOKEN",
            "choreography_revisions": {"idle": "sha256:" + "a" * 64,
                                        "speaking": "sha256:" + "b" * 64},
            "allowed_tools": ["robot_capabilities", "robot_state", "stop"]}
    with pytest.raises(ValueError, match="durations"):
        SOARMBrokerConfig.model_validate(base)


def test_sdk_owned_simulation_launch_uses_real_sdk_daemon_and_cleans_up(tmp_path):
    """Exercise the SDK process lifecycle, not a fake broker or HTTP server."""
    import json
    import os
    import urllib.request

    from forge_puppeteer.ensemble.build import start_owned_simulation
    from forge_puppeteer.ensemble.config import SOARMBrokerConfig

    executable = _sim_daemon_executable()
    if not executable or not os.path.isfile(executable):
        pytest.skip("local SO-ARM SDK simulation executable is unavailable")
    # A per-test ephemeral loopback port avoids collisions with operator daemons.
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = SOARMBrokerConfig.model_validate({
        "instance_id": "sim-arm", "mode": "sdk_simulation_daemon",
        "broker_url": f"http://127.0.0.1:{port}", "token_env": "TEST_SOARM_TOKEN",
        "simulation_state_dir": str(tmp_path / "sdk-state"),
        "simulation_port": port, "sdk_simulation_executable": executable,
        "allowed_tools": ["robot_capabilities", "robot_state", "stop"],
    })
    (token, _trusted), cleanup = start_owned_simulation(config)
    try:
        request = urllib.request.Request(
            config.broker_url + "/v1/health",
            headers={"Authorization": "Bearer " + token},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            health = json.loads(response.read())
        assert health["ok"] is True
        assert (tmp_path / "sdk-state" / "daemon.json").is_file()
    finally:
        cleanup()
    assert not (tmp_path / "sdk-state" / "daemon.json").exists()


@pytest.mark.anyio
async def test_v4_real_stdio_mcp_simulation_idle_speaking_stop_and_cleanup(tmp_path):
    """Run the actual v4 launcher and SDK daemon through MCP stdio."""
    import os
    import socket
    import sys

    import anyio
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    executable = _sim_daemon_executable()
    if not executable or not os.path.isfile(executable):
        pytest.skip("local SO-ARM SDK simulation executable is unavailable")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    pytest.importorskip("soarm101_motion.choreography")
    from soarm101_motion.choreography import (
        ChoreographyPolicy,
        JointChoreography,
        JointFrame,
    )
    state = tmp_path / "sdk-state"
    robot_id, calibration_id = "so101", "simulation"
    # Sample a cosine ease at the SDK command cadence. This has zero slope at
    # neutral and at the reversal, returns to the calibrated neutral, and gives
    # the SDK enough points to validate acceleration across the full path.
    def smooth_joint_frames(target, duration, frequency_hz=25):
        import math
        count = round(duration * frequency_hz)
        return tuple(
            JointFrame(duration * index / count,
                       (target * (1 - math.cos(2 * math.pi * index / count)) / 2,
                        0, 0, 0, 0))
            for index in range(count + 1)
        )

    def eased(name, target, duration):
        return JointChoreography.author(name, robot_id, calibration_id, (
            *smooth_joint_frames(target, duration),
        ))
    programs = [eased("idle", 0.006, 3.0), eased("speaking", 0.01, 4.5)]
    policy = ChoreographyPolicy(robot_id, calibration_id, tuple(p.revision for p in programs),
                                max_duration_s=6, max_speed_deg_s=8, max_acceleration_deg_s2=25)
    catalog = tmp_path / "trusted-catalog.json"
    catalog.write_text(json.dumps({"policy": {
        "robot_id": policy.robot_id, "calibration_id": policy.calibration_id,
        "allowed_revisions": list(policy.allowed_revisions), "max_duration_s": policy.max_duration_s,
        "max_samples": policy.max_samples, "max_start_error_rad": policy.max_start_error_rad,
        "max_speed_deg_s": policy.max_speed_deg_s,
        "max_acceleration_deg_s2": policy.max_acceleration_deg_s2,
        "allow_physical_trial": False,
    }, "programs": [{"name": p.name, "robot_id": p.robot_id,
        "calibration_id": p.calibration_id, "revision": p.revision,
        "frames": [{"at_s": f.at_s, "joints_rad": list(f.joints_rad)} for f in p.frames]}
        for p in programs]}))
    pins = {p.name: p.revision for p in programs}
    durations = {p.name: p.frames[-1].at_s for p in programs}
    robot = tmp_path / "soarm.json"
    robot.write_text(json.dumps({"kind": "soarm101", "id": "sim-arm", "provider": {
        "instance_id": "sim-arm", "mode": "sdk_simulation_daemon",
        "broker_url": f"http://127.0.0.1:{port}", "token_env": "TEST_SOARM_TOKEN",
        "trusted_host_token_env": "TEST_SOARM_HOST_TOKEN",
        "choreography_revisions": pins, "choreography_durations_s": durations,
        "trusted_choreography_catalog": str(catalog),
        "simulation_state_dir": str(state), "simulation_port": port,
        "sdk_simulation_executable": executable,
        "allowed_tools": ["robot_capabilities", "robot_state", "stop"],
    }}))
    room = tmp_path / "room.json"
    room.write_text(json.dumps({"schema_version": 4,
        "physical": True,
        "robots": [{"kind": "soarm101", "id": "sim-arm", "file": str(robot)}],
        "action_journal": str(tmp_path / "actions.db"),
        "ownership_db": str(tmp_path / "owners.db")}))
    root = str(__import__("pathlib").Path(__file__).resolve().parents[2])
    code = f"from forge_puppeteer.ensemble.launcher import main; main(['--config', {str(room)!r}])"
    params = StdioServerParameters(command=sys.executable, args=["-c", code],
        env={**os.environ, "PYTHONPATH": root + os.pathsep + os.environ.get("PYTHONPATH", "")})
    try:
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
            await session.initialize()
            caps = await session.call_tool("ensemble_capabilities", {})
            assert caps.structured_content["performers"]["sim-arm"]["status"] == "idle"
            import sqlite3
            journal = tmp_path / "actions.db"

            def action_state(asset_name):
                with sqlite3.connect(journal) as db:
                    rows = db.execute("SELECT request, record FROM actions").fetchall()
                matches = [(json.loads(req), json.loads(record)) for req, record in rows
                           if json.loads(req).get("asset_id", "").endswith("/" + asset_name)]
                return matches[-1][1].get("state") if matches else None

            async def wait_for_action(asset_name, expected):
                with anyio.fail_after(8):
                    while action_state(asset_name) != expected:
                        await anyio.sleep(0.025)

            idle = await session.call_tool("ensemble_presence", {
                "session_id": "sim-room", "event_id": "event-0",
                "sequence": 0, "state": "idle", "source": "manual"})
            assert idle.structured_content["outcome"] == "accepted"
            await wait_for_action("idle", "completed")
            # The SDK reports the returned neutral pose before the next
            # semantic action is admitted.
            # The SDK simulation applies a complete trajectory synchronously;
            # use journal completion as execution evidence before STOP instead
            # of expecting an observable intermediate motor position.
            import urllib.request
            broker_token = (state / "broker.token").read_text().strip()
            host_token = (state / "trusted-host.token").read_text().strip()
            assert broker_token and host_token and broker_token != host_token
            catalog_req = urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/trusted/catalog",
                headers={"Authorization": f"Bearer {host_token}"})
            def read_catalog():
                with urllib.request.urlopen(catalog_req, timeout=2) as response:
                    return json.loads(response.read())["result"]["programs"]
            catalog_result = await anyio.to_thread.run_sync(read_catalog)
            assert {item["name"]: item["revision"] for item in catalog_result} == pins
            speaking = await session.call_tool("ensemble_presence", {
                "session_id": "sim-room", "event_id": "event-1",
                "sequence": 1, "state": "speaking", "source": "manual"})
            assert speaking.structured_content["outcome"] == "accepted"
            await wait_for_action("speaking", "completed")
            state_req = urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/state",
                headers={"Authorization": f"Bearer {broker_token}"})
            def read_state():
                with urllib.request.urlopen(state_req, timeout=2) as response:
                    return json.loads(response.read())["result"]
            simulated_state = await anyio.to_thread.run_sync(read_state)
            assert simulated_state["broker_session"]["mode"] == "simulation_actions"
            stopped = await session.call_tool("ensemble_presence", {
                "session_id": "sim-room", "event_id": "event-stop", "sequence": 2,
                "state": "disconnected", "source": "manual", "interrupted": True})
            assert stopped.structured_content["state"] == "disconnected"
    finally:
        with anyio.fail_after(3):
            while (state / "daemon.json").exists():
                await anyio.sleep(0.02)
    assert not (state / "broker.token").exists()
    assert not (state / "trusted-host.token").exists()
    assert not (state / "daemon.json").exists()


@pytest.mark.parametrize("url", ["http://example.com:8765", "http://user@127.0.0.1:8765",
                                  "http://127.0.0.1:8765/redirect", "http://127.0.0.1:8765?x=1"])
def test_v4_rejects_non_loopback_or_ambiguous_broker_urls(url):
    from forge_puppeteer.ensemble.config import SOARMBrokerConfig
    with pytest.raises(ValueError, match="loopback"):
        SOARMBrokerConfig.model_validate({"instance_id": "arm", "mode": "existing_broker", "broker_url": url,
            "token_env": "SOARM_TOKEN",
            "allowed_tools": ["robot_capabilities", "robot_state", "stop"]})


def test_physical_daemon_supervision_uses_private_tokens_and_only_child_sigterm(tmp_path, monkeypatch):
    import json
    import os

    from forge_puppeteer.ensemble import build as ensemble_build
    from forge_puppeteer.ensemble import config as ensemble_config

    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    catalog = tmp_path / "catalog.json"
    revision = "sha256:" + "a" * 64
    catalog.write_text(json.dumps({"policy": {"robot_id": "hardware-arm", "calibration_id": "cal-1",
        "allow_physical_trial": True}, "programs": [{"name": "idle", "revision": revision},
            {"name": "speaking", "revision": "sha256:" + "b" * 64}]}))
    daemon_path = tmp_path / "physical.json"
    daemon_path.write_text(json.dumps({"physical": True, "robot_id": "hardware-arm", "port": "/dev/unused",
        "calibration_id": "cal-1", "lease_path": str(tmp_path / "lease"),
        "operator_choreography_only": True,
        "trusted_catalog_path": str(catalog), "loopback_port": 9876, "state_dir": str(state)}))
    cfg = ensemble_config.SOARMBrokerConfig.model_validate({"instance_id": "arm", "mode": "sdk_physical_daemon",
        "broker_url": "http://127.0.0.1:9876", "token_env": "UNUSED",
        "trusted_host_token_env": "UNUSED_HOST", "trusted_choreography_catalog": str(catalog),
        "physical_daemon_config": str(daemon_path), "sdk_physical_executable": "/fake/physical-daemon",
        "startup_target_revision": revision,
        "choreography_revisions": {"idle": revision, "speaking": "sha256:" + "b" * 64},
        "choreography_durations_s": {"idle": 1, "speaking": 1},
        "allowed_tools": ["robot_capabilities", "robot_state", "stop"]})

    children = []
    class Child:
        pid = 4242
        signalled = None
        def __init__(self, *args, **kwargs):
            children.append(self)
            self.returncode = None
            (state / "broker.token").write_text("broker-secret")
            (state / "trusted-host.token").write_text("host-secret")
            os.chmod(state / "broker.token", 0o600)
            os.chmod(state / "trusted-host.token", 0o600)
            (state / "daemon.json").write_text(json.dumps({"pid": self.pid, "port": 9876,
                "robot_id": "hardware-arm", "physical": True}))
        def poll(self): return self.returncode
        def send_signal(self, sig): self.signalled = sig
        def wait(self, timeout=None): self.returncode = 0; return 0
    monkeypatch.setattr(ensemble_build.subprocess, "Popen", Child)

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return json.dumps({"ok": True, "result": {"programs": [
            {"name": "idle", "revision": revision},
            {"name": "speaking", "revision": "sha256:" + "b" * 64}]}}).encode()
    class Opener:
        def open(self, req, timeout=None): return Response()
    monkeypatch.setattr(ensemble_build.urllib.request, "build_opener", lambda *args: Opener())
    (tokens, cleanup) = ensemble_build.start_owned_physical(cfg)
    assert tokens == ("broker-secret", "host-secret")
    cleanup()
    assert children[0].signalled == __import__("signal").SIGTERM


def test_v4_accepts_long_presence_and_requires_a_wake_to_neutral_target_for_physical(tmp_path):
    from forge_puppeteer.ensemble.config import SOARMBrokerConfig
    base = {"instance_id": "arm", "mode": "sdk_physical_daemon",
        "broker_url": "http://127.0.0.1:9876", "token_env": "TOKEN",
        "trusted_host_token_env": "HOST_TOKEN", "trusted_choreography_catalog": str(tmp_path / "catalog.json"),
        "physical_daemon_config": str(tmp_path / "daemon.json"),
        "choreography_revisions": {"idle": "sha256:" + "a" * 64, "speaking": "sha256:" + "b" * 64},
        "choreography_durations_s": {"idle": 1, "speaking": 1},
        "allowed_tools": ["robot_capabilities", "robot_state", "stop"]}
    with pytest.raises(ValueError, match="startup_target_revision"):
        SOARMBrokerConfig.model_validate(base)
    target = {"startup_target_revision": "sha256:" + "c" * 64}
    SOARMBrokerConfig.model_validate({**base, **target,
        "choreography_durations_s": {"idle": 19, "speaking": 1}})
    SOARMBrokerConfig.model_validate({**base, **target})


def test_v4_startup_target_is_pinned_and_needs_choreography():
    from forge_puppeteer.ensemble.build import _soarm_broker_pack
    from forge_puppeteer.ensemble.config import SOARMBrokerConfig
    pin = 'sha256:' + 'a' * 64
    base = {'instance_id': 'arm', 'mode': 'existing_broker',
        'broker_url': 'http://127.0.0.1:8765', 'token_env': 'TOKEN',
        'trusted_host_token_env': 'HOST', 'allowed_tools': ['robot_capabilities', 'robot_state', 'stop'],
        'choreography_revisions': {'idle': pin, 'speaking': pin},
        'choreography_durations_s': {'idle': 1, 'speaking': 1},
        'startup_target_revision': pin}
    cfg = SOARMBrokerConfig.model_validate(base)
    cue = _soarm_broker_pack(cfg).startup.cues[0]
    assert cue.operation == 'wake_to_neutral' and cue.asset_revision == pin
    no_programs = {k: v for k, v in base.items() if not k.startswith('choreography')}
    with pytest.raises(ValueError, match='startup target requires trusted choreography'):
        SOARMBrokerConfig.model_validate(no_programs)


def test_stale_daemon_state_is_cleared_but_running_daemon_blocks(tmp_path, monkeypatch):
    import json as _json
    import os as _os

    import pytest as _pytest

    from forge_puppeteer.ensemble import build as ensemble_build

    def seed(pid):
        (tmp_path / "daemon.json").write_text(_json.dumps({"pid": pid}))
        (tmp_path / "broker.token").write_text("t")
        (tmp_path / "trusted-host.token").write_text("h")

    seed(2**22 + 1)  # above default pid_max: never alive
    ensemble_build.clear_stale_daemon_state(tmp_path, "SDK physical")
    assert not any(tmp_path.iterdir())

    (tmp_path / "broker.token").write_text("orphan")  # token without record
    ensemble_build.clear_stale_daemon_state(tmp_path, "SDK physical")
    assert not any(tmp_path.iterdir())

    seed(_os.getpid())
    monkeypatch.setattr(ensemble_build, "_daemon_process_alive", lambda pid: True)
    with _pytest.raises(RuntimeError, match="already running"):
        ensemble_build.clear_stale_daemon_state(tmp_path, "SDK physical")
    assert (tmp_path / "daemon.json").exists()


def test_sleep_target_must_name_a_configured_pose_target():
    from forge_puppeteer.ensemble.config import SOARMBrokerConfig

    base = provider("soarm101", "arm", None)
    revision = "sha256:" + "a" * 64
    with pytest.raises(ValueError, match="sleep_target"):
        SOARMBrokerConfig(**base, sleep_target="sleep2")
    config = SOARMBrokerConfig(**base, pose_targets={"sleep2": revision},
                               trusted_host_token_env="SOARM_TRUSTED",
                               sleep_target="sleep2")
    assert config.sleep_target == "sleep2"


def test_startup_gate_covers_real_robots_and_skips_simulation_or_opt_out(tmp_path):
    from types import SimpleNamespace

    from forge_puppeteer.ensemble.build import build_startup_gate

    class Reader:
        async def read_pose(self):  # pragma: no cover - never called here
            raise AssertionError

    class Runtime:
        def _provider(self, ref_id):
            return Reader()

    class Ensemble:
        def __init__(self):
            self.packs = {"arm": object(), "sim": object(), "off": object()}

    def room_of(*ids):
        return SimpleNamespace(robots=[SimpleNamespace(id=i, kind="soarm101") for i in ids])

    configs = {"soarm101": SimpleNamespace(mode="existing_broker", sleep_check=True,
                                           sleep_target=None, pose_targets={})}
    gate = build_startup_gate(room_of("arm"), configs, Runtime(), Ensemble())
    assert set(gate.readers) == {"arm"}
    for mode, check in (("sdk_simulation_daemon", True), ("existing_broker", False)):
        skipped = {"soarm101": SimpleNamespace(mode=mode, sleep_check=check,
                                               sleep_target=None, pose_targets={})}
        assert build_startup_gate(room_of("arm"), skipped, Runtime(), Ensemble()) is None


def _room_files(tmp_path, kinds, *, physical=True, provider_extra=None):
    refs = []
    for kind in kinds:
        data = provider(kind, f"robot-{kind}", tmp_path)
        data.update((provider_extra or {}).get(kind, {}))
        (tmp_path / f"{kind}.json").write_text(json.dumps(
            {"kind": kind, "id": f"robot-{kind}", "provider": data}))
        refs.append({"kind": kind, "id": f"robot-{kind}", "file": f"{kind}.json"})
    room = {"schema_version": 4, "robots": refs,
            "action_journal": str(tmp_path / "actions.db"), "ownership_db": str(tmp_path / "owners.db")}
    if physical is not None:
        room["physical"] = physical
    if "reachy" in kinds:
        room["reachy_neutral_head"] = str(tmp_path / "neutral.json")
    path = tmp_path / "room.json"
    path.write_text(json.dumps(room))
    return path


@pytest.mark.parametrize("kind", ["reachy", "stackchan"])
def test_real_robot_rooms_need_physical_true_and_reject_the_old_marker(tmp_path, kind):
    assert config_readiness(_room_files(tmp_path, [kind]))["performers"] == [f"robot-{kind}"]
    for physical in (False, None):
        with pytest.raises(ValueError, match="physical: true"):
            config_readiness(_room_files(tmp_path, [kind], physical=physical))
    with pytest.raises(ValueError, match="physical_authority is no longer used"):
        config_readiness(_room_files(tmp_path, [kind], provider_extra={
            kind: {"physical_authority": "I_AUTHORIZE_REACHY_CONTROL"}}))


def test_old_motion_authority_marker_is_rejected(tmp_path):
    path = _room_files(tmp_path, ["soarm101"])
    data = json.loads(path.read_text())
    data["motion_authority"] = "I_AUTHORIZE_ENSEMBLE_ROBOT_MOTION"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="motion_authority"):
        config_readiness(path)


def test_sdk_physical_daemon_needs_physical_true(tmp_path):
    revision = "sha256:" + "a" * 64
    extra = {"soarm101": {
        "mode": "sdk_physical_daemon", "trusted_host_token_env": "HOST",
        "trusted_choreography_catalog": str(tmp_path / "catalog.json"),
        "physical_daemon_config": str(tmp_path / "daemon.json"),
        "startup_target_revision": revision,
        "choreography_revisions": {"idle": revision, "speaking": revision},
        "choreography_durations_s": {"idle": 1, "speaking": 1}}}
    with pytest.raises(ValueError, match="needs physical: true"):
        config_readiness(_room_files(tmp_path, ["soarm101"], physical=False, provider_extra=extra))
    assert config_readiness(_room_files(tmp_path, ["soarm101"], provider_extra=extra))["performers"]


def test_check_config_prints_a_readiness_summary_without_serving(tmp_path, monkeypatch, capsys):
    from forge_puppeteer.ensemble.launcher import main

    path = _room_files(tmp_path, ["reachy", "stackchan"])
    monkeypatch.setattr("forge_puppeteer.ensemble.launcher.serve",
                        lambda *_: pytest.fail("check mode must not start the ensemble"))
    assert main(["--config", str(path), "--check-config"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary == {
        "configuration": "valid", "schema_version": 4,
        "performers": ["robot-reachy", "robot-stackchan"], "robot_kinds": ["reachy", "stackchan"],
        "robot_files_resolved": True, "device_connections_attempted": False,
        "motion_authority_checked": False,
    }


@pytest.mark.parametrize("selected", [
    ("reachy",), ("stackchan",), ("soarm101",), ("reachy", "stackchan"),
    ("reachy", "soarm101"), ("stackchan", "soarm101"), ("reachy", "stackchan", "soarm101"),
])
def test_robot_file_identity_must_match_its_reference(tmp_path, selected):
    path = _room_files(tmp_path, list(selected))
    assert config_readiness(path)["robot_kinds"] == list(selected)
    robot_file = tmp_path / f"{selected[0]}.json"
    data = json.loads(robot_file.read_text())
    data["id"] = "wrong"
    robot_file.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="identity mismatch"):
        config_readiness(path)


@pytest.mark.parametrize("version", [1, 2, 3])
def test_older_schema_versions_point_to_the_migration_script(tmp_path, version):
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"schema_version": version}))
    with pytest.raises(ValueError, match="migrate-room-config"):
        config_readiness(path)


def _live_presence_config(tmp_path, **overrides):
    pin = "sha256:" + "a" * 64
    base = {
        "instance_id": "arm", "mode": "sdk_simulation_daemon",
        "simulation_state_dir": str(tmp_path / "state"), "simulation_port": 8765,
        "broker_url": "http://127.0.0.1:8765", "token_env": "TOKEN",
        "trusted_host_token_env": "HOST", "allowed_tools": ["robot_capabilities", "robot_state", "stop"],
        "trusted_choreography_catalog": str(tmp_path / "catalog.json"),
        "choreography_revisions": {"yes": pin}, "choreography_durations_s": {"yes": 2.0},
        "live_presence": {"envelope_revision": "sha256:" + "b" * 64,
                          "breath_delta_deg": [0, 3, -2, 0, 0],
                          "max_offset_deg": [0, 6, 6, 6, 6], "speech_driven": True},
    }
    return {**base, **overrides}


def test_live_presence_replaces_the_looping_presence_programs(tmp_path):
    from forge_puppeteer.ensemble.build import _soarm_broker_pack
    from forge_puppeteer.ensemble.config import SOARMBrokerConfig

    config = SOARMBrokerConfig.model_validate(_live_presence_config(tmp_path))
    assert config.live_presence.settings().envelope == "presence"
    pack = _soarm_broker_pack(config)
    assert {s: pack.behaviors[s].mode for s in ("idle", "listening", "speaking")} == {
        "idle": "hold", "listening": "hold", "speaking": "hold"}
    assert pack.behaviors["yes"].mode == "once"


@pytest.mark.parametrize("change,message", [
    ({"live_presence": {"envelope_revision": "sha256:" + "b" * 64,
                        "breath_delta_deg": [0, 7, 0, 0, 0], "max_offset_deg": [0, 6, 6, 6, 6]}},
     "exceeds the pinned envelope"),
    ({"live_presence": {"envelope_revision": "sha256:" + "b" * 64,
                        "breath_delta_deg": [0, 3, 0, 0, 0], "max_offset_deg": [0, 6, 6, 20, 6]}},
     "within 0..15"),
    ({"trusted_host_token_env": None}, "separate token env"),
])
def test_live_presence_must_fit_its_pinned_envelope_and_be_trusted(tmp_path, change, message):
    from forge_puppeteer.ensemble.config import SOARMBrokerConfig

    with pytest.raises(ValueError, match=message):
        SOARMBrokerConfig.model_validate(_live_presence_config(tmp_path, **change))


def test_finish_on_interrupt_defaults_to_the_full_grace_and_is_bounded(tmp_path):
    from forge_puppeteer.ensemble.config import SOARMBrokerConfig

    config = SOARMBrokerConfig.model_validate(_live_presence_config(tmp_path))
    assert config.finish_on_interrupt_s == 5.0
    assert SOARMBrokerConfig.model_validate(
        _live_presence_config(tmp_path, finish_on_interrupt_s=0)).finish_on_interrupt_s == 0
    for bad in (-1, 5.5):
        with pytest.raises(ValueError):
            SOARMBrokerConfig.model_validate(_live_presence_config(tmp_path, finish_on_interrupt_s=bad))
