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
    ReachyOnlyEnsembleConfig,
    SOARMEnsembleConfig,
    _check_original_idle_reference,
    _original_idle_pack,
    _profile_overrides,
    build_ensemble,
    soarm_native_binding,
    stackchan_display_presets,
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
            "daemon": {"command": ["/bin/true"]},
            "expected_daemon_hardware_id": "test-hardware",
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


def test_reachy_only_configuration_needs_explicit_identity_and_authority(tmp_path):
    data = config_data(tmp_path)
    data.pop("stackchan")
    data.pop("soarm101")
    data["schema_version"] = 2
    assert ReachyOnlyEnsembleConfig.model_validate(data).reachy.instance_id == "reachy"
    for change in ({"motion_authority": "REVIEW_REQUIRED"},
                   {"reachy_neutral_head": "relative.json"},
                   {"stackchan": {}},
                   {"action_journal": data["ownership_db"]}):
        with pytest.raises(ValueError):
            ReachyOnlyEnsembleConfig.model_validate(data | change)


def test_original_idle_requires_exact_grant_and_reviewed_reference(tmp_path):
    data = config_data(tmp_path)
    data.pop("stackchan")
    data.pop("soarm101")
    data["schema_version"] = 2
    data["motion_profiles"] = {"reachy": {"idle": "original_v1"}}
    with pytest.raises(ValueError, match="original_idle grant"):
        ReachyOnlyEnsembleConfig.model_validate(data)
    data["reachy"]["allowed_tools"].append("original_idle")
    data["reachy_original_idle_authority"] = "I_AUTHORIZE_REACHY_ORIGINAL_IDLE"
    cfg = ReachyOnlyEnsembleConfig.model_validate(data)
    neutral = tmp_path / "reachy-neutral.json"
    neutral.write_text(json.dumps([[1, 0, 0, 0], [0, 1, 0, 0],
                                   [0, 0, 1, 0], [0, 0, 0, 1]]))
    _check_original_idle_reference(cfg)
    neutral.write_text(json.dumps([[1, 0, 0, 0.01], [0, 1, 0, 0],
                                   [0, 0, 1, 0], [0, 0, 0, 1]]))
    with pytest.raises(ValueError, match="identity neutral"):
        _check_original_idle_reference(cfg)


@pytest.mark.parametrize("schema", [1, 3])
def test_original_idle_is_reachy_only_in_multi_performer_configs(tmp_path, schema):
    data = config_data(tmp_path)
    data["schema_version"] = schema
    if schema == 3:
        data.pop("soarm101")
    data["reachy"]["allowed_tools"].append("original_idle")
    data["reachy_original_idle_authority"] = "I_AUTHORIZE_REACHY_ORIGINAL_IDLE"
    data["motion_profiles"] = {"reachy": {"idle": "original_v1"}}
    assert EnsembleConfig.model_validate(data).motion_profiles["reachy"].idle == "original_v1"
    data["motion_profiles"] = {"stackchan": {"idle": "original_v1"}}
    with pytest.raises(ValueError, match="only for Reachy"):
        EnsembleConfig.model_validate(data)


def test_original_idle_pack_keeps_one_continuous_action():
    from forge_puppeteer.conversation_native import reachy_conversation_pack

    pack = reachy_conversation_pack("reachy", neutral_head=[
        [1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1],
    ], recording_revision="sha256:" + "a" * 64, available_recordings=("yes1",))
    original = _original_idle_pack(pack)
    selection = {"reachy": {"idle": "original_v1", "speaking": "original_v1"}}
    from forge_puppeteer.mcp.ensemble_launcher import PerformerMotionSelection
    overrides = _profile_overrides(
        {"reachy": PerformerMotionSelection.model_validate(selection["reachy"])}, {}, {},
        {"reachy": original},
    )
    idle = overrides["reachy"]["idle"].behaviors["idle"]
    assert len(idle.cues) == 1
    assert idle.cues[0].operation == "original_idle"
    assert idle.cues[0].parameters["duration_s"] == 3600
    assert overrides["reachy"]["speaking"].behaviors["speaking"] == idle


