"""Finite SDK transition grace never changes hard interruption semantics."""

import anyio
import pytest

from forge_puppeteer.behavior_contract import (
    BehaviorCue,
    BehaviorPattern,
    EnsemblePresence,
    PerformerBehaviors,
)
from forge_puppeteer.conversation_ensemble import ConversationEnsemble
from forge_puppeteer.execution import Completion, ExecutionRuntime, StopResult
from forge_puppeteer.runtime import PerformanceStage


class SlowNative:
    finish_before_interrupt_s = 0.25

    def __init__(self):
        self.commands = []
        self.stops = 0

    async def validate(self, request):
        assert request.operation in {"idle", "speaking", "yes"}

    def resources_for(self, request):
        return ("native.motion",)

    async def execute(self, request):
        self.commands.append(request.operation)
        await anyio.sleep(0.10)
        return Completion("synthetic SDK finished finite segment")

    async def stop(self):
        self.stops += 1
        return StopResult(True, "fake SDK stop confirmed")


def pack():
    return PerformerBehaviors.author("arm", {
        name: BehaviorPattern(
            source="procedural", mode="loop", period_s=0.4,
            max_duration_s=4, max_cycles=9,
            cues=(BehaviorCue(at_s=0, operation=name),),
        ) for name in ("idle", "speaking")
    } | {"yes": BehaviorPattern(
        source="sequence", mode="once",
        cues=(BehaviorCue(at_s=0, operation="yes"),),
    )})


def event(n, state, interrupted=False):
    return EnsemblePresence(
        session_id="room", event_id=f"e-{n}", sequence=n, state=state,
        source="agent_state", interrupted=interrupted,
    )


@pytest.mark.anyio
@pytest.mark.parametrize("settle", [0.0, 0.05])
async def test_startup_and_gesture_resume_use_speech_channel(settle):
    class Provider:
        def __init__(self):
            self.commands = []

        async def validate(self, request):
            pass

        def resources_for(self, request):
            return ("head.pose",)

        async def execute(self, request):
            self.commands.append(request.operation)
            return Completion("fake action returned")

        async def stop(self):
            return StopResult(True, "fake stop")

    class Channel:
        active = False
        sequence = 0

        def latest(self):
            if not self.active:
                return None
            self.sequence += 1
            return {"pitch": 0.1, "yaw": 0.1, "roll": 0.0,
                    "active": True, "stream_id": "fake", "sequence": self.sequence}

    provider = Provider()
    runtime = ExecutionRuntime(PerformanceStage())
    runtime.register("arm", provider)
    base = pack()
    startup = BehaviorPattern(source="procedural", mode="once",
                              cues=(BehaviorCue(at_s=0, operation="startup"),))
    behaviors = PerformerBehaviors.author("arm", base.behaviors, startup=startup)
    ensemble = ConversationEnsemble(runtime, [behaviors], startup_settle_s=settle)
    channel = Channel()
    identity = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    ensemble.bind_speech_channel("arm", channel, amplitude=0.08,
                                 neutral_head=identity, interval_s=0.02)
    async with runtime.run(), ensemble.run():
        await ensemble.observe(event(0, "speaking"))
        await anyio.sleep(0.16)
        assert provider.commands == ["startup"]
        channel.active = True
        with anyio.fail_after(1):
            while "head_pose" not in provider.commands:
                await anyio.sleep(0.01)
        assert (await ensemble.express("yes", action_id="yes-1"))["outcome"] == "accepted"
        with anyio.fail_after(1):
            while "yes" not in provider.commands:
                await anyio.sleep(0.01)
            while provider.commands[-1] != "head_pose":
                await anyio.sleep(0.01)
        assert "speaking" not in provider.commands
        await ensemble.observe(event(1, "disconnected"))


@pytest.mark.anyio
async def test_normal_presence_transition_finishes_finite_native_command_without_stop():
    native = SlowNative()
    runtime = ExecutionRuntime(PerformanceStage())
    runtime.register("arm", native)
    ensemble = ConversationEnsemble(runtime, [pack()])
    async with runtime.run(), ensemble.run():
        assert (await ensemble.observe(event(0, "idle")))["outcome"] == "accepted"
        await anyio.sleep(0.03)
        assert (await ensemble.observe(event(1, "speaking")))["outcome"] == "accepted"
        assert native.stops == 0
        await anyio.sleep(0.16)
        assert native.commands[:2] == ["idle", "speaking"]
        assert (await ensemble.express("yes", action_id="yes-0"))["outcome"] == "accepted"
        assert native.stops == 0


@pytest.mark.anyio
async def test_explicit_interruption_never_waits_for_provider_grace():
    native = SlowNative()
    runtime = ExecutionRuntime(PerformanceStage())
    runtime.register("arm", native)
    ensemble = ConversationEnsemble(runtime, [pack()])
    async with runtime.run(), ensemble.run():
        await ensemble.observe(event(0, "idle"))
        await anyio.sleep(0.03)
        result = await ensemble.observe(event(1, "idle", interrupted=True))
        assert result["outcome"] == "accepted"
        assert native.stops >= 1


@pytest.fixture
def anyio_backend():
    return "asyncio"
