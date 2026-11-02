"""Behavior coordination tests use fake, non-hardware drivers only."""

import anyio
import pytest

from forge_puppeteer.conversation import (
    BehaviorAck, BehaviorUnavailable, ConversationBehaviors,
)


class FakeDriver:
    def __init__(self, *, supported=True, stop_ok=True, delay=0):
        self.supported = supported
        self.stop_ok = stop_ok
        self.delay = delay
        self.calls = []

    async def start_talking(self):
        self.calls.append("talk")
        if self.delay:
            await anyio.sleep(self.delay)
        if not self.supported:
            raise BehaviorUnavailable()
        return BehaviorAck(True, "native speaking animation requested")

    async def stop_talking(self):
        self.calls.append("stop")
        return BehaviorAck(self.stop_ok, "native talking loop stopped")

    async def set_idle(self):
        self.calls.append("idle")
        return BehaviorAck(True, "native idle mode requested")


def test_three_performers_share_one_turn_and_return_to_idle():
    async def run():
        group = ConversationBehaviors()
        robots = {name: FakeDriver(delay=0.001) for name in (
            "stackchan", "reachy", "soarm101"
        )}
        for name, driver in robots.items():
            group.register(name, driver)
        started = await group.speech_started("utterance-1")
        assert started.event == "speech_started"
        assert {p.performer_id for p in started.performers} == set(robots)
        assert all(p.state == "talking" for p in started.performers)
        assert all(driver.calls == ["talk"] for driver in robots.values())
        duplicate = await group.speech_started("utterance-1")
        assert duplicate == started
        finished = await group.speech_finished("utterance-1")
        assert all(p.state == "idle" for p in finished.performers)
        assert all(driver.calls == ["talk", "stop", "idle"] for driver in robots.values())
    anyio.run(run)


def test_unsupported_device_does_not_hold_back_other_performers():
    async def run():
        group = ConversationBehaviors()
        reachy = FakeDriver()
        arm = FakeDriver(supported=False)
        group.register("reachy", reachy)
        group.register("arm", arm)
        report = await group.speech_started("a")
        assert {p.performer_id: p.state for p in report.performers} == {
            "reachy": "talking", "arm": "unavailable",
        }
        await group.speech_finished("a")
        assert reachy.calls == ["talk", "stop", "idle"]
        assert arm.calls == ["talk"]
    anyio.run(run)


def test_stale_finish_cannot_stop_newer_turn_and_interrupt_is_idempotent():
    async def run():
        group = ConversationBehaviors()
        robot = FakeDriver()
        group.register("reachy", robot)
        await group.speech_started("one")
        await group.speech_started("two")
        assert robot.calls == ["talk", "stop", "idle", "talk"]
        stale = await group.speech_finished("one")
        assert stale.stale
        assert robot.calls[-1] == "talk"
        report = await group.speech_interrupted("two")
        assert not report.stale
        assert report.performers[0].state == "idle"
        again = await group.speech_interrupted("two")
        assert again.stale
        assert robot.calls == ["talk", "stop", "idle", "talk", "stop", "idle"]
    anyio.run(run)


def test_unconfirmed_stop_is_not_retried_or_replaced_by_idle():
    async def run():
        group = ConversationBehaviors()
        robot = FakeDriver(stop_ok=False)
        group.register("arm", robot)
        await group.speech_started("one")
        report = await group.speech_interrupted("one")
        assert report.performers[0].state == "blocked"
        assert report.performers[0].evidence == "unconfirmed_adapter_outcome"
        await group.speech_started("two")
        assert robot.calls == ["talk", "stop"]
    anyio.run(run)


def test_error_blocks_only_one_robot_and_idle_does_not_override_speech():
    class Fails(FakeDriver):
        async def start_talking(self):
            self.calls.append("talk")
            raise OSError("internal driver secret")

    async def run():
        group = ConversationBehaviors()
        good, bad = FakeDriver(), Fails()
        group.register("good", good)
        group.register("bad", bad)
        report = await group.speech_started("turn")
        assert {p.performer_id: p.state for p in report.performers} == {
            "good": "talking", "bad": "blocked",
        }
        assert "secret" not in repr(report)
        assert (await group.idle("other")).stale
        assert good.calls == ["talk"]
        await group.speech_finished("turn")
        assert good.calls == ["talk", "stop", "idle"]
        assert bad.calls == ["talk"]
    anyio.run(run)


def test_registration_and_empty_event_ids_fail_closed():
    group = ConversationBehaviors()
    driver = FakeDriver()
    with pytest.raises(ValueError):
        group.register("", driver)
    group.register("robot", driver)
    with pytest.raises(ValueError):
        group.register("robot", FakeDriver())
    with pytest.raises(ValueError):
        group.register("other", driver)

    async def run():
        with pytest.raises(ValueError):
            await group.speech_started("")
        with pytest.raises(ValueError):
            await group.speech_finished("")
    anyio.run(run)
