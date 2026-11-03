"""Offline integration: real native StackChan SDK facade + Reachy SDK adapter.

No serial or robot connection is opened. Fake SDK execution objects still
exercise provider validation, runtime ownership, recorded-move pins and action
journaling through the PR1 ConversationEnsemble.
"""

import hashlib
import json
import time
from types import SimpleNamespace

import anyio
import pytest

from forge_puppeteer.behavior_contract import EnsemblePresence
from forge_puppeteer.conversation_ensemble import ConversationEnsemble
from forge_puppeteer.conversation_native import (
    reachy_pack_from_catalog,
    stackchan_conversation_pack,
)
from forge_puppeteer.devices.reachy import ReachyCatalog, ReachyPlaybackProvider
from forge_puppeteer.devices.stackchan import StackChanProvider
from forge_puppeteer.execution import ExecutionRuntime
from forge_puppeteer.runtime import PerformanceStage

pytest.importorskip("stackchan_control.assets")
pytestmark = pytest.mark.anyio

NEUTRAL = [
    [1., 0., 0., 0.],
    [0., 1., 0., 0.],
    [0., 0., 1., 0.],
    [0., 0., 0., 1.],
]


@pytest.fixture
def anyio_backend():
    return "asyncio"


class RecordedMove:
    def __init__(self, data, sound_path=None):
        self.description = data["description"]
        self.duration = data["duration"]
        self.sound_path = sound_path


class FakeReachySDK:
    def __init__(self):
        self.commands = []

    def goto_target(self, **kwargs):
        self.commands.append(("head", kwargs))

    async def async_play_move(self, move, *, sound):
        self.commands.append(("recording", move.description, sound))

    def cancel_move(self):
        self.commands.append(("cancel_unconfirmed",))


class FakeStackChanSDK:
    def __init__(self):
        self.commands = []
        self.generation = 0

    def execution_state(self):
        return SimpleNamespace(generation=self.generation)

    def discover(self):
        return {
            "capabilities": [
                "system.state", "avatar.update",
                "motion.update.position_only",
            ],
        }

    def state(self):
        return {"native_angle_limits_tenths": {"yaw": [-350, 350], "pitch": [300, 600]}}

    def update_face(self, payload, *, expected_generation=None):
        assert expected_generation == self.generation
        self.commands.append(("face", payload))
        return (SimpleNamespace(event="completed"),)

    def move_head(self, payload, *, expected_generation=None):
        assert expected_generation == self.generation
        self.commands.append(("head", payload))
        return (SimpleNamespace(event="completed"),)

    def stop(self):
        self.generation += 1
        self.commands.append(("stop", None))
        return (SimpleNamespace(event="completed"),)


class FakeClient:
    def close(self):
        return None


def make_catalog(tmp_path):
    root = tmp_path / "reachy-moves"
    root.mkdir()
    files = {}
    for name in ("yes1", "no1", "cheerful1", "curious1"):
        blob = json.dumps({"description": name, "duration": 0.25}).encode()
        (root / f"{name}.json").write_bytes(blob)
        files[f"{name}.json"] = hashlib.sha256(blob).hexdigest()
    manifest = {
        "version": 1, "dataset_revision": "f" * 40,
        "sdk_version": "1.10.0rc5",
        "source_url": "https://huggingface.co/datasets/pollen-robotics/reachy-mini-emotions-library",
        "license": "Apache-2.0", "notices": "offline fixture",
        "files": files,
    }
    return ReachyCatalog(root, manifest, move_factory=RecordedMove)


def presence(seq, state):
    return EnsemblePresence(
        event_id=f"event-{seq}", session_id="one-4g-room",
        sequence=seq, state=state, source="agent_state",
    )


