"""Offline three-native-SDK ensemble tests: no actual USB, motors, or voice."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import anyio
import pytest

pytest.importorskip("stackchan_control.assets")
pytest.importorskip("soarm101_motion.choreography")

from soarm101_motion.agent_control import AgentAuthorityStore
from soarm101_motion.broker import AgentMotionRates
from soarm101_motion.broker_sdk import SDKAgentExecutor
from soarm101_motion.broker_watchdog import BrokerWatchdog
from soarm101_motion.choreography import (
    ChoreographyPolicy, JointChoreography, JointFrame, preflight_choreography,
)
from soarm101_motion.config import SOARM101Config
from soarm101_motion.constants import ARM_JOINTS

from forge_puppeteer.behavior_contract import EnsemblePresence, PerformerBehaviors
from forge_puppeteer.conversation_ensemble import ConversationEnsemble
from forge_puppeteer.conversation_native import (
    reachy_pack_from_catalog,
    stackchan_conversation_pack,
)
from forge_puppeteer.conversation_soarm import (
    author_soarm_reference_programs,
    soarm_conversation_pack,
)
from forge_puppeteer.devices.reachy import ReachyCatalog, ReachyPlaybackProvider
from forge_puppeteer.devices.soarm101_choreography import SOARMChoreographyProvider
from forge_puppeteer.devices.stackchan import StackChanProvider
from forge_puppeteer.execution import ActionRequest, ExecutionRuntime
from forge_puppeteer.runtime import PerformanceStage
from forge_puppeteer.soarm_pose_assets import author_complete_programs

NEUTRAL_HEAD = [[1., 0., 0., 0.], [0., 1., 0., 0.],
                [0., 0., 1., 0.], [0., 0., 0., 1.]]


class Move:
    def __init__(self, data, sound_path=None):
        self.description = data["description"]
        self.duration = data["duration"]
        self.sound_path = sound_path


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
    # The source SDK must approve each full authored path before the
    # ensemble attempts any hardware dispatch; error type remains visible.
    arm = sdk[0]._session
    for program in programs.values():
        try:
            preflight_choreography(arm, program, provider.policy)
        except Exception as exc:
            raise AssertionError(f"authored program {program.name} failed SDK preflight: {exc}") from exc
    assert pack.revision == pack.content_revision()
    assert "4g" not in pack.model_dump_json().lower()
    assert "yes" in pack.behaviors and "no" in pack.behaviors
    assert pack.behaviors["idle"].mode == "loop"
    assert pack.behaviors["idle"].period_s == 1.0  # neutral-only default, no 10 s interruption
    assert pack.behaviors["yes"].cues[0].asset_id == "soarm101/choreography/yes"
    with pytest.raises(PermissionError, match="pin"):
        soarm_conversation_pack(
            "soarm101", programs, approved_revisions=("sha256:" + "0"*64,),
        )


def test_soarm_pack_without_startup_preserves_independent_yes_endpoints(sdk):
    _provider, _pack, programs = arm_binding(sdk)
    yes = programs["yes"]
    endpoint = list(yes.frames[-1].joints_rad)
    endpoint[0] += 0.01
    frames = (*yes.frames[:-1], JointFrame(yes.frames[-1].at_s, tuple(endpoint)))
    programs["yes"] = JointChoreography.author(
        yes.name, yes.robot_id, yes.calibration_id, frames,
    )
    pack = soarm_conversation_pack(
        "soarm101", programs,
        approved_revisions=tuple(program.revision for program in programs.values()),
    )
    assert pack.startup is None
    assert "yes" in pack.behaviors


def test_soarm_authoring_rejects_unreviewed_extremes(sdk):
    executor, _ = sdk
    neutral = [executor._session.get_joint_positions().positions[n] for n in ARM_JOINTS]
    with pytest.raises(ValueError):
        author_soarm_reference_programs(
            robot_id="so101", calibration_id="simulation",
            neutral_joints_rad=neutral, listen_wrist_roll_rad=1.0,
        )


def test_soarm_pack_accepts_optional_pinned_breathe_behavior(sdk):
    _provider, base_pack, programs = arm_binding(sdk)
    neutral = tuple(sdk[0]._session.get_joint_positions().positions[n] for n in ARM_JOINTS)
    breathe = author_complete_programs(robot_id="so101", calibration_id="simulation",
        neutral=neutral, bottom=neutral, top=neutral, yes_up=neutral, yes_down=neutral)["breathe"]
    programs["breathe"] = breathe
    pack = soarm_conversation_pack("soarm101", programs,
        approved_revisions=tuple(p.revision for p in programs.values()))
    assert "breathe" in pack.behaviors
    assert pack.behaviors["breathe"].mode == "once"
    assert "breathe" not in base_pack.behaviors
    breathing = soarm_conversation_pack(
        "soarm101", programs,
        approved_revisions=tuple(p.revision for p in programs.values()),
        idle_behavior="breathe",
    )
    idle = breathing.behaviors["idle"]
    assert idle.mode == "loop" and idle.max_cycles == 300
    assert idle.cues[0].asset_id == "soarm101/choreography/breathe"
    assert idle.cues[0].asset_revision == breathe.revision


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
    arm_provider, arm_pack, _programs = arm_binding(sdk)
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
            arm_status = ensemble.snapshot()["performers"]["soarm101"]
            assert arm_status["status"] not in {"blocked", "unavailable"}, arm_status
            yes = await ensemble.express("yes", action_id="one-yes")
            assert yes["outcome"] == "accepted", yes
            await anyio.sleep(2.9)
            # A second named gesture uses independent native SDK motions for
            # all three; all are finite and return to neutral.
            no = await ensemble.express("no", action_id="one-no")
            assert no["outcome"] == "accepted", no
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
    provider, _, _programs = arm_binding(sdk)
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


@pytest.mark.anyio
async def test_actual_soarm_scheduler_repeats_bounded_breathe_only_while_fresh(sdk):
    executor, watchdog = sdk
    neutral = tuple(executor._session.get_joint_positions().positions[n] for n in ARM_JOINTS)
    breathe = author_complete_programs(
        robot_id="so101", calibration_id="simulation", neutral=neutral,
        bottom=neutral, top=neutral, yes_up=neutral, yes_down=neutral,
    )["breathe"]
    programs = author_soarm_reference_programs(
        robot_id="so101", calibration_id="simulation", neutral_joints_rad=neutral,
        speaking_wrist_roll_amplitude_rad=0.01, nod_wrist_flex_amplitude_rad=0.01,
        shake_wrist_roll_amplitude_rad=0.01,
    )
    programs["breathe"] = breathe
    policy = ChoreographyPolicy(
        "so101", "simulation", tuple(p.revision for p in programs.values()), max_duration_s=20,
    )
    provider = SOARMChoreographyProvider(executor, watchdog, policy, programs)
    pack = soarm_conversation_pack(
        "soarm101", programs, approved_revisions=policy.allowed_revisions,
        idle_behavior="breathe",
    )
    idle = pack.behaviors["idle"].model_copy(update={"period_s": 0.2, "max_duration_s": 20})
    pack = PerformerBehaviors.author("soarm101", {**pack.behaviors, "idle": idle})
    dispatched = []

    def sdk_execution(_request, program, _timing_scale):
        dispatched.append(program.revision)
        return {"revision": program.revision}

    async def scheduler_preflight(_request):
        return None

    provider._execute_sdk = sdk_execution
    provider.validate = scheduler_preflight
    runtime = ExecutionRuntime(PerformanceStage())
    runtime.register("soarm101", provider)
    ensemble = ConversationEnsemble(runtime, [pack], presence_ttl_s=1)
    async with runtime.run(), ensemble.run():
        before = len(executor._session.backend.command_history)
        assert (await ensemble.observe(event(0, "idle")))["outcome"] == "accepted"
        with anyio.fail_after(2):
            while len(dispatched) < 3:
                details = ensemble.snapshot()["performers"]["soarm101"]
                if details["status"] in {"blocked", "unavailable"}:
                    raise AssertionError(details)
                await anyio.sleep(0.01)
        assert dispatched[:3] == [breathe.revision] * 3
        # SDK-side command history stays empty: the actual provider and room
        # scheduler were exercised, but trusted SDK dispatch was stubbed.
        assert len(executor._session.backend.command_history) == before
        await anyio.sleep(1.15)
        expired_count = len(dispatched)
        await anyio.sleep(0.12)
        assert len(dispatched) == expired_count
        assert ensemble.snapshot()["stale"] is True


@pytest.mark.anyio
async def test_actual_soarm_scheduler_fault_quarantines_without_retry(sdk):
    executor, watchdog = sdk
    _provider, _pack, programs = arm_binding(sdk)
    policy = ChoreographyPolicy(
        "so101", "simulation", tuple(p.revision for p in programs.values()), max_duration_s=20,
    )
    provider = SOARMChoreographyProvider(executor, watchdog, policy, programs)
    pack = soarm_conversation_pack(
        "soarm101", programs, approved_revisions=policy.allowed_revisions,
    )
    idle = pack.behaviors["idle"].model_copy(update={"period_s": 0.2, "max_duration_s": 20})
    pack = PerformerBehaviors.author("soarm101", {**pack.behaviors, "idle": idle})
    dispatched = []

    def sdk_fault(_request, program, _timing_scale):
        dispatched.append(program.revision)
        raise OSError("simulated SDK error with unknown outcome")

    provider._execute_sdk = sdk_fault

    async def scheduler_preflight(_request):
        return None

    provider.validate = scheduler_preflight
    runtime = ExecutionRuntime(PerformanceStage())
    runtime.register("soarm101", provider)
    ensemble = ConversationEnsemble(runtime, [pack], presence_ttl_s=2)
    async with runtime.run(), ensemble.run():
        assert (await ensemble.observe(event(0, "idle")))["outcome"] == "accepted"
        with anyio.fail_after(2):
            while ensemble.snapshot()["performers"]["soarm101"]["status"] != "blocked":
                await anyio.sleep(0.01)
        await anyio.sleep(0.12)
        assert len(dispatched) == 1
        assert ensemble.snapshot()["performers"]["soarm101"]["status"] == "blocked"
