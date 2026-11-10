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
from forge_puppeteer.devices.soarm101 import SOARM101Provider


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
@pytest.mark.parametrize("confirmed", [True, False])
async def test_disconnect_releases_only_confirmed_stopped_owner_without_rearming(confirmed):
    class FailedNative(SlowNative):
        async def execute(self, request):
            self.commands.append(request.operation)
            raise RuntimeError("injected lost action receipt")
        async def stop(self):
            self.stops += 1
            return StopResult(confirmed, "confirmed HOLD" if confirmed else "HOLD unknown")
    provider = FailedNative()
    runtime = ExecutionRuntime(PerformanceStage())
    runtime.register("arm", provider)
    ensemble = ConversationEnsemble(runtime, [pack()])
    async with runtime.run(), ensemble.run():
        await ensemble.observe(event(0, "idle"))
        with anyio.fail_after(2):
            while "arm" not in ensemble._blocked:
                await anyio.sleep(.01)
        await ensemble.observe(event(1, "disconnected", interrupted=True))
        assert ("arm" in runtime._owners) is not confirmed
        count = len(provider.commands)
        await ensemble.observe(event(2, "idle"))
        await anyio.sleep(.05)
        assert len(provider.commands) == count
        assert "arm" in ensemble._blocked


def test_rebinding_without_fallback_clears_previous_opt_in():
    runtime = ExecutionRuntime(PerformanceStage())
    runtime.register("arm", SlowNative())
    ensemble = ConversationEnsemble(runtime, [pack()])
    fallback = BehaviorPattern(source="procedural", mode="loop", period_s=0.4,
                               cues=(BehaviorCue(at_s=0, operation="head_pose"),))
    identity = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    ensemble.bind_speech_channel("arm", object(), amplitude=0.08,
                                 neutral_head=identity, fallback=fallback)
    assert "arm" in ensemble._speech_fallbacks
    with pytest.raises(ValueError, match="4x4"):
        ensemble.bind_speech_channel("arm", object(), amplitude=0.08, neutral_head=[[1]])
    assert "arm" in ensemble._speech_fallbacks
    ensemble.bind_speech_channel("arm", object(), amplitude=0.08, neutral_head=identity)
    assert "arm" not in ensemble._speech_fallbacks


@pytest.mark.anyio
@pytest.mark.parametrize("unknown", [False, True])
async def test_opt_in_speech_fallback_continues_without_replaying_features(unknown):
    class Provider:
        def __init__(self):
            self.calls = []

        async def validate(self, request):
            assert request.operation == "head_pose"

        def resources_for(self, request):
            return ("head.pose",)

        async def execute(self, request):
            self.calls.append(request.cue_id)
            if unknown and request.cue_id.startswith("speaking_fallback"):
                raise RuntimeError("synthetic unconfirmed SDK outcome")
            return Completion("fake head motion completed")

        async def stop(self):
            return StopResult(True, "fake stop confirmed")

    class Channel:
        def __init__(self):
            self.first = True

        def latest(self):
            if self.first:
                self.first = False
                return {"pitch": 0.1, "yaw": 0.1, "roll": 0.0,
                        "active": True, "stream_id": "speech", "sequence": 0}
            return None

    provider = Provider()
    runtime = ExecutionRuntime(PerformanceStage())
    runtime.register("arm", provider)
    ensemble = ConversationEnsemble(runtime, [pack()])
    fallback = BehaviorPattern(source="procedural", mode="loop", period_s=0.4,
                               cues=(BehaviorCue(at_s=0, operation="head_pose"),
                                     BehaviorCue(at_s=0.2, operation="head_pose")))
    identity = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    ensemble.bind_speech_channel("arm", Channel(), amplitude=0.08, neutral_head=identity,
                                 interval_s=0.03, fallback=fallback)
    async with runtime.run(), ensemble.run():
        await ensemble.observe(event(0, "speaking"))
        await anyio.sleep(1.2)
        fallback_calls = [call for call in provider.calls if call.startswith("speaking_fallback")]
        assert len([call for call in provider.calls if call.startswith("speaking:")]) == 1
        assert len(fallback_calls) == 1 if unknown else len(fallback_calls) >= 2
        if unknown:
            assert ensemble.snapshot()["performers"]["arm"]["status"] == "blocked"
        else:
            await ensemble.observe(event(1, "disconnected", interrupted=True))
            count = len(provider.calls)
            await anyio.sleep(0.1)
            assert len(provider.calls) == count


