"""Fake SDK coverage for a separately authorized continuous idle action."""

import asyncio
import threading
import time

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("reachy_mini")

from forge_puppeteer.behavior_contract import (
    BehaviorCue,
    BehaviorPattern,
    EnsemblePresence,
    PerformerBehaviors,
)
from forge_puppeteer.conversation_ensemble import ConversationEnsemble
from forge_puppeteer.devices.reachy import ReachyPlaybackProvider
from forge_puppeteer.execution import (
    ActionRequest,
    Completion,
    ExecutionRuntime,
    StopResult,
)
from forge_puppeteer.reachy_native_speech import NativeSpeechGainWindow
from forge_puppeteer.runtime import PerformanceStage


class Catalog:
    def __init__(self):
        self.manifest = {"sdk_version": "1.10.0rc5"}


class FakeRobot:
    def __init__(self):
        self.commands = []

    def get_current_head_pose(self):
        return np.eye(4)

    def get_present_antenna_joint_positions(self):
        return [0.0, 0.0]

    def set_target(self, **target):
        self.commands.append(target)


def request(duration=1.0):
    return ActionRequest(id="idle", performer_id="reachy", caller_id="operator",
                         session_id="room", operation="original_idle",
                         parameters={"duration_s": duration})


@pytest.mark.anyio
async def test_separate_grant_and_fixed_duration_are_required():
    robot = FakeRobot()
    provider = ReachyPlaybackProvider(Catalog(), robot, motion_authorized=True)
    with pytest.raises(PermissionError, match="separate operator grant"):
        await provider.execute(request())
    assert not robot.commands
    provider.original_idle_authorized = True
    for duration in (0, 3601, True, float("inf")):
        with pytest.raises(ValueError):
            await provider.validate(request(duration))
    assert not robot.commands


@pytest.mark.anyio
async def test_capability_requires_grant_and_sdk_methods():
    denied = ReachyPlaybackProvider(Catalog(), FakeRobot(), motion_authorized=True)
    assert "original_idle" not in (await denied.describe()).supported_operations
    assert not await denied.permitted("original_idle")
    unsupported = ReachyPlaybackProvider(Catalog(), object(), motion_authorized=True,
                                         original_idle_authorized=True)
    assert "original_idle" not in (await unsupported.describe()).supported_operations
    assert not await unsupported.permitted("original_idle")
    ready = ReachyPlaybackProvider(Catalog(), FakeRobot(), motion_authorized=True,
                                   original_idle_authorized=True)
    assert "original_idle" in (await ready.describe()).supported_operations
    assert await ready.permitted("original_idle")


def test_native_speech_source_requires_separate_authority():
    with pytest.raises(ValueError, match="separate explicit authorization"):
        ReachyPlaybackProvider(Catalog(), FakeRobot(), motion_authorized=True,
                               original_idle_authorized=True, native_speech_source=lambda: None)


@pytest.mark.anyio
async def test_out_of_bounds_measured_start_never_dispatches():
    class OutOfBounds(FakeRobot):
        def get_current_head_pose(self):
            head = np.eye(4)
            head[0, 3] = 0.2
            return head

    robot = OutOfBounds()
    provider = ReachyPlaybackProvider(Catalog(), robot, motion_authorized=True,
                                     original_idle_authorized=True)
    with pytest.raises(ValueError, match="adapter bounds"):
        await provider.execute(request())
    assert not robot.commands


@pytest.mark.anyio
async def test_inactivity_delay_is_bounded_and_interruptible_before_first_target():
    robot = FakeRobot()
    provider = ReachyPlaybackProvider(Catalog(), robot, motion_authorized=True,
                                     original_idle_authorized=True)
    task = asyncio.create_task(provider.execute(request()))
    await asyncio.sleep(0.1)
    assert not robot.commands
    assert (await provider.stop()).confirmed
    await task
    assert not robot.commands

    short = request(0.05).model_copy(update={"id": "short"})
    await provider.execute(short)
    assert not robot.commands


@pytest.mark.anyio
async def test_stop_waits_for_loop_and_fences_future_targets():
    robot = FakeRobot()
    provider = ReachyPlaybackProvider(Catalog(), robot, motion_authorized=True,
                                     original_idle_authorized=True)
    task = asyncio.create_task(provider.execute(request()))
    for _ in range(100):
        if len(robot.commands) >= 2:
            break
        await asyncio.sleep(0.005)
    assert len(robot.commands) >= 2
    result = await provider.stop()
    assert result.confirmed
    assert "physical settling unmeasured" in result.evidence
    await task
    count = len(robot.commands)
    await asyncio.sleep(0.05)
    assert len(robot.commands) == count
    assert all(set(command) == {"head", "antennas", "body_yaw"} for command in robot.commands)