def test_original_speech_requires_matching_idle_and_separate_grant(tmp_path):
    data = config_data(tmp_path)
    data.pop("stackchan")
    data.pop("soarm101")
    data["schema_version"] = 2
    data["reachy"]["allowed_tools"].append("original_idle")
    data["reachy_original_idle_authority"] = "I_AUTHORIZE_REACHY_ORIGINAL_IDLE"
    data["motion_profiles"] = {"reachy": {"idle": "original_v1", "speaking": "original_v1"}}
    with pytest.raises(ValueError, match="original_speech grant"):
        ReachyOnlyEnsembleConfig.model_validate(data)
    data["reachy"]["allowed_tools"].append("original_speech")
    data["reachy_original_speech_authority"] = "I_AUTHORIZE_REACHY_ORIGINAL_SPEECH"
    config = ReachyOnlyEnsembleConfig.model_validate(data)
    assert config.motion_profiles["reachy"].speaking == "original_v1"
    data["motion_profiles"]["reachy"]["idle"] = "legacy"
    with pytest.raises(ValueError, match="requires original_v1 Reachy idle"):
        ReachyOnlyEnsembleConfig.model_validate(data)



def test_reachy_expressive_requires_native_compound_grant_and_reviewed_neutral(tmp_path):
    data = config_data(tmp_path)
    data["reachy_expression_style"] = "expressive"
    with pytest.raises(ValueError, match="pose_and_antennas"):
        EnsembleConfig.model_validate(data)

    data["reachy"]["allowed_tools"].append("pose_and_antennas")
    with pytest.raises(ValueError, match="neutral antenna"):
        EnsembleConfig.model_validate(data)

    data["reachy_neutral_antennas_rad"] = [-0.17, 0.17]
    cfg = EnsembleConfig.model_validate(data)
    assert cfg.reachy_expression_style == "expressive"
    assert cfg.reachy_neutral_antennas_rad == (-0.17, 0.17)

    only = {k: v for k, v in data.items() if k not in {"stackchan", "soarm101"}}
    only["schema_version"] = 2
    assert ReachyOnlyEnsembleConfig.model_validate(only).reachy_expression_style == "expressive"
    only["reachy_neutral_antennas_rad"] = [1.1, 0.1]
    with pytest.raises(ValueError, match="antenna motion"):
        ReachyOnlyEnsembleConfig.model_validate(only)


def test_reachy_startup_behavior_requires_explicit_wake_up_grant(tmp_path):
    data = config_data(tmp_path)
    data["reachy_startup_behavior"] = "wake_up_if_sleeping"
    with pytest.raises(ValueError, match="explicit wake_up grant"):
        EnsembleConfig.model_validate(data)
    data["reachy"]["allowed_tools"].append("wake_up")
    assert EnsembleConfig.model_validate(data).reachy_startup_behavior == "wake_up_if_sleeping"

    only = {k: v for k, v in data.items() if k not in {"stackchan", "soarm101"}}
    only["schema_version"] = 2
    assert ReachyOnlyEnsembleConfig.model_validate(only).reachy_startup_behavior == "wake_up_if_sleeping"


