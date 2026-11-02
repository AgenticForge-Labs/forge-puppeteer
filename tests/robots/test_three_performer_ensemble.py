"""Offline three-native-SDK ensemble tests: no actual USB, motors, or voice."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import anyio
import pytest

pytest.importorskip("stackchan_control.assets")
pytest.importorskip("soarm101_motion.choreography")

from forge_puppeteer.behavior_contract import EnsemblePresence
from forge_puppeteer.conversation_ensemble import ConversationEnsemble
from forge_puppeteer.conversation_native import reachy_pack_from_catalog, stackchan_conversation_pack
from forge_puppeteer.conversation_soarm import author_soarm_reference_programs, soarm_conversation_pack
from forge_puppeteer.devices.reachy import ReachyCatalog, ReachyPlaybackProvider
from forge_puppeteer.devices.soarm101_choreography import SOARMChoreographyProvider
from forge_puppeteer.devices.stackchan import StackChanProvider
from forge_puppeteer.execution import ActionRequest, ExecutionRuntime
from forge_puppeteer.runtime import PerformanceStage
from soarm101_motion.agent_control import AgentAuthorityStore
from soarm101_motion.broker import AgentMotionRates
from soarm101_motion.broker_sdk import SDKAgentExecutor
from soarm101_motion.broker_watchdog import BrokerWatchdog
from soarm101_motion.choreography import ChoreographyPolicy
from soarm101_motion.config import SOARM101Config
from soarm101_motion.constants import ARM_JOINTS


NEUTRAL_HEAD = [[1., 0., 0., 0.], [0., 1., 0., 0.],
                [0., 0., 1., 0.], [0., 0., 0., 1.]]


class Move:
    def __init__(self, data, sound_path=None):
        self.description = data["description"]
        self.duration = data["duration"]


class ReachyFake:
    def __init__(self):
        self.ops = []

    def goto_target(self, **kwargs):
        self.ops.append(("head", kwargs))

    async def async_play_move(self, move, *, sound):
        self.ops.append((move.description, sound))

    def cancel_move(self):
        self.ops.append(("cancel", None))


class StackFake:
    def __init__(self):
        self.ops = []
        self.generation = 0

    def execution_state(self):
        return SimpleNamespace(generation=self.generation)

    def discover(self):
        return {"capabilities": ["system.state", "avatar.update", "motion.update.position_only"]}

    def state(self):
        return {"native_angle_limits_tenths": {
            "yaw": [-350, 350], "pitch": [300, 600],
        }}

    def update_face(self, payload, *, expected_generation=None):
        assert expected_generation == self.generation
        self.ops.append(("face", payload))
        return (SimpleNamespace(event="completed"),)

    def move_head(self, payload, *, expected_generation=None):
        assert expected_generation == self.generation
        self.ops.append(("head", payload))
        return (SimpleNamespace(event="completed"),)

    def stop(self):
        self.generation += 1
        self.ops.append(("stop", None))
        return (SimpleNamespace(event="completed"),)


class ClientFake:
    def close(self):
        pass


def reachy_catalog(tmp_path):
    root = tmp_path / "reachy-catalog"
    root.mkdir()
    files = {}
    for name in ("yes1", "no1"):
        blob = json.dumps({"description": name, "duration": 0.2}).encode()
        (root / (name + ".json")).write_bytes(blob)
        files[name + ".json"] = hashlib.sha256(blob).hexdigest()
    return ReachyCatalog(root, {
        "version": 1, "dataset_revision": "f" * 40, "sdk_version": "1.10.0rc5",
        "source_url": "https://huggingface.co/datasets/pollen-robotics/reachy-mini-emotions-library",
        "license": "Apache-2.0", "notices": "offline-only test",
        "files": files,
    }, move_factory=Move)


def event(n, state):
    return EnsemblePresence(
        session_id="one-persona-all-robots", event_id=f"event-{n}",
        sequence=n, state=state, source="agent_state",
    )


@pytest.fixture
def sdk(tmp_path):
    executor = SDKAgentExecutor(
        config=SOARM101Config(robot_id="so101"), rates=AgentMotionRates(),
        simulation=True, simulation_actions=True,
        authority_store=AgentAuthorityStore(tmp_path / "sdk-lease.json"),
    )
    executor.execute("state", {})
    watch = BrokerWatchdog(
        timeout_s=10, validate=executor.validate_supervision,
        trip=lambda _reason: executor.execute("stop", {}),
    )
    executor.motion_guard = watch.check
    watch.start()
    watch.heartbeat()
    try:
        yield executor, watch
    finally:
        watch.close()
        executor.close()


def arm_binding(sdk):
    executor, watch = sdk
    arm = executor._session
    neutral = [arm.get_joint_positions().positions[n] for n in ARM_JOINTS]
    programs = author_soarm_reference_programs(
        robot_id="so101", calibration_id="simulation",
        neutral_joints_rad=neutral,
        speaking_wrist_roll_amplitude_rad=0.01,
        nod_wrist_flex_amplitude_rad=0.01,
        shake_wrist_roll_amplitude_rad=0.01,
    )
    policy = ChoreographyPolicy(
        "so101", "simulation", tuple(p.revision for p in programs.values()),
        max_duration_s=20,
    )
    provider = SOARMChoreographyProvider(executor, watch, policy, programs)
    pack = soarm_conversation_pack(
        "soarm101", programs, approved_revisions=policy.allowed_revisions,
    )
    return provider, pack, programs


def test_soarm_pack_is_pinned_and_persona_free(sdk):
    provider, pack, programs = arm_binding(sdk)
    assert pack.revision == pack.content_revision()
    assert "4g" not in pack.model_dump_json().lower()
    assert "yes" in pack.behaviors and "no" in pack.behaviors
    assert pack.behaviors["idle"].mode == "loop"
    assert pack.behaviors["idle"].period_s == 10.5
    assert pack.behaviors["yes"].cues[0].asset_id == "soarm101/choreography/yes"
    with pytest.raises(PermissionError, match="pin"):
        soarm_conversation_pack(
            "soarm101", programs, approved_revisions=("sha256:" + "0"*64,),
        )


def test_soarm_authoring_rejects_unreviewed_extremes(sdk):
    executor, _ = sdk
    neutral = [executor._session.get_joint_positions().positions[n] for n in ARM_JOINTS]
    with pytest.raises(ValueError):
        author_soarm_reference_programs(
            robot_id="so101", calibration_id="simulation",
            neutral_joints_rad=neutral, listen_wrist_roll_rad=1.0,
        )


@pytest.mark.anyio
async def test_complete_three_robot_runtime_and_yes_no(sdk, tmp_path):
    reachy_native = ReachyFake()
    catalog = reachy_catalog(tmp_path)
    reachy_provider = ReachyPlaybackProvider(catalog, reachy_native, motion_authorized=True)
    stack_native = StackFake()
    stack_provider = StackChanProvider(
        "stackchan", connector=lambda *_a, **_k: (ClientFake(), stack_native),
        allowed_tools=frozenset({"device_info", "device_state", "face_update",
                                 "head_move", "device_stop", "action_lookup"}),
    )
    await stack_provider.connect("fake-port")
    arm_provider, arm_pack, programs = arm_binding(sdk)
    try:
        packs = (
            await reachy_pack_from_catalog("reachy", catalog, neutral_head=NEUTRAL_HEAD),
            stackchan_conversation_pack("stackchan"),
            arm_pack,
        )
        runtime = ExecutionRuntime(PerformanceStage(), execution_timeout=30)
        runtime.register("reachy", reachy_provider)
        runtime.register("stackchan", stack_provider)
        runtime.register("soarm101", arm_provider)
        ensemble = ConversationEnsemble(runtime, packs, presence_ttl_s=20)
        async with runtime.run(), ensemble.run():
            start = await ensemble.observe(event(0, "speaking"))
            assert start["outcome"] == "accepted"
            assert set(start["performers"]) == {"reachy", "stackchan", "soarm101"}
            await anyio.sleep(2.9)
            yes = await ensemble.express("yes", action_id="one-yes")
            assert yes["outcome"] == "accepted"
            await anyio.sleep(2.9)
            # A second named gesture uses independent native SDK motions for
            # all three; all are finite and return to neutral.
            no = await ensemble.express("no", action_id="one-no")
            assert no["outcome"] == "accepted"
            await anyio.sleep(2.9)
            end = await ensemble.observe(event(1, "disconnected"))
            assert end["outcome"] == "accepted"
            assert end["session_id"] is None
            assert end["performers"]["soarm101"]["status"] != "blocked"
        assert ("yes1", False) in reachy_native.ops
        assert ("no1", False) in reachy_native.ops
        assert any(op[0] == "face" for op in stack_native.ops)
        assert any(op[0] == "head" for op in stack_native.ops)
        assert len(sdk[0]._session.backend.command_history) > 0
        assert sdk[0].connection_count == 1
    finally:
        await stack_provider.close()


@pytest.mark.anyio
async def test_sdk_provider_rejects_unpinned_asset_without_motion(sdk):
    provider, _, programs = arm_binding(sdk)
    bad = ActionRequest(
        id="bad", performer_id="soarm101", caller_id="conversation",
        session_id="session", operation="play_choreography",
        parameters={}, asset_id="soarm101/choreography/yes",
        asset_revision="sha256:" + "f" * 64,
    )
    before = len(sdk[0]._session.backend.command_history)
    with pytest.raises(ValueError, match="stale"):
        await provider.validate(bad)
    assert len(sdk[0]._session.backend.command_history) == before