@pytest.mark.anyio
async def test_runtime_stop_fences_continuous_idle():
    robot = FakeRobot()
    provider = ReachyPlaybackProvider(Catalog(), robot, motion_authorized=True,
                                     original_idle_authorized=True)
    runtime = ExecutionRuntime(PerformanceStage())
    runtime.register("reachy", provider)
    async with runtime.run():
        await runtime.submit(request())
        for _ in range(100):
            if robot.commands:
                break
            await asyncio.sleep(0.005)
        assert robot.commands
        result = await runtime.stop("reachy")
        assert result.confirmed
        record = await runtime.wait("idle")
        assert record.state.value == "cancelled"
        count = len(robot.commands)
        await asyncio.sleep(0.05)
        assert len(robot.commands) == count


@pytest.mark.anyio
async def test_unconfirmed_stop_fences_a_stuck_sdk_call():
    class StuckRobot(FakeRobot):
        def __init__(self):
            super().__init__()
            self.entered = threading.Event()
            self.release = threading.Event()

        def set_target(self, **target):
            self.commands.append(target)
            self.entered.set()
            self.release.wait(timeout=2)

    robot = StuckRobot()
    provider = ReachyPlaybackProvider(Catalog(), robot, motion_authorized=True,
                                     original_idle_authorized=True)
    runtime = ExecutionRuntime(PerformanceStage(), stop_timeout=0.05)
    runtime.register("reachy", provider)
    try:
        async with runtime.run():
            await runtime.submit(request())
            assert await asyncio.to_thread(robot.entered.wait, 1)
            stopped = await runtime.stop("reachy")
            assert not stopped.confirmed
            assert runtime.lookup("idle").state.value == "unknown"
            robot.release.set()
            await asyncio.sleep(0.1)
            assert len(robot.commands) == 1
    finally:
        robot.release.set()


@pytest.mark.anyio
async def test_room_disconnect_does_not_wait_for_finite_goto_grace():
    robot = FakeRobot()
    provider = ReachyPlaybackProvider(Catalog(), robot, motion_authorized=True,
                                     original_idle_authorized=True)
    runtime = ExecutionRuntime(PerformanceStage())
    runtime.register("reachy", provider)
    idle = BehaviorPattern(source="procedural", mode="loop", period_s=10,
                           cues=(BehaviorCue(at_s=0, operation="original_idle",
                                             parameters={"duration_s": 10}),))
    ensemble = ConversationEnsemble(runtime, [PerformerBehaviors.author("reachy", {"idle": idle})])
    async with runtime.run(), ensemble.run():
        def presence(seq, state):
            return EnsemblePresence(session_id="room", event_id=str(seq), sequence=seq,
                                    state=state, source="manual")

        await ensemble.observe(presence(0, "idle"))
        for _ in range(100):
            if robot.commands:
                break
            await asyncio.sleep(0.005)
        assert robot.commands
        started = time.monotonic()
        await ensemble.observe(presence(1, "disconnected"))
        assert time.monotonic() - started < 1.0
        count = len(robot.commands)
        await asyncio.sleep(0.05)
        assert len(robot.commands) == count


@pytest.mark.anyio
async def test_continuous_reachy_does_not_block_another_performer():
    class OtherPerformer:
        def __init__(self):
            self.started = asyncio.Event()

        async def validate(self, request):
            assert request.operation == "other_idle"

        def resources_for(self, request):
            return ("other.motion",)

        async def execute(self, request):
            self.started.set()
            return Completion("simulated action completed")

        async def stop(self):
            return StopResult(True, "simulated stop")

    robot, other = FakeRobot(), OtherPerformer()
    reachy = ReachyPlaybackProvider(Catalog(), robot, motion_authorized=True,
                                    original_idle_authorized=True)
    runtime = ExecutionRuntime(PerformanceStage())
    runtime.register("reachy", reachy)
    runtime.register("other", other)
    reachy_idle = BehaviorPattern(source="procedural", mode="loop", period_s=10,
                                  cues=(BehaviorCue(at_s=0, operation="original_idle",
                                                    parameters={"duration_s": 10}),))
    other_idle = BehaviorPattern(source="procedural", mode="loop", period_s=1,
                                 cues=(BehaviorCue(at_s=0, operation="other_idle"),))
    ensemble = ConversationEnsemble(runtime, [
        PerformerBehaviors.author("reachy", {"idle": reachy_idle}),
        PerformerBehaviors.author("other", {"idle": other_idle}),
    ])
    async with runtime.run(), ensemble.run():
        await ensemble.observe(EnsemblePresence(session_id="room", event_id="0", sequence=0,
                                                state="idle", source="manual"))
        await asyncio.wait_for(other.started.wait(), timeout=1.0)
        assert (await reachy.state())["active"]
        await ensemble.observe(EnsemblePresence(session_id="room", event_id="1", sequence=1,
                                                state="disconnected", source="manual"))


