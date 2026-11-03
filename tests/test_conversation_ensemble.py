"""Offline three-performer conversation patterns and real ExecutionRuntime admission.

No hardware imports or network access; tests exercise the same action journal,
provider/resource admission and STOP pathway used by the MCP runtime.
"""

import anyio
import pytest

from forge_puppeteer.behavior_contract import (
    BehaviorCue,
    BehaviorPattern,
    EnsemblePresence,
    PerformerBehaviors,
)
from forge_puppeteer.conversation_ensemble import ConversationEnsemble
from forge_puppeteer.execution import (
    Completion,
    ExecutionError,
    ExecutionRuntime,
    StopResult,
)
from forge_puppeteer.runtime import FakeClock, PerformanceStage

REV = "sha256:" + "a" * 64


class FakeSDK:
    def __init__(self, *, fail=False, stop_confirmed=True):
        self.calls = []
        self.stop_calls = 0
        self.fail = fail
        self.stop_confirmed = stop_confirmed

    async def validate(self, request):
        if request.operation not in {"fake_idle", "fake_listen", "fake_talk", "fake_yes", "fake_no", "fake_wake_up"}:
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
    return PerformerBehaviors.author(
        performer_id=name,
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



def test_status_discovers_only_authored_performer_actions():
    reachy = PerformerBehaviors.author("reachy", {
        **pack("reachy").behaviors,
        "happy": once("fake_yes"),
        "neutral": once("fake_yes"),
    })
    stackchan = PerformerBehaviors.author("stackchan", {
        "idle": loop("fake_idle"),
        "listening": loop("fake_listen"),
        "yes": once("fake_yes"),
    })
    runtime = ExecutionRuntime(PerformanceStage(FakeClock()))
    ensemble = ConversationEnsemble(runtime, [reachy, stackchan])
    snapshot = ensemble.snapshot()
    catalog = snapshot["performers"]
    assert catalog["reachy"]["expressions"] == [
        "happy", "neutral", "no", "yes",
    ]
    assert catalog["stackchan"]["expressions"] == ["yes"]
    assert catalog["reachy"]["presence_behaviors"] == [
        "idle", "listening", "speaking",
    ]
    assert catalog["stackchan"]["presence_behaviors"] == ["idle", "listening"]
    assert catalog["reachy"]["behavior_revision"] == reachy.revision
    assert catalog["stackchan"]["behavior_revision"] == stackchan.revision
    assert snapshot["state"] == "disconnected"
    assert "speaking" not in catalog["reachy"]["expressions"]



def test_behavior_contracts_are_persona_free_pinned_and_bounded():
    one = pack("reachy")
    assert one.schema == "forge.behavior.v1"
    assert "personality" not in one.model_dump()
    assert one.revision == one.content_revision()
    with pytest.raises(ValueError, match="revision"):
        PerformerBehaviors(
            performer_id="reachy", revision=REV, behaviors=one.behaviors
        )
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
    assert event("room", 1, "listening", interrupted=True).interrupted
    with pytest.raises(ValueError):
        event("room", 2, "speaking", interrupted=True)


def test_normal_voice_transition_does_not_cancel_finite_gesture():
    async def run():
        started = anyio.Event()
        finish = anyio.Event()

        class SlowGesture(FakeSDK):
            async def execute(self, request):
                self.calls.append(request.operation)
                if request.operation == "fake_no":
                    started.set()
                    await finish.wait()
                return Completion("fake SDK completed")

        sdk = SlowGesture()
        runtime = ExecutionRuntime(PerformanceStage())
        runtime.register("reachy", sdk)
        ensemble = ConversationEnsemble(runtime, [pack("reachy")])
        async with runtime.run(), ensemble.run():
            await ensemble.observe(event("room", 0, "listening"))
            await anyio.sleep(0.02)
            assert (await ensemble.express("no", action_id="gesture-1"))["outcome"] == "accepted"
            await started.wait()
            result = await ensemble.observe(event("room", 1, "speaking"))
            assert result["performers"]["reachy"]["status"] != "blocked"
            assert sdk.stop_calls == 0
            finish.set()
            await anyio.sleep(0.05)
            assert sdk.calls.count("fake_no") == 1
            assert "fake_talk" in sdk.calls
            assert sdk.stop_calls == 0
    anyio.run(run)


def test_startup_behavior_runs_once_before_latest_presence_and_is_not_an_expression():
    async def run():
        startup_started = anyio.Event()
        finish_startup = anyio.Event()
        speaking_started = anyio.Event()

        class StartupSDK(FakeSDK):
            async def execute(self, request):
                self.calls.append(request.operation)
                if request.operation == "fake_wake_up":
                    startup_started.set()
                    await finish_startup.wait()
                elif request.operation == "fake_talk":
                    speaking_started.set()
                return Completion("fake SDK completed")

        sdk = StartupSDK()
        runtime = ExecutionRuntime(PerformanceStage())
        runtime.register("reachy", sdk)
        base = pack("reachy")
        authored = PerformerBehaviors.author(
            "reachy", base.behaviors, startup=once("fake_wake_up"),
        )
        ensemble = ConversationEnsemble(runtime, [authored])
        snapshot = ensemble.snapshot()["performers"]["reachy"]
        assert snapshot["startup_behavior"] is True
        assert snapshot["startup_pending"] is True
        assert "startup" not in snapshot["expressions"]

        async with runtime.run(), ensemble.run():
            await ensemble.observe(event("room", 0, "listening"))
            await startup_started.wait()
            result = await ensemble.observe(event("room", 1, "speaking"))
            assert result["performers"]["reachy"]["startup_pending"] is False
            assert sdk.stop_calls == 0
            finish_startup.set()
            await speaking_started.wait()
            assert ensemble.snapshot()["performers"]["reachy"]["status"] == "starting"
            assert sdk.calls.count("fake_wake_up") == 1
            assert "fake_talk" in sdk.calls
            assert "fake_listen" not in sdk.calls

    anyio.run(run)


def test_pre_dispatch_runtime_rejection_does_not_quarantine_or_send_stop():
    async def run():
        class InvalidResources(FakeSDK):
            def resources_for(self, _request):
                return ()

        sdk = InvalidResources()
        runtime = ExecutionRuntime(PerformanceStage())
        runtime.register("reachy", sdk)
        ensemble = ConversationEnsemble(runtime, [pack("reachy")])
        async with runtime.run(), ensemble.run():
            await ensemble.observe(event("room", 0, "listening"))
            await anyio.sleep(0.03)
            performer = ensemble.snapshot()["performers"]["reachy"]
            assert performer["status"] == "unavailable"
            assert performer["evidence"] == "invalid_resources"
            assert sdk.calls == []
            assert sdk.stop_calls == 0
            assert "reachy" not in ensemble._blocked

    anyio.run(run)


def test_explicit_interruption_still_stops_inflight_gesture():
    async def run():
        started = anyio.Event()
        class SlowGesture(FakeSDK):
            async def execute(self, request):
                self.calls.append(request.operation)
                if request.operation == "fake_no":
                    started.set()
                    await anyio.sleep_forever()
                return Completion("fake SDK completed")
        sdk = SlowGesture()
        runtime = ExecutionRuntime(PerformanceStage())
        runtime.register("reachy", sdk)
        ensemble = ConversationEnsemble(runtime, [pack("reachy")])
        async with runtime.run(), ensemble.run():
            await ensemble.observe(event("room", 0, "listening"))
            await anyio.sleep(0.02)
            await ensemble.express("no", action_id="gesture-1")
            await started.wait()
            await ensemble.observe(event("room", 1, "listening", interrupted=True))
            assert sdk.stop_calls == 1
    anyio.run(run)


def test_listening_hold_sends_no_motion_and_resumes_on_speaking():
    async def run():
        sdk = FakeSDK()
        runtime = ExecutionRuntime(PerformanceStage())
        runtime.register("reachy", sdk)
        base = pack("reachy").behaviors
        behaviors = {**base, "listening": BehaviorPattern(
            source="procedural", mode="hold", cues=(),
        )}
        ensemble = ConversationEnsemble(runtime, [PerformerBehaviors.author("reachy", behaviors)])
        async with runtime.run(), ensemble.run():
            await ensemble.observe(event("room", 0, "speaking"))
            await anyio.sleep(0.03)
            await ensemble.observe(event("room", 1, "listening"))
            await anyio.sleep(0.03)
            before = list(sdk.calls)
            assert ensemble.snapshot()["performers"]["reachy"]["evidence"] == "no_motion_command_dispatched"
            await anyio.sleep(0.25)
            assert sdk.calls == before
            await ensemble.observe(event("room", 2, "speaking"))
            await anyio.sleep(0.03)
            assert len(sdk.calls) > len(before)
            assert sdk.calls[-1] == "fake_talk"
    anyio.run(run)


def test_hold_is_only_allowed_for_cueless_listening():
    hold = BehaviorPattern(source="procedural", mode="hold", cues=())
    with pytest.raises(ValueError):
        PerformerBehaviors.author("reachy", {"speaking": hold})
    with pytest.raises(ValueError):
        BehaviorPattern(source="procedural", mode="hold", cues=(BehaviorCue(at_s=0, operation="x"),))
    with pytest.raises(ValueError):
        BehaviorPattern(source="recorded", mode="hold", cues=())
    with pytest.raises(ValueError):
        BehaviorPattern(source="procedural", mode="loop", cues=(), period_s=1)


def test_single_conversation_animates_three_performers_and_yes_no_resumes():
    async def run():
        providers = {name: FakeSDK() for name in ("reachy", "stackchan", "soarm101")}
        runtime = ExecutionRuntime(PerformanceStage(FakeClock()))
        for name, provider in providers.items():
            runtime.register(name, provider)
        ensemble = ConversationEnsemble(runtime, [pack(name) for name in providers])
        async with runtime.run(), ensemble.run():
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
        async with runtime.run(), ensemble.run():
            await ensemble.observe(event("room", 0, "idle"))
            await anyio.sleep(0.03)
            initial = len(provider.calls)
            assert (await ensemble.observe(event("room", 0, "idle")))["outcome"] == "duplicate"
            assert (await ensemble.observe(event("room", 0, "speaking", event_id="conflict")))["outcome"] == "denied"
            assert (await ensemble.observe(event("other-room", 1, "speaking")))["outcome"] == "denied"
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
        async with runtime.run(), ensemble.run():
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
        async with runtime.run(), ensemble.run():
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
        missing = PerformerBehaviors.author(
            "stackchan",
            {k: v for k, v in pack("stackchan").behaviors.items() if k != "no"},
        )
        ensemble = ConversationEnsemble(runtime, [pack("reachy"), missing])
        async with runtime.run(), ensemble.run():
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
        async with runtime.run(), ensemble.run():
            await ensemble.observe(event("room", 0, "idle"))
            await anyio.sleep(1.2)
            assert ensemble.snapshot()["stale"] is True
            assert ensemble.snapshot()["state"] == "disconnected"
            count = len(provider.calls)
            await anyio.sleep(0.3)
            assert len(provider.calls) == count
    anyio.run(run)


def test_owner_fence_is_acquired_and_released_at_room_boundaries():
    async def run():
        provider = FakeSDK()
        runtime = ExecutionRuntime(PerformanceStage(FakeClock()))
        runtime.register("soarm101", provider)
        ensemble = ConversationEnsemble(runtime, [pack("soarm101")])
        async with runtime.run(), ensemble.run():
            await ensemble.observe(event("room", 0, "speaking"))
            await anyio.sleep(0.03)
            with pytest.raises(ExecutionError, match="ownership_busy"):
                runtime.acquire_control("soarm101", "manipulator", "job-a")
            await ensemble.observe(event("room", 1, "disconnected"))
            runtime.acquire_control("soarm101", "manipulator", "job-a")
            runtime.release_control("soarm101", "manipulator", "job-a")
    anyio.run(run)


def test_mutating_a_frozen_pack_nested_payload_is_rejected_before_scheduling():
    original = pack("reachy")
    original.behaviors["speaking"].cues[0].parameters["speed"] = 100
    runtime = ExecutionRuntime(PerformanceStage(FakeClock()))
    with pytest.raises(ValueError, match="revision"):
        ConversationEnsemble(runtime, [original])



def test_presentation_preflight_denial_does_not_stop_or_preempt_ambient_cues():
    async def run():
        sdk = FakeSDK()
        runtime = ExecutionRuntime(PerformanceStage())
        runtime.register("stackchan", sdk)
        ensemble = ConversationEnsemble(runtime, [pack("stackchan")])
        cue = BehaviorCue(at_s=0, operation="face_scale", parameters={"percent": 170})
        async with runtime.run(), ensemble.run():
            await ensemble.observe(event("room", 0, "speaking"))
            await anyio.sleep(0.03)
            before = list(sdk.calls)
            result = await ensemble.present(
                "stackchan", "enlarged", action_id="scale-failed", cue=cue,
            )
            assert result["outcome"] == "unsupported"
            assert result["reason"] == "presentation_preflight_denied"
            assert sdk.stop_calls == 0
            assert "face_scale" not in sdk.calls
            assert sdk.calls[:len(before)] == before
            assert "stackchan" not in ensemble._blocked
    anyio.run(run)


def test_presentation_uses_existing_action_journal_and_resumes_presence():
    async def run():
        class VisualSDK(FakeSDK):
            async def validate(self, request):
                if request.operation == "face_scale":
                    if request.parameters != {"percent": 170}:
                        raise ValueError("unsupported visual preset")
                    return
                await super().validate(request)

        sdk = VisualSDK()
        runtime = ExecutionRuntime(PerformanceStage())
        runtime.register("stackchan", sdk)
        ensemble = ConversationEnsemble(runtime, [pack("stackchan")])
        cue = BehaviorCue(at_s=0, operation="face_scale", parameters={"percent": 170})
        async with runtime.run(), ensemble.run():
            await ensemble.observe(event("room", 0, "speaking"))
            await anyio.sleep(0.03)
            result = await ensemble.present(
                "stackchan", "enlarged", action_id="scale-one", cue=cue,
            )
            assert result["outcome"] == "accepted"
            with anyio.fail_after(2):
                while "face_scale" not in sdk.calls:
                    await anyio.sleep(0.01)
            record = await runtime.wait("scale-one:stackchan:0:0")
            assert record.state.value == "completed"
            assert sdk.calls.count("face_scale") == 1
            assert sdk.stop_calls == 0
            duplicate = await ensemble.present(
                "stackchan", "enlarged", action_id="scale-one", cue=cue,
            )
            assert duplicate["outcome"] == "duplicate"
            assert "face_scale" not in ensemble.snapshot()["performers"]["stackchan"]["expressions"]
            await anyio.sleep(0.05)
            assert sdk.calls.count("fake_talk") >= 2
    anyio.run(run)