def test_reachy_only_launcher_starts_without_other_device_providers(tmp_path, monkeypatch):
    import forge_puppeteer.mcp.ensemble_launcher as launcher
    from forge_puppeteer.behavior_contract import (
        BehaviorCue,
        BehaviorPattern,
        PerformerBehaviors,
    )

    data = config_data(tmp_path)
    data.pop("stackchan")
    data.pop("soarm101")
    data["schema_version"] = 2
    (tmp_path / "reachy-neutral.json").write_text(json.dumps([[1, 0, 0, 0]] * 4))
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(data))
    pack = PerformerBehaviors.author("reachy", {
        name: BehaviorPattern(source="procedural", mode="loop", period_s=1, cues=(
            BehaviorCue(at_s=0, operation="head_pose"),
        )) for name in ("idle", "listening", "speaking")
    })
    seen = []
    class FakeReachy:
        def __init__(self, config):
            self.catalog = object()
        async def connected(self):
            raise AssertionError("test must not connect hardware")
    resolved_kwargs = []
    async def fake_pack(*args, **kwargs):
        resolved_kwargs.append(kwargs)
        return pack
    class FakeServer:
        def run(self, *, transport):
            seen.append(transport)
    monkeypatch.setattr(launcher, "ReachySDKProvider", FakeReachy)
    monkeypatch.setattr(launcher, "StackChanSDKProvider", lambda *_: pytest.fail("unexpected StackChan"))
    monkeypatch.setattr(launcher, "reachy_pack_from_catalog", fake_pack)
    monkeypatch.setattr(launcher, "create_ensemble_server", lambda *a, **k: FakeServer())
    launcher.serve(config_path)
    assert seen == ["stdio"]
    assert resolved_kwargs[0]["expression_style"] == "classic"
    assert resolved_kwargs[0]["breath_amplitude_m"] == 0.0

    data["reachy_expression_style"] = "expressive"
    data["reachy_neutral_antennas_rad"] = [-0.17, 0.17]
    data["reachy"]["allowed_tools"].append("pose_and_antennas")
    config_path.write_text(json.dumps(data))
    launcher.serve(config_path)
    assert seen == ["stdio", "stdio"]
    assert resolved_kwargs[1]["expression_style"] == "expressive"
    assert resolved_kwargs[1]["neutral_antennas_rad"] == (-0.17, 0.17)
    assert resolved_kwargs[1]["wobble_amplitude_rad"] == 0.09


def test_launcher_selects_one_continuous_original_idle_and_speech_action(tmp_path, monkeypatch):
    import forge_puppeteer.mcp.ensemble_launcher as launcher
    from forge_puppeteer.behavior_contract import (
        BehaviorCue,
        BehaviorPattern,
        PerformerBehaviors,
    )

    data = config_data(tmp_path)
    data.pop("stackchan")
    data.pop("soarm101")
    data["schema_version"] = 2
    data["reachy"]["allowed_tools"] += ["original_idle", "original_speech"]
    data["reachy_original_idle_authority"] = "I_AUTHORIZE_REACHY_ORIGINAL_IDLE"
    data["reachy_original_speech_authority"] = "I_AUTHORIZE_REACHY_ORIGINAL_SPEECH"
    data["motion_profiles"] = {"reachy": {"idle": "original_v1", "speaking": "original_v1"}}
    (tmp_path / "reachy-neutral.json").write_text(json.dumps([
        [1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1],
    ]))
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(data))
    legacy = BehaviorPattern(source="procedural", mode="loop", period_s=1,
                             cues=(BehaviorCue(at_s=0, operation="head_pose"),))
    pack = PerformerBehaviors.author("reachy", {
        "idle": legacy, "speaking": legacy, "listening": legacy,
    })
    class FakeReachy:
        def __init__(self, config):
            self.catalog = object()

        async def connected(self):
            raise AssertionError("test must not connect hardware")

    async def fake_pack(*args, **kwargs):
        return pack

    seen = {}
    class FakeServer:
        def run(self, *, transport):
            assert transport == "stdio"

    def fake_server(runtime, ensemble, **kwargs):
        seen.update(kwargs)
        seen["ensemble"] = ensemble
        return FakeServer()

    monkeypatch.setattr(launcher, "ReachySDKProvider", FakeReachy)
    monkeypatch.setattr(launcher, "reachy_pack_from_catalog", fake_pack)
    monkeypatch.setattr(launcher, "create_ensemble_server", fake_server)
    launcher.serve(config_path)
    selected = seen["ensemble"].packs["reachy"].behaviors
    assert selected["idle"] == selected["speaking"]
    assert selected["idle"].cues[0].operation == "original_idle"
    assert seen["native_gain_performers"] == ("reachy",)
    assert seen["native_motion_performers"] == ("reachy",)


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


def test_stackchan_native_speaking_is_explicit_operator_config(tmp_path):
    data = config_data(tmp_path)
    assert EnsembleConfig.model_validate(data).stackchan_speaking_mode == "legacy_size"
    data["stackchan_speaking_mode"] = "native_weight"
    assert EnsembleConfig.model_validate(data).stackchan_speaking_mode == "native_weight"
    data["stackchan_speaking_mode"] = "anything"
    with pytest.raises(ValueError):
        EnsembleConfig.model_validate(data)


