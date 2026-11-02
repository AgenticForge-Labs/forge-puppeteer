"""Finite SDK transition grace never changes hard interruption semantics."""

import anyio
import pytest

from forge_puppeteer.behavior_contract import (
    BehaviorCue, BehaviorPattern, EnsemblePresence, PerformerBehaviors,
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
        result = await ensemble.observe(event(1, "speaking", interrupted=True))
        assert result["outcome"] == "accepted"
        assert native.stops >= 1


@pytest.fixture
def anyio_backend():
    return "asyncio"