@pytest.mark.anyio
async def test_fallback_never_restarts_while_interruption_waits_for_current_action():
    class Provider:
        finish_on_interrupt_s = 0.5

        def __init__(self):
            self.calls = []
            self.started = anyio.Event()
            self.release = anyio.Event()

        async def validate(self, _request):
            pass

        def resources_for(self, _request):
            return ("head.pose",)

        async def execute(self, request):
            self.calls.append(request.cue_id)
            self.started.set()
            await self.release.wait()
            return Completion("fake action completed")

        async def stop(self):
            return StopResult(True, "fake stop")

    class Channel:
        def latest(self):
            return None

    provider = Provider()
    runtime = ExecutionRuntime(PerformanceStage())
    runtime.register("arm", provider)
    ensemble = ConversationEnsemble(runtime, [pack()])
    fallback = BehaviorPattern(source="procedural", mode="loop", period_s=0.2,
                               cues=(BehaviorCue(at_s=0, operation="head_pose"),))
    identity = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    ensemble.bind_speech_channel("arm", Channel(), amplitude=0.08, neutral_head=identity,
                                 interval_s=0.01, fallback=fallback)
    async with runtime.run(), ensemble.run(), anyio.create_task_group() as tasks:
        await ensemble.observe(event(0, "speaking"))
        with anyio.fail_after(2):
            await provider.started.wait()
        tasks.start_soon(ensemble.observe, event(1, "disconnected", interrupted=True))
        await anyio.sleep(0.05)
        provider.release.set()
        await anyio.sleep(0.15)
        assert len(provider.calls) == 1


@pytest.mark.anyio
async def test_fresh_speech_features_resume_after_fallback():
    class Provider:
        def __init__(self):
            self.calls = []

        async def validate(self, _request):
            pass

        def resources_for(self, _request):
            return ("head.pose",)

        async def execute(self, request):
            self.calls.append(request.cue_id)
            return Completion("fake action completed")

        async def stop(self):
            return StopResult(True, "fake stop")

    class Channel:
        value = None

        def latest(self):
            return self.value

    provider, channel = Provider(), Channel()
    runtime = ExecutionRuntime(PerformanceStage())
    runtime.register("arm", provider)
    ensemble = ConversationEnsemble(runtime, [pack()])
    fallback = BehaviorPattern(source="procedural", mode="loop", period_s=0.2,
                               cues=(BehaviorCue(at_s=0, operation="head_pose"),))
    identity = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    ensemble.bind_speech_channel("arm", channel, amplitude=0.08, neutral_head=identity,
                                 interval_s=0.02, fallback=fallback)
    async with runtime.run(), ensemble.run():
        await ensemble.observe(event(0, "speaking"))
        with anyio.fail_after(2):
            while not provider.calls:
                await anyio.sleep(0.01)
        assert provider.calls[0].startswith("speaking_fallback")
        channel.value = {"pitch": 0.1, "yaw": 0.1, "roll": 0.0,
                         "active": True, "stream_id": "fresh", "sequence": 0}
        with anyio.fail_after(2):
            while not any(cue.startswith("speaking:") for cue in provider.calls):
                await anyio.sleep(0.01)
        reactive_count = sum(cue.startswith("speaking:") for cue in provider.calls)
        await anyio.sleep(0.1)
        assert sum(cue.startswith("speaking:") for cue in provider.calls) == reactive_count == 1
        await ensemble.observe(event(1, "disconnected", interrupted=True))


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

@pytest.mark.anyio
async def test_deferred_transition_coalesces_and_interrupts_promptly():
    class Deferred(SlowNative):
        defer_normal_transition = True

        def __init__(self):
            super().__init__()
            self.started = anyio.Event()
            self.finish = anyio.Event()

        async def execute(self, request):
            self.commands.append(request.operation)
            self.started.set()
            await self.finish.wait()
            return Completion("completed")

    provider = Deferred()
    runtime = ExecutionRuntime(PerformanceStage())
    runtime.register("arm", provider)
    ensemble = ConversationEnsemble(runtime, [pack()])
    async with runtime.run(), ensemble.run():
        await ensemble.observe(event(0, "idle"))
        await provider.started.wait()
        with anyio.fail_after(.2):
            await ensemble.observe(event(1, "speaking"))
            await ensemble.observe(event(2, "idle"))
            await ensemble.observe(event(3, "speaking"))
        assert provider.commands == ["idle"]
        provider.finish.set()
        with anyio.fail_after(1):
            while len(provider.commands) < 2:
                await anyio.sleep(.01)
        assert provider.commands[:2] == ["idle", "speaking"]
        # Start a fresh blocked segment and ensure interruption does not wait
        # for its completion or restart from the interrupted event.
        provider.finish = anyio.Event()
        with anyio.fail_after(1):
            while len(provider.commands) < 3:
                await anyio.sleep(.01)
        with anyio.fail_after(1):
            await ensemble.observe(event(4, "idle", interrupted=True))
        assert provider.stops == 1
        count = len(provider.commands)
        await anyio.sleep(.05)
        assert len(provider.commands) == count
