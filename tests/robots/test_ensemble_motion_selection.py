"""Ensemble launch accepts explicit per-performer pattern selection, default unchanged."""

import pytest

from forge_puppeteer.conversation_ensemble import ConversationEnsemble
from forge_puppeteer.conversation_native import (
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


def reachy(axis="yaw"):
    return reachy_conversation_pack(
        "reachy", neutral_head=NEUTRAL, recording_revision=REV,
        available_recordings=("yes1", "no1"), speaking_axis=axis,
    )


def test_default_launch_preserves_exact_legacy_packs():
    r = reachy()
    s = stackchan_conversation_pack("stackchan")
    ensemble = ConversationEnsemble(None, [r, s])
    assert ensemble.packs["reachy"].model_dump() == r.model_dump()
    assert ensemble.packs["stackchan"].model_dump() == s.model_dump()


def test_launch_selects_only_explicit_speaking_pattern():
    legacy = reachy()
    alternative = reachy("tilt")
    stackchan = stackchan_conversation_pack("stackchan")
    ensemble = ConversationEnsemble(
        None, [legacy, stackchan],
        motion_patterns={"reachy": {"speaking": alternative}},
    )
    selected = ensemble.packs["reachy"]
    assert selected.behaviors["speaking"] == alternative.behaviors["speaking"]
    assert selected.behaviors["idle"] == legacy.behaviors["idle"]
    assert selected.behaviors["yes"] == legacy.behaviors["yes"]
    assert selected.revision == selected.content_revision()
    assert ensemble.packs["stackchan"].model_dump() == stackchan.model_dump()


def test_launch_rejects_unknown_performer_and_behavior():
    original = reachy()
    with pytest.raises(ValueError, match="unknown performer"):
        ConversationEnsemble(
            None, [original],
            motion_patterns={"stackchan": {"speaking": original}},
        )
    with pytest.raises(ValueError, match="unsupported"):
        ConversationEnsemble(
            None, [original],
            motion_patterns={"reachy": {"neutral": original}},
        )


def test_launch_rejects_cross_performer_substitution():
    with pytest.raises(ValueError, match="another performer"):
        ConversationEnsemble(
            None, [reachy()],
            motion_patterns={
                "reachy": {"speaking": stackchan_conversation_pack("stackchan")},
            },
        )
