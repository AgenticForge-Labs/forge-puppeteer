"""Independent authored pattern selection; no hardware operations."""

import pytest

from forge_puppeteer.conversation_native import (
    reachy_conversation_pack,
    stackchan_conversation_pack,
)
from forge_puppeteer.motion_profiles import select_motion_patterns

NEUTRAL = [
    [1.0, 0.0, 0.0, 0.0],
    [0.0, 1.0, 0.0, 0.0],
    [0.0, 0.0, 1.0, 0.0],
    [0.0, 0.0, 0.0, 1.0],
]
REV = "sha256:" + "a" * 64


def reachy(*, tilt=False):
    return reachy_conversation_pack(
        "reachy", neutral_head=NEUTRAL, recording_revision=REV,
        available_recordings=("yes1", "no1"),
        speaking_axis="tilt" if tilt else "yaw",
    )


def test_no_selection_preserves_full_revision_and_content():
    original = reachy()
    result = select_motion_patterns(original)
    assert result.model_dump() == original.model_dump()


def test_reachy_speaking_selection_keeps_legacy_idle_and_recordings():
    original = reachy()
    alternative = reachy(tilt=True)
    result = select_motion_patterns(original, speaking=alternative)
    assert result.behaviors["idle"] == original.behaviors["idle"]
    assert result.behaviors["speaking"] == alternative.behaviors["speaking"]
    assert result.behaviors["yes"] == original.behaviors["yes"]
    assert result.revision == result.content_revision()
    assert result.revision != original.revision


def test_stackchan_idle_and_speaking_selection_are_independent():
    original = stackchan_conversation_pack("stackchan")
    native = stackchan_conversation_pack("stackchan", speaking_mode="native_weight")
    alternate_idle = stackchan_conversation_pack("stackchan", yaw_amplitude=100)
    result = select_motion_patterns(original, idle=alternate_idle, speaking=native)
    assert result.behaviors["idle"] == alternate_idle.behaviors["idle"]
    assert result.behaviors["speaking"] == native.behaviors["speaking"]
    assert result.behaviors["yes"] == original.behaviors["yes"]
    assert result.revision == result.content_revision()


def test_cross_performer_replacement_fails_closed():
    original = reachy()
    wrong = stackchan_conversation_pack("stackchan")
    with pytest.raises(ValueError, match="another performer"):
        select_motion_patterns(original, speaking=wrong)


def test_mutated_revision_is_rejected_before_composition():
    original = reachy()
    bad = reachy(tilt=True)
    bad.behaviors["speaking"].cues[0].parameters["duration_s"] = 1.1
    with pytest.raises(ValueError, match="revision"):
        select_motion_patterns(original, speaking=bad)
