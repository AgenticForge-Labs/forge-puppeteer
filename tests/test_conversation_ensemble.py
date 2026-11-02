"""Offline three-performer conversation patterns and real ExecutionRuntime admission.

No hardware imports or network access; tests exercise the same action journal,
provider/resource admission and STOP pathway used by the MCP runtime.
"""

import anyio
import pytest

from forge_puppeteer.behavior_contract import (
    BehaviorCue, BehaviorPattern, EnsemblePresence, PerformerBehaviors,
)
from forge_puppeteer.conversation_ensemble import ConversationEnsemble
from forge_puppeteer.execution import Completion, ExecutionRuntime, StopResult
from forge_puppeteer.runtime import FakeClock, PerformanceStage


REV = "sha256:" + "a" * 64


class FakeSDK:
    def __init__(self, *, fail=False, stop_confirmed=True):
        self.calls = []
        self.stop_calls = 0
        self.fail = fail
        self.stop_confirmed = stop_confirmed

    async def validate(self, request):
        if request.operation not in {"fake_idle", "fake_listen", "fake_talk", "fake_yes", "fake_no"}:
            raise ValueError("unsupported SDK operation")

    def resources_for(self, request):
        return ("fake.motion",)

    async def execute(self, request):
        self.calls.append(request.operation)
        if self.fail:
            raise OSError("simulated device outcome unknown")
        await anyio.lowlevel.checkpoint()
        return Completion("verified fake SDK completion")

    async def stop(self):
        self.stop_calls += 1
        return StopResult(self.stop_confirmed, "fake SDK stop response")


def loop(operation):
    return BehaviorPattern(
        source="procedural", mode="loop", period_s=0.2,
        max_cycles=20, max_duration_s=3,
        cues=(BehaviorCue(at_s=0, operation=operation),),
    )


def once(operation):
    return BehaviorPattern(
        source="sequence", mode="once",
        cues=(BehaviorCue(at_s=0, operation=operation),),
    )


def pack(name):
    return PerformerBehaviors(
        performer_id=name, revision=REV,
        behaviors={
            "idle": loop("fake_idle"),
            "listening": loop("fake_listen"),
            "speaking": loop("fake_talk"),
            "yes": once("fake_yes"),
            "no": once("fake_no"),
        },
    )


def event(session, sequence, state, *, event_id=None, interrupted=False):
    return EnsemblePresence(
        session_id=session, event_id=event_id or f"event-{sequence}",
        sequence=sequence, state=state, source="agent_state",
        interrupted=interrupted,
    )


def test_behavior_contracts_are_persona_free_pinned_and_bounded():
    one = pack("reachy")
    assert one.schema == "forge.behavior.v1"
    assert "personality" not in one.model_dump()
    with pytest.raises(ValueError):
        BehaviorCue(at_s=0, operation="play_move", asset_id="reachy/yes1")
    with pytest.raises(ValueError):
        BehaviorCue(at_s=0, operation="play_move", asset_id="reachy/yes1", asset_revision="not-a-hash")
    with pytest.raises(ValueError):
        BehaviorPattern(source="recorded", mode="once", cues=(BehaviorCue(at_s=0, operation="play_move"),))
    with pytest.raises(ValueError):
        BehaviorPattern(source="procedural", mode="loop", period_s=0.2, cues=(BehaviorCue(at_s=0.2, operation="x"),))
    with pytest.raises(ValueError):
        BehaviorPattern(source="procedural", mode="loop", period_s=0.01, cues=(BehaviorCue(at_s=0, operation="x"),))
    with pytest.raises(ValueError):
        BehaviorPattern(source="sequence", mode="once", cues=(BehaviorCue(at_s=0.5, operation="a"), BehaviorCue(at_s=0.2, operation="b")))
    with pytest.raises(ValueError):
        PerformerBehaviors(
            performer_id="arm", revision=REV,
            behaviors={"yes": loop("fake_yes")},
        )


def test_single_conversation_animates_three_performers_and_yes_no_resumes():
    async def run():
        providers = {name: FakeSDK() for name in ("reachy", "stackchan", "soarm101")}
        runtime = ExecutionRuntime(PerformanceStage(FakeClock()))
        for name, provider in providers.items():
            runtime.register(name, provider)
        ensemble = ConversationEnsemble(runtime, [pack(name) for name in providers])
        async with runtime.run():
            async with ensemble.run():
                out = await ensemble.observe(event("room-a", 0, "idle"))
                assert out["outcome"] == "accepted"
                await anyio.sleep(0.025)
                out = await ensemble.observe(event("room-a", 1, "listening"))
                assert out["state"] == "listening"
                await anyio.sleep(0.025)
                out = await ensemble.observe(event("room-a", 2, "speaking"))
                assert out["state"] == "speaking"
                await anyio.sleep(0.025)
                out = await ensemble.express("yes", action_id="yes-1")
                assert out["outcome"] == "accepted"
                await anyio.sleep(0.045)
                out = await ensemble.express("no", action_id="no-1")
                assert out["outcome"] == "accepted"
                await anyio.sleep(0.045)
                end = await ensemble.observe(event("room-a", 3, "disconnected"))
                assert end["state"] == "disconnected"
                assert end["session_id"] is None
        for provider in providers.values():
            assert provider.calls[:3] == ["fake_idle", "fake_listen", "fake_talk"]
            assert "fake_yes" in provider.calls
            assert "fake_no" in provider.calls
            assert provider.calls.count("fake_talk") >= 2  # resume after finite gesture
    anyio.run(run)


