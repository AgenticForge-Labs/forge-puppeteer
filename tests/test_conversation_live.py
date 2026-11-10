"""SO-ARM live presence in the ConversationEnsemble: one stream, gestures close it."""

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
from forge_puppeteer.soarm_live import LivePresence, LivePresenceSettings

REV = "sha256:" + "b" * 64
ZERO = (0.0,) * 5
SETTINGS = LivePresenceSettings(
    "presence", REV, (0.0, 0.05, 0.05, 0.0, 0.0), (0.1, 0.1, 0.1, 0.1, 0.1))


class FakeArm:
    """Records what the SDK would see: stream open/close and finite programs."""

    defer_normal_transition = True
    finish_before_interrupt_s = 3.5

    def __init__(self, presence):
        self.presence = presence
        self.events = []
        self.behaviors = []
        self.open = False
        self.stop_calls = 0

    async def validate(self, request):
        if request.operation not in {"live_presence", "play_choreography", "wake_to_neutral", "play_joint_target"}:
            raise ValueError("unsupported")

    def resources_for(self, request):
        return ("soarm.motion",)

    async def execute(self, request):
        if request.operation == "live_presence":
            self.presence.reset()
            self.events.append("open")
            self.open = True
            try:
                while not self.presence.closing:
                    if not self.behaviors or self.behaviors[-1] != self.presence.behavior:
                        self.behaviors.append(self.presence.behavior)
                    await anyio.sleep(0.005)
                self.events.append("close")
            finally:
                self.open = False
            return Completion("live stream closed")
        assert not self.open, "a finite program ran while the stream was open"
        self.events.append(request.operation if request.operation != "play_choreography"
                           else request.asset_id.rsplit("/", 1)[-1])
        await anyio.sleep(0.02)
        return Completion("program done")

    async def stop(self):
        self.stop_calls += 1
        return StopResult(True, "stopped")


def hold():
    return BehaviorPattern(source="procedural", mode="hold", cues=())


def program(name, operation="play_choreography"):
    return BehaviorPattern(source="recorded", mode="once", cues=(BehaviorCue(
        at_s=0, operation=operation, asset_id=f"soarm101/choreography/{name}", asset_revision=REV),),
        max_duration_s=5)


def pose(name, operation):
    return BehaviorPattern(source="recorded", mode="once", cues=(BehaviorCue(
        at_s=0, operation=operation, asset_id=f"soarm101/target/{name}", asset_revision=REV),),
        max_duration_s=5)


def build(startup=False):
    presence = LivePresence(SETTINGS)
    arm = FakeArm(presence)
    runtime = ExecutionRuntime(PerformanceStage())
    runtime.register("arm", arm)
    behaviors = {"idle": hold(), "listening": hold(), "speaking": hold(), "yes": program("yes"),
                 "sleep2": pose("sleep2", "play_joint_target"),
                 "neutral": pose("wake_to_neutral", "wake_to_neutral")}
    wake = BehaviorPattern(source="recorded", mode="once", cues=(BehaviorCue(
        at_s=0, operation="wake_to_neutral", asset_id="soarm101/target/wake_to_neutral",
        asset_revision=REV),), max_duration_s=5) if startup else None
    ensemble = ConversationEnsemble(
        runtime, [PerformerBehaviors.author("arm", behaviors, startup=wake)], presence_ttl_s=5)
    ensemble.bind_live_presence("arm", presence)
    return arm, runtime, ensemble


def event(sequence, state, *, interrupted=False):
    return EnsemblePresence(
        session_id="room", event_id=f"e{sequence}", sequence=sequence, state=state,
        source="agent_state", interrupted=interrupted)


async def until(predicate, timeout=2.0):
    with anyio.fail_after(timeout):
        while not predicate():
            await anyio.sleep(0.005)


@pytest.mark.anyio
async def test_presence_changes_move_one_open_stream_instead_of_switching_programs():
    arm, runtime, ensemble = build()
    async with runtime.run(), ensemble.run():
        await ensemble.observe(event(0, "listening"))
        for sequence, state in enumerate(["speaking", "listening", "idle", "speaking"], start=1):
            await until(lambda: arm.open)
            await ensemble.observe(event(sequence, state))
            await until(lambda s=state: arm.behaviors[-1] == s)
        assert arm.events == ["open"]
        assert arm.behaviors == ["listening", "speaking", "listening", "idle", "speaking"]
        assert arm.stop_calls == 0


