"""Review-only config and trust-boundary tests for the three-SDK launcher."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import anyio
import pytest

pytest.importorskip("soarm101_motion.choreography")

from soarm101_motion.constants import ARM_JOINTS, HOME_JOINTS

from forge_puppeteer.conversation_soarm import author_soarm_reference_programs
from forge_puppeteer.mcp.ensemble_launcher import (
    EnsembleConfig,
    SOARMEnsembleConfig,
    soarm_native_binding,
    build_ensemble,
)


def config_data(tmp_path):
    neutral = [HOME_JOINTS[name] for name in ARM_JOINTS]
    programs = author_soarm_reference_programs(
        robot_id="so101", calibration_id="simulation", neutral_joints_rad=neutral
    )
    soarm = {
        "instance_id": "soarm101", "robot_id": "so101",
        "calibration_id": "simulation", "mode": "simulation",
        "programs": [
            {
                "name": name, "revision": program.revision,
                "frames": [
                    {"at_s": frame.at_s, "joints_rad": list(frame.joints_rad)}
                    for frame in program.frames
                ],
            } for name, program in programs.items()
        ],
        "approved_revisions": [p.revision for p in programs.values()],
    }
    return {
        "schema_version": 1,
        "motion_authority": "I_AUTHORIZE_ENSEMBLE_ROBOT_MOTION",
        "reachy_neutral_head": str(tmp_path / "reachy-neutral.json"),
        "reachy": {
            "backend": "reachy_sdk", "device": "reachy",
            "instance_id": "reachy", "allowed_tools": [
                "head_pose", "play_move", "device_stop",
            ],
            "physical_authority": "I_AUTHORIZE_REACHY_CONTROL",
            "asset_root": str(tmp_path / "reachy-recordings"),
            "manifest": str(tmp_path / "emotions.manifest.json"),
            "sdk_version": "1.10.0rc5",
            "host": "127.0.0.1", "connection_mode": "localhost_only", "daemon_port": 8000,
        },
        "stackchan": {
            "backend": "stackchan_sdk", "device": "stackchan",
            "instance_id": "stackchan", "allowed_tools": [
                "head_move", "face_update", "device_stop",
            ],
            "physical_authority": "I_AUTHORIZE_STACKCHAN_CONTROL",
            "port": "/dev/ttyACM0", "sdk_version": "0.3.0",
        },
        "soarm101": soarm,
        "action_journal": str(tmp_path / "actions.db"),
        "ownership_db": str(tmp_path / "owners.db"),
    }


def test_native_launcher_config_binds_three_distinct_approved_programs(tmp_path):
    data = config_data(tmp_path)
    cfg = EnsembleConfig.model_validate(data)
    programs, policy, pack = soarm_native_binding(cfg.soarm101)
    assert len(programs) == 7
    assert set(programs) == {"idle", "listening", "speaking", "yes", "no",
                             "acknowledge", "neutral"}
    assert policy.calibration_id == "simulation"
    assert pack.behaviors["yes"].cues[0].asset_revision == programs["yes"].revision
    assert "4g" not in pack.model_dump_json().lower()
    assert EnsembleConfig.model_validate_json(json.dumps(data)).reachy.host == "127.0.0.1"


@pytest.mark.parametrize("bad", [
    {"motion_authority": "I_AM_AN_AGENT"},
    {"ownership_db": "relative.db"},
    {"reachy_neutral_head": "./fake-neutral.json"},
])
def test_launcher_refuses_untrusted_room_configuration(tmp_path, bad):
    with pytest.raises(ValueError):
        EnsembleConfig.model_validate(config_data(tmp_path) | bad)


def test_launcher_rejects_soarm_unpinned_or_tampered_trajectory(tmp_path):
    data = config_data(tmp_path)
    data["soarm101"]["programs"][0]["frames"][0]["joints_rad"][0] += 0.1
    with pytest.raises(ValueError, match="content differs"):
        soarm_native_binding(SOARMEnsembleConfig.model_validate(data["soarm101"]))

    data = config_data(tmp_path)
    data["soarm101"]["approved_revisions"][0] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="pinned"):
        EnsembleConfig.model_validate(data)


def test_launcher_rejects_physical_soarm_without_human_lease(tmp_path):
    data = config_data(tmp_path)
    data["soarm101"].update({
        "mode": "attended_physical_trial",
        "calibration_id": "real-calibration",
        "port": "/dev/ttyACM1",
    })
    with pytest.raises(ValueError, match="physical SO-ARM"):
        EnsembleConfig.model_validate(data)


def test_launcher_rejects_duplicate_performer_and_missing_sdk_grants(tmp_path):
    data = config_data(tmp_path)
    data["stackchan"]["instance_id"] = "reachy"
    with pytest.raises(ValueError, match="unique performer"):
        EnsembleConfig.model_validate(data)
    data = config_data(tmp_path)
    data["reachy"]["allowed_tools"] = ["play_move", "device_stop"]
    with pytest.raises(ValueError, match="expression requires"):
        EnsembleConfig.model_validate(data)


def test_launcher_denies_two_physical_robots_claiming_same_serial_port(tmp_path):
    data = config_data(tmp_path)
    data["soarm101"].update({
        "mode": "attended_physical_trial",
        "calibration_id": "real-calibration",
        "port": "/dev/ttyACM0",
        "human_lease_path": str(tmp_path / "operator-lease.json"),
        "physical_authority": "I_AUTHORIZE_ATTENDED_PHYSICAL_CHOREOGRAPHY",
    })
    with pytest.raises(ValueError, match="share one serial port"):
        EnsembleConfig.model_validate(data)


@pytest.mark.anyio
async def test_native_ensemble_launcher_opens_one_supervised_sdk_session(
    tmp_path, monkeypatch,
):
    """Test actual launcher construction/SDK supervisor without USB or Reachy."""
    import forge_puppeteer.mcp.ensemble_launcher as launcher

    connections = []

    class NoHardwareProvider:
        def __init__(self, config):
            self.config = config

        @asynccontextmanager
        async def connected(self):
            connections.append((self.config.device, "open"))
            try:
                yield
            finally:
                connections.append((self.config.device, "closed"))

    monkeypatch.setattr(launcher, "ReachySDKProvider", NoHardwareProvider)
    monkeypatch.setattr(launcher, "StackChanSDKProvider", NoHardwareProvider)
    cfg = EnsembleConfig.model_validate(config_data(tmp_path))
    reachy, stackchan, arm, _pack, runtime, connected = build_ensemble(cfg)
    assert reachy is not stackchan
    try:
        async with connected():
            assert arm.executor.connection_count == 1
            assert arm.executor._session is not None
            assert arm.watchdog.status["state"] == "watching"
            await anyio.to_thread.run_sync(arm.watchdog.heartbeat)
            assert connections == [("reachy", "open"), ("stackchan", "open")]
        assert connections[-2:] == [("stackchan", "closed"), ("reachy", "closed")]
        assert arm.executor._session is None
    finally:
        runtime.close()


@pytest.fixture
def anyio_backend():
    return "asyncio"