def test_optional_stackchan_display_presets_require_sdk_and_separate_grant(tmp_path):
    data = config_data(tmp_path)
    config = EnsembleConfig.model_validate(data)
    assert config.stackchan_display_presets_enabled is False
    assert stackchan_display_presets(config) == {}
    data["stackchan_display_presets_enabled"] = True
    with pytest.raises(ValueError, match="SDK 0.4.0"):
        EnsembleConfig.model_validate(data)
    data["stackchan"]["sdk_version"] = "0.4.0"
    with pytest.raises(ValueError, match="face_scale_update"):
        EnsembleConfig.model_validate(data)
    data["stackchan"]["allowed_tools"].append("face_scale_update")
    config = EnsembleConfig.model_validate(data)
    curated = stackchan_display_presets(config)["stackchan"]
    assert set(curated) == {"normal", "enlarged"}
    assert curated["normal"].operation == "face_scale"
    assert curated["normal"].parameters == {"percent": 100}
    assert curated["enlarged"].parameters == {"percent": 170}
    assert all(cue.asset_id is None for cue in curated.values())
    assert "face_scale" not in config.stackchan.allowed_tools  # only the named MCP grant



def test_operator_motion_profile_validation_covers_all_performers(tmp_path):
    data = config_data(tmp_path)
    cfg = EnsembleConfig.model_validate(data)
    assert cfg.motion_profiles == {}
    data["motion_profiles"] = {"reachy": {"speaking": "native_v1"}}
    with pytest.raises(ValueError, match="pose_and_antennas"):
        EnsembleConfig.model_validate(data)
    data["reachy"]["allowed_tools"].append("pose_and_antennas")
    data["reachy_neutral_antennas_rad"] = [-0.17, 0.17]
    approved = EnsembleConfig.model_validate(data)
    assert approved.motion_profiles["reachy"].speaking == "native_v1"
    assert approved.motion_profiles["reachy"].idle == "legacy"

    data["motion_profiles"]["reachy"]["speaking"] = "smooth_v2"
    with pytest.raises(ValueError, match="speech-driven"):
        EnsembleConfig.model_validate(data)
    data["speech_driven_performers"] = ["reachy"]
    assert EnsembleConfig.model_validate(data).motion_profiles["reachy"].speaking == "smooth_v2"
    data["speech_driven_performers"] = ["reachy", "reachy"]
    with pytest.raises(ValueError, match="unique"):
        EnsembleConfig.model_validate(data)

    data["speech_driven_performers"] = []
    for selected in (
        {"soarm101": {"speaking": "native_v1"}},
        {"nonexistent": {"idle": "native_v1"}},
        {"reachy": {"idle": "not_real"}},
        {"reachy": {"unexpected": "legacy"}},
    ):
        data["motion_profiles"] = selected
        with pytest.raises(ValueError):
            EnsembleConfig.model_validate(data)
    data["motion_profiles"] = {}
    data["speech_driven_performers"] = ["soarm101"]
    with pytest.raises(ValueError, match="native Reachy or StackChan"):
        EnsembleConfig.model_validate(data)


def test_reachy_only_motion_profiles_require_expressive_review(tmp_path):
    data = config_data(tmp_path)
    data.pop("stackchan")
    data.pop("soarm101")
    data["schema_version"] = 2
    data["motion_profiles"] = {"reachy": {"idle": "native_v1"}}
    with pytest.raises(ValueError, match="pose_and_antennas"):
        ReachyOnlyEnsembleConfig.model_validate(data)
    data["reachy"]["allowed_tools"].append("pose_and_antennas")
    data["reachy_neutral_antennas_rad"] = [-0.17, 0.17]
    assert ReachyOnlyEnsembleConfig.model_validate(data).motion_profiles["reachy"].idle == "native_v1"
    data["speech_driven_performers"] = ["stackchan"]
    with pytest.raises(ValueError, match="native Reachy or StackChan"):
        ReachyOnlyEnsembleConfig.model_validate(data)