async def test_reachy_and_stackchan_execute_same_conversation_through_native_adapters(tmp_path):
    catalog = make_catalog(tmp_path)
    robot = FakeReachySDK()
    reachy = ReachyPlaybackProvider(catalog, robot, motion_authorized=True)

    native_stack = FakeStackChanSDK()
    stack = StackChanProvider(
        "stackchan",
        connector=lambda *_args, **_kwargs: (FakeClient(), native_stack),
        allowed_tools=frozenset({
            "device_info", "device_state", "face_update", "head_move",
            "action_lookup", "device_stop", "action_cancel",
        }),
    )
    await stack.connect("fake-port")
    try:
        packs = [
            await reachy_pack_from_catalog(
                "reachy", catalog, neutral_head=NEUTRAL, breath_amplitude_m=0.0,
            ),
            stackchan_conversation_pack("stackchan"),
        ]
        runtime = ExecutionRuntime(PerformanceStage())
        runtime.register("reachy", reachy)
        runtime.register("stackchan", stack)
        ensemble = ConversationEnsemble(runtime, packs, presence_ttl_s=30)
        async with runtime.run(), ensemble.run():
            await ensemble.observe(presence(0, "idle"))
            await anyio.sleep(0.15)
            await ensemble.observe(presence(1, "listening"))
            await anyio.sleep(0.15)
            await ensemble.observe(presence(2, "speaking"))
            await anyio.sleep(0.15)
            assert (await ensemble.express("yes", action_id="yes-4g"))["outcome"] == "accepted"
            await anyio.sleep(1.6)
            assert (await ensemble.express("no", action_id="no-4g"))["outcome"] == "accepted"
            await anyio.sleep(1.6)
            end = await ensemble.observe(presence(3, "disconnected"))
            assert end["session_id"] is None
            assert all(x["status"] != "blocked" for x in end["performers"].values())
        assert any(item[0] == "recording" and item[1] == "yes1" and item[2] is False
                   for item in robot.commands)
        assert any(item[0] == "recording" and item[1] == "no1" and item[2] is False
                   for item in robot.commands)
        assert any(item[0] == "head" for item in robot.commands)
        assert any(name == "face" for name, _ in native_stack.commands)
        assert any(name == "head" for name, _ in native_stack.commands)
        # An actual firmwares's terminal "completed" isn't measured pose settling.
    finally:
        await stack.close()


async def test_reachy_normal_transition_waits_for_inflight_native_head_move(tmp_path):
    class SlowReachy(FakeReachySDK):
        def goto_target(self, **kwargs):
            self.commands.append(("head", kwargs))
            time.sleep(0.15)

    catalog = make_catalog(tmp_path)
    robot = SlowReachy()
    provider = ReachyPlaybackProvider(catalog, robot, motion_authorized=True)
    pack = await reachy_pack_from_catalog("reachy", catalog, neutral_head=NEUTRAL)
    runtime = ExecutionRuntime(PerformanceStage())
    runtime.register("reachy", provider)
    ensemble = ConversationEnsemble(runtime, [pack])
    async with runtime.run(), ensemble.run():
        await ensemble.observe(presence(0, "speaking"))
        await anyio.sleep(0.04)
        result = await ensemble.observe(presence(1, "listening"))
        assert result["performers"]["reachy"]["status"] != "blocked"
        await anyio.sleep(0.2)
        assert len([c for c in robot.commands if c[0] == "head"]) >= 2
        assert not any(c[0] == "cancel_unconfirmed" for c in robot.commands)


async def test_reachy_explicit_interruption_still_quarantines_unconfirmed_stop(tmp_path):
    class SlowReachy(FakeReachySDK):
        def goto_target(self, **kwargs):
            self.commands.append(("head", kwargs))
            time.sleep(0.15)

    catalog = make_catalog(tmp_path)
    provider = ReachyPlaybackProvider(catalog, SlowReachy(), motion_authorized=True)
    pack = await reachy_pack_from_catalog("reachy", catalog, neutral_head=NEUTRAL)
    runtime = ExecutionRuntime(PerformanceStage())
    runtime.register("reachy", provider)
    ensemble = ConversationEnsemble(runtime, [pack])
    async with runtime.run(), ensemble.run():
        await ensemble.observe(presence(0, "speaking"))
        await anyio.sleep(0.04)
        event = presence(1, "listening").model_copy(update={"interrupted": True})
        result = await ensemble.observe(event)
        assert result["performers"]["reachy"]["status"] == "blocked"