def test_ordering_identity_and_heartbeats_never_repeat_native_movement():
    async def run():
        provider = FakeSDK()
        runtime = ExecutionRuntime(PerformanceStage(FakeClock()))
        runtime.register("reachy", provider)
        ensemble = ConversationEnsemble(runtime, [pack("reachy")])
        async with runtime.run():
            async with ensemble.run():
                await ensemble.observe(event("room", 0, "idle"))
                await anyio.sleep(0.03)
                initial = len(provider.calls)
                assert (await ensemble.observe(event("room", 0, "idle")))["outcome"] == "duplicate"
                assert (await ensemble.observe(event("room", 0, "speaking", event_id="conflict"))["outcome"] == "denied"
                assert (await ensemble.observe(event("other-room", 1, "speaking"))["outcome"] == "denied"
                assert (await ensemble.observe(event("room", 1, "idle")))["outcome"] == "accepted"
                assert (await ensemble.observe(event("room", 0, "speaking")))["outcome"] == "stale"
                await anyio.sleep(0.03)
                assert len(provider.calls) == initial
                await ensemble.observe(event("room", 2, "disconnected"))
                assert (await ensemble.observe(event("next-room", 0, "listening")))["outcome"] == "accepted"
    anyio.run(run)


def test_denied_resource_ownership_does_not_stop_another_controller():
    async def run():
        provider = FakeSDK()
        runtime = ExecutionRuntime(PerformanceStage(FakeClock()))
        runtime.register("soarm101", provider)
        runtime.acquire_control("soarm101", "manipulator", "job-a")
        ensemble = ConversationEnsemble(runtime, [pack("soarm101")])
        async with runtime.run():
            async with ensemble.run():
                await ensemble.observe(event("room", 0, "speaking"))
                await anyio.sleep(0.05)
                snapshot = ensemble.snapshot()
                assert snapshot["performers"]["soarm101"]["status"] == "unavailable"
                assert provider.stop_calls == 0
                assert provider.calls == []
        runtime.release_control("soarm101", "manipulator", "job-a")
    anyio.run(run)


def test_unknown_sdk_outcome_and_unconfirmed_stop_quarantines_only_one_performer():
    async def run():
        ok, bad = FakeSDK(), FakeSDK(fail=True, stop_confirmed=False)
        runtime = ExecutionRuntime(PerformanceStage(FakeClock()))
        runtime.register("reachy", ok)
        runtime.register("soarm101", bad)
        ensemble = ConversationEnsemble(runtime, [pack("reachy"), pack("soarm101")])
        async with runtime.run():
            async with ensemble.run():
                await ensemble.observe(event("room", 0, "speaking"))
                await anyio.sleep(0.05)
                assert ensemble.snapshot()["performers"]["soarm101"]["status"] == "blocked"
                calls_before = list(bad.calls)
                await ensemble.observe(event("room", 1, "listening"))
                await anyio.sleep(0.05)
                assert bad.calls == calls_before
                assert bad.stop_calls >= 1
                assert "fake_listen" in ok.calls
    anyio.run(run)


def test_expression_is_pinned_and_missing_mapping_is_not_substituted():
    async def run():
        providers = {name: FakeSDK() for name in ("reachy", "stackchan")}
        runtime = ExecutionRuntime(PerformanceStage(FakeClock()))
        for name, provider in providers.items():
            runtime.register(name, provider)
        missing = pack("stackchan").model_copy(update={
            "behaviors": {k: v for k, v in pack("stackchan").behaviors.items() if k != "no"},
        })
        ensemble = ConversationEnsemble(runtime, [pack("reachy"), missing])
        async with runtime.run():
            async with ensemble.run():
                await ensemble.observe(event("room", 0, "idle"))
                assert (await ensemble.express("no", action_id="no-1"))["outcome"] == "unsupported"
                assert (await ensemble.express("yes", action_id="yes-1"))["outcome"] == "accepted"
                assert (await ensemble.express("yes", action_id="yes-1"))["outcome"] == "duplicate"
                assert (await ensemble.express("yes", action_id="other", performer_ids=("invalid",)))["outcome"] == "denied"
                await anyio.sleep(0.03)
                assert "fake_no" not in providers["stackchan"].calls
    anyio.run(run)


def test_inactive_session_expires_and_stops_background_dispatch():
    async def run():
        provider = FakeSDK()
        runtime = ExecutionRuntime(PerformanceStage(FakeClock()))
        runtime.register("reachy", provider)
        ensemble = ConversationEnsemble(runtime, [pack("reachy")], presence_ttl_s=1)
        async with runtime.run():
            async with ensemble.run():
                await ensemble.observe(event("room", 0, "idle"))
                await anyio.sleep(1.2)
                assert ensemble.snapshot()["stale"] is True
                assert ensemble.snapshot()["state"] == "disconnected"
                count = len(provider.calls)
                await anyio.sleep(0.3)
                assert len(provider.calls) == count
    anyio.run(run)
