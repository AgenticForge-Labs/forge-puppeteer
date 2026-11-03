"""Opt-in Reachy expressive authoring and SDK execution. All tests are hardware-free."""

from __future__ import annotations

import math
from typing import ClassVar

import pytest

from forge_puppeteer.conversation_native import reachy_conversation_pack
from forge_puppeteer.devices.reachy import ReachyPlaybackProvider
from forge_puppeteer.execution import ActionRequest

NEUTRAL = [
    [1.0, 0.0, 0.0, 0.0],
    [0.0, 1.0, 0.0, 0.0],
    [0.0, 0.0, 1.0, 0.0],
    [0.0, 0.0, 0.0, 1.0],
]
REV = "sha256:" + "b" * 64
ANTENNAS = (-0.17, 0.17)


def expressive_pack(**changes):
    options = {
        "neutral_head": NEUTRAL,
        "recording_revision": REV,
        "available_recordings": ("yes1", "no1", "curious1"),
        "expression_style": "expressive",
        "neutral_antennas_rad": ANTENNAS,
        "wobble_amplitude_rad": 0.05,
        "breath_amplitude_m": 0.0,
    }
    options.update(changes)
    return reachy_conversation_pack("reachy", **options)


def test_expressive_pack_coordinates_multiple_axes_without_changing_recorded_gestures():
    pack = expressive_pack()
    assert pack.revision == pack.content_revision()
    assert pack.behaviors["speaking"].period_s == 2.4
    speaking = pack.behaviors["speaking"].cues
    assert len(speaking) == 4
    assert all(c.operation == "pose_and_antennas" for c in speaking)
    assert all(c.parameters["duration_s"] == 0.6 for c in speaking)
    assert len({str(c.parameters["pose"]) for c in speaking}) == 4
    assert len({tuple(c.parameters["angles_rad"]) for c in speaking}) >= 3
    for name in ("yes", "no", "acknowledge", "curious"):
        assert pack.behaviors[name].cues[0].operation == "play_move"
        assert pack.behaviors[name].cues[0].asset_revision == REV
    assert pack.behaviors["yes"].cues[0].asset_id == "reachy/yes1"
    assert pack.behaviors["no"].cues[0].asset_id == "reachy/no1"
    idle = pack.behaviors["idle"]
    assert len(idle.cues) == 20 and idle.period_s == 10
    assert {c.parameters["pose"][2][3] for c in idle.cues} == {0.0}
    assert any(c.parameters["angles_rad"] != list(ANTENNAS) for c in idle.cues)
    assert pack.behaviors["neutral"].cues[0].parameters["angles_rad"] == list(ANTENNAS)


@pytest.mark.parametrize("changes", [
    {"neutral_antennas_rad": None},
    {"neutral_antennas_rad": (0.0, math.nan)},
    {"neutral_antennas_rad": (1.1, 0.1)},
    {"neutral_antennas_rad": ("bad", 0.0)},
    {"antenna_sway_rad": 0.3},
    {"antenna_sway_rad": -0.1},
    {"expression_style": "aggressive"},
])
def test_expressive_pack_rejects_unreviewed_or_out_of_bounds_targets(changes):
    with pytest.raises((ValueError, TypeError)):
        expressive_pack(**changes)


def test_expressive_movement_envelope_is_explicitly_larger_than_classic():
    strong = expressive_pack(wobble_amplitude_rad=0.09)
    assert strong.behaviors["speaking"].cues[0].operation == "pose_and_antennas"
    with pytest.raises(ValueError, match="talking wobble"):
        expressive_pack(wobble_amplitude_rad=0.13)
    with pytest.raises(ValueError, match="talking wobble"):
        reachy_conversation_pack(
            "reachy", neutral_head=NEUTRAL, recording_revision=REV,
            available_recordings=("yes1", "no1"), wobble_amplitude_rad=0.09,
        )


def test_classic_pack_preserves_single_axis_and_nonmoving_default_breathing():
    pack = reachy_conversation_pack(
        "reachy", neutral_head=NEUTRAL, recording_revision=REV,
        available_recordings=("yes1", "no1"),
    )
    assert all(c.operation == "head_pose" for c in pack.behaviors["speaking"].cues)
    idle = pack.behaviors["idle"]
    assert idle.mode == "hold"
    assert idle.cues == ()


def test_classic_idle_loop_is_kept_when_breathing_is_enabled():
    pack = reachy_conversation_pack(
        "reachy", neutral_head=NEUTRAL, recording_revision=REV,
        available_recordings=("yes1", "no1"), breath_amplitude_m=0.002,
    )
    idle = pack.behaviors["idle"]
    assert idle.mode == "loop"
    assert len(idle.cues) == 20
    assert any(c.parameters["pose"][2][3] != 0 for c in idle.cues)


class FakeRobot:
    def __init__(self):
        self.commands = []

    def goto_target(self, **kwargs):
        self.commands.append(kwargs)


@pytest.mark.anyio
async def test_expressive_motion_uses_single_sdk_goto_for_head_and_antennas():
    class Catalog:
        manifest: ClassVar[dict[str, str]] = {"sdk_version": "1.10.0rc5"}

    robot = FakeRobot()
    provider = ReachyPlaybackProvider(Catalog(), robot, motion_authorized=True)
    cue = expressive_pack().behaviors["speaking"].cues[0]
    request = ActionRequest(
        id="test", performer_id="reachy", caller_id="operator",
        session_id="test-room", operation=cue.operation,
        parameters=cue.parameters,
    )
    await provider.execute(request)
    assert len(robot.commands) == 1
    assert robot.commands[0]["head"] == request.parameters["pose"]
    assert robot.commands[0]["antennas"] == request.parameters["angles_rad"]
    assert robot.commands[0]["duration"] == 0.6
    assert robot.commands[0]["body_yaw"] is None

    bad = request.model_copy(update={"parameters": {
        **request.parameters, "angles_rad": [1.3, 0.0],
    }})
    with pytest.raises(ValueError, match="antenna angle exceeds"):
        await provider.validate(bad)
    assert len(robot.commands) == 1

    denied = ReachyPlaybackProvider(Catalog(), robot, motion_authorized=True,
                                    restricted_actions=True,
                                    approved_moves=frozenset({("reachy/yes1", REV)}))
    with pytest.raises(PermissionError, match="restricted"):
        await denied.validate(request)
    assert not await denied.permitted("pose_and_antennas")


@pytest.fixture
def anyio_backend():
    return "asyncio"