@pytest.mark.anyio
async def test_an_interruption_changes_the_stream_state_without_a_stop():
    arm, runtime, ensemble = build()
    async with runtime.run(), ensemble.run():
        await ensemble.observe(event(0, "speaking"))
        await until(lambda: arm.open)
        await ensemble.observe(event(1, "listening", interrupted=True))
        await until(lambda: arm.behaviors[-1] == "listening")
        assert arm.open and arm.stop_calls == 0


@pytest.mark.anyio
async def test_a_gesture_closes_the_stream_runs_the_program_and_reopens():
    arm, runtime, ensemble = build()
    async with runtime.run(), ensemble.run():
        await ensemble.observe(event(0, "speaking"))
        await until(lambda: arm.open)
        result = await ensemble.express("yes", action_id="yes-1")
        assert result["outcome"] == "accepted"
        await until(lambda: arm.events == ["open", "close", "yes", "open"])
        assert arm.behaviors[-1] == "speaking", "presence resumes for the current state"
        assert arm.stop_calls == 0


@pytest.mark.anyio
async def test_settling_a_session_closes_the_stream_without_a_stop():
    arm, runtime, ensemble = build()
    async with runtime.run(), ensemble.run():
        await ensemble.observe(event(0, "listening"))
        await until(lambda: arm.open)
        assert (await ensemble.settle(timeout_s=3))["outcome"] == "settled"
        assert arm.events == ["open", "close"] and not arm.open
        assert arm.stop_calls == 0
        await anyio.sleep(0.05)
        assert arm.events == ["open", "close"], "nothing reopens after settling"


@pytest.mark.anyio
async def test_a_disconnect_is_an_urgent_stop():
    arm, runtime, ensemble = build()
    async with runtime.run(), ensemble.run():
        await ensemble.observe(event(0, "listening"))
        await until(lambda: arm.open)
        await ensemble.observe(event(1, "disconnected"))
        assert arm.stop_calls == 1 and not arm.open


@pytest.mark.anyio
async def test_the_stream_only_opens_after_the_wake_motion_even_for_a_spoken_greeting():
    arm, runtime, ensemble = build(startup=True)
    async with runtime.run(), ensemble.run():
        await ensemble.observe(event(0, "speaking"))
        await until(lambda: arm.open)
        assert arm.events[:2] == ["wake_to_neutral", "open"]
        assert arm.behaviors[-1] == "speaking"


@pytest.mark.anyio
async def test_a_sleep_gesture_leaves_the_arm_resting_until_it_is_woken():
    arm, runtime, ensemble = build()
    async with runtime.run(), ensemble.run():
        await ensemble.observe(event(0, "listening"))
        await until(lambda: arm.open)
        assert (await ensemble.express("sleep2", action_id="sleep-1"))["outcome"] == "accepted"
        await until(lambda: arm.events == ["open", "close", "play_joint_target"])
        # Presence changes while lying down start nothing: no stream, no refused opens.
        await ensemble.observe(event(1, "speaking"))
        await ensemble.observe(event(2, "idle"))
        await anyio.sleep(0.15)
        assert arm.events == ["open", "close", "play_joint_target"] and not arm.open
        # Other gestures wait for the wake move instead of being sent to a lying arm.
        denied = await ensemble.express("yes", action_id="yes-1")
        assert denied["outcome"] == "denied" and denied["reason"] == "performer_resting_needs_wake"
        # "neutral" runs the wake move and presence resumes for the current state.
        assert (await ensemble.express("neutral", action_id="wake-1"))["outcome"] == "accepted"
        await until(lambda: arm.events[-2:] == ["wake_to_neutral", "open"])
        assert arm.behaviors[-1] == "idle"
        assert (await ensemble.express("yes", action_id="yes-2"))["outcome"] == "accepted"
        assert arm.stop_calls == 0


@pytest.mark.anyio
async def test_resting_does_not_carry_into_the_next_conversation():
    arm, runtime, ensemble = build()
    async with runtime.run(), ensemble.run():
        await ensemble.observe(event(0, "listening"))
        await until(lambda: arm.open)
        await ensemble.express("sleep2", action_id="sleep-1")
        await until(lambda: "play_joint_target" in arm.events)
        await ensemble.observe(event(1, "disconnected"))
        arm.events.clear()
        await ensemble.observe(EnsemblePresence(
            session_id="next", event_id="n0", sequence=0, state="listening", source="agent_state"))
        await until(lambda: arm.open)