@pytest.mark.anyio
async def test_speech_offsets_compose_over_one_continuous_idle_action():
    robot = FakeRobot()
    window = NativeSpeechGainWindow()
    provider = ReachyPlaybackProvider(
        Catalog(), robot, motion_authorized=True, original_idle_authorized=True,
        native_speech_authorized=True, native_speech_source=window.latest,
    )
    runtime = ExecutionRuntime(PerformanceStage())
    runtime.register("reachy", provider)
    ambient = BehaviorPattern(source="procedural", mode="loop", period_s=10,
                              cues=(BehaviorCue(at_s=0, operation="original_idle",
                                                parameters={"duration_s": 10}),))
    pack = PerformerBehaviors.author("reachy", {"idle": ambient, "speaking": ambient})
    ensemble = ConversationEnsemble(runtime, [pack], continuous_presence_performers=("reachy",))
    async with runtime.run(), ensemble.run():
        def presence(seq, state, interrupted=False):
            return EnsemblePresence(session_id="room", event_id=str(seq), sequence=seq,
                                    state=state, source="manual", interrupted=interrupted)

        await ensemble.observe(presence(0, "idle"))
        for _ in range(100):
            if len(robot.commands) >= 2:
                break
            await asyncio.sleep(0.005)
        assert len(robot.commands) >= 2
        action = ensemble.snapshot()["performers"]["reachy"]["action_id"]
        await ensemble.observe(presence(1, "speaking"))
        assert ensemble.snapshot()["performers"]["reachy"]["action_id"] == action
        window.accept([{
            "schema": "forge.native_speech_gain.v1", "stream_id": "speech",
            "sequence": 0, "offset_ms": 0, "duration_ms": 50, "gain": 1.0,
            "timing_source": "generated_pcm", "acoustic_playout_measured": False,
        }])
        for _ in range(100):
            if not np.allclose(robot.commands[-1]["head"][:3, :3], np.eye(3)):
                break
            await asyncio.sleep(0.005)
        assert not np.allclose(robot.commands[-1]["head"][:3, :3], np.eye(3))
        assert ensemble.snapshot()["performers"]["reachy"]["action_id"] == action
        window.invalidate()
        await ensemble.observe(presence(2, "idle"))
        for _ in range(100):
            if np.allclose(robot.commands[-1]["head"][:3, :3], np.eye(3)):
                break
            await asyncio.sleep(0.005)
        assert np.allclose(robot.commands[-1]["head"][:3, :3], np.eye(3))
        assert ensemble.snapshot()["performers"]["reachy"]["action_id"] == action
        await ensemble.observe(presence(3, "disconnected", interrupted=True))
        count = len(robot.commands)
        await asyncio.sleep(0.05)
        assert len(robot.commands) == count


@pytest.mark.anyio
async def test_startup_resume_preserves_continuous_action_on_speaking_transition():
    class FakeStartupProvider(ReachyPlaybackProvider):
        def _wake_up_if_sleeping(self):
            return "fake startup returned"

    robot = FakeRobot()
    provider = FakeStartupProvider(Catalog(), robot, motion_authorized=True,
                                   original_idle_authorized=True)
    runtime = ExecutionRuntime(PerformanceStage())
    runtime.register("reachy", provider)
    ambient = BehaviorPattern(source="procedural", mode="loop", period_s=10,
                              cues=(BehaviorCue(at_s=0, operation="original_idle",
                                                parameters={"duration_s": 10}),))
    startup = BehaviorPattern(source="procedural", mode="once",
                              cues=(BehaviorCue(at_s=0, operation="wake_up"),))
    pack = PerformerBehaviors.author("reachy", {"idle": ambient, "speaking": ambient}, startup=startup)
    ensemble = ConversationEnsemble(runtime, [pack], continuous_presence_performers=("reachy",))
    async with runtime.run(), ensemble.run():
        await ensemble.observe(EnsemblePresence(session_id="room", event_id="0", sequence=0,
                                                state="idle", source="manual"))
        for _ in range(200):
            if robot.commands:
                break
            await asyncio.sleep(0.005)
        assert robot.commands
        action = ensemble.snapshot()["performers"]["reachy"]["action_id"]
        await ensemble.observe(EnsemblePresence(session_id="room", event_id="1", sequence=1,
                                                state="speaking", source="manual"))
        assert ensemble.snapshot()["performers"]["reachy"]["action_id"] == action
        await ensemble.observe(EnsemblePresence(session_id="room", event_id="2", sequence=2,
                                                state="disconnected", source="manual"))


@pytest.fixture
def anyio_backend():
    return "asyncio"
