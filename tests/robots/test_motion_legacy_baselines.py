"""Frozen authored motion contracts before native-style continuity changes.

These tests are intentionally hardware-free. They protect the old speaking
and idle options, not the safety or smoothness of physical execution.
"""

import math

import pytest

from forge_puppeteer.conversation_native import (
    IDLE_BREATH_SAMPLE_S,
    reachy_conversation_pack,
    stackchan_conversation_pack,
)

NEUTRAL = [
    [1.0, 0.0, 0.0, 0.0],
    [0.0, 1.0, 0.0, 0.0],
    [0.0, 0.0, 1.0, 0.0],
    [0.0, 0.0, 0.0, 1.0],
]
REV = "sha256:" + "a" * 64


def _reachy(**kwargs):
    return reachy_conversation_pack(
        "reachy", neutral_head=NEUTRAL, recording_revision=REV,
        available_recordings=("yes1", "no1"), **kwargs,
    )


def test_reachy_classic_is_default_and_preserves_original_speaking():
    default = _reachy()
    explicit = _reachy(expression_style="classic")
    assert default.model_dump() == explicit.model_dump()
    speaking = default.behaviors["speaking"]
    assert speaking.mode == "loop"
    assert speaking.period_s == pytest.approx(1.5)
    assert [cue.at_s for cue in speaking.cues] == pytest.approx([0.0, 0.55, 1.1])
    assert [cue.operation for cue in speaking.cues] == ["head_pose"] * 3
    assert [cue.parameters["duration_s"] for cue in speaking.cues] == pytest.approx([0.5] * 3)
    amplitude = 0.025
    # Classic yaw-labelled talking historically rotates about the head roll
    # axis; preserve the actual pose output, not an assumed semantic label.
    assert speaking.cues[0].parameters["pose"][1][2] == pytest.approx(-math.sin(amplitude))
    assert speaking.cues[1].parameters["pose"][1][2] == pytest.approx(math.sin(amplitude))
    assert speaking.cues[2].parameters["pose"] == NEUTRAL
    assert default.behaviors["yes"].cues[0].asset_id == "reachy/yes1"
    assert default.behaviors["no"].cues[0].asset_id == "reachy/no1"


def test_reachy_classic_idle_preserves_sampled_breath_and_zero_default():
    pack = _reachy(breath_amplitude_m=0.004)
    idle = pack.behaviors["idle"]
    assert idle.mode == "loop"
    assert idle.period_s == pytest.approx(10.0)
    assert [cue.at_s for cue in idle.cues] == pytest.approx(
        [i * IDLE_BREATH_SAMPLE_S for i in range(6)]
    )
    expected_z = [
        0.004 * math.sin(2 * math.pi * i * IDLE_BREATH_SAMPLE_S / 10.0)
        * (0.0 if i == 5 else 1.0)
        for i in range(6)
    ]
    assert [cue.parameters["pose"][2][3] for cue in idle.cues] == pytest.approx(expected_z)
    assert all(cue.parameters["pose"][2][3] == 0 for cue in _reachy().behaviors["idle"].cues)


def test_stackchan_legacy_size_is_default_and_preserves_speaking():
    default = stackchan_conversation_pack("stackchan")
    explicit = stackchan_conversation_pack("stackchan", speaking_mode="legacy_size")
    assert default.model_dump() == explicit.model_dump()
    speaking = default.behaviors["speaking"]
    assert speaking.mode == "loop"
    assert speaking.period_s == pytest.approx(0.9)
    assert [cue.at_s for cue in speaking.cues] == pytest.approx([0, 0.22, 0.44, 0.66])
    assert [cue.operation for cue in speaking.cues] == ["face"] * 4
    assert [cue.parameters["mouth"]["size"] for cue in speaking.cues] == [12, 42, 22, 0]


def test_stackchan_legacy_idle_preserves_head_and_face_sequence():
    idle = stackchan_conversation_pack("stackchan").behaviors["idle"]
    assert idle.mode == "loop"
    assert idle.period_s == pytest.approx(3.2)
    assert [cue.at_s for cue in idle.cues] == pytest.approx([0, 0.35, 1.2, 2.15, 2.65])
    assert [cue.operation for cue in idle.cues] == [
        "face", "head", "head", "head", "face",
    ]
    assert [cue.parameters["yaw"]["angle"] for cue in idle.cues if cue.operation == "head"] == [
        0, 40, 0,
    ]
    assert all(
        cue.parameters["pitch"]["angle"] == 450
        for cue in idle.cues if cue.operation == "head"
    )
    assert [cue.parameters["left_eye"]["weight"] for cue in idle.cues if cue.operation == "face"] == [
        45, 40,
    ]


def test_stackchan_native_weight_remains_opt_in():
    default = stackchan_conversation_pack("stackchan")
    native = stackchan_conversation_pack("stackchan", speaking_mode="native_weight")
    assert default.behaviors["speaking"] != native.behaviors["speaking"]
    assert [cue.parameters["mouth"]["weight"] for cue in native.behaviors["speaking"].cues] == [
        45, 75, 55, 0,
    ]
    assert native.behaviors["idle"].cues[0].parameters["mouth"]["weight"] == 0
    assert "weight" not in default.behaviors["idle"].cues[0].parameters["mouth"]
