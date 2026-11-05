"""Offline, no-hardware acceptance tests for unitless speech expression shaping."""

import math
from itertools import pairwise

import pytest
from pydantic import ValidationError

from forge_puppeteer.speech_expression import (
    SpeechEnvelopeHop,
    SpeechEnvelopeWindow,
    SpeechExpressionShaper,
)


def sample(sequence=0, intensity=0.8, active=True, stream_id="utterance-1"):
    return {
        "schema": "forge.speech_envelope.v1",
        "stream_id": stream_id,
        "sequence": sequence,
        "offset_ms": sequence * 50,
        "duration_ms": 50,
        "active": active,
        "intensity": intensity,
        "rms_dbfs": -20.0,
        "timing_source": "generated_pcm",
        "acoustic_playout_measured": False,
    }


def test_incoming_speech_contract_accepts_conversationalist_wire_shape():
    frame = SpeechEnvelopeHop.model_validate(sample())
    assert frame.sequence == 0
    assert frame.intensity == 0.8
    assert frame.timing_source == "generated_pcm"
    assert frame.acoustic_playout_measured is False


@pytest.mark.parametrize("change", [
    {"schema": "unreviewed"},
    {"stream_id": ""},
    {"sequence": -1},
    {"offset_ms": 99},
    {"duration_ms": 100},
    {"intensity": 2},
    {"intensity": float("nan")},
    {"rms_dbfs": 3.0},
    {"timing_source": "speaker_measured"},
    {"acoustic_playout_measured": True},
    {"text": "private words"},
    {"pose": [0, 1, 2]},
])
def test_envelope_rejects_invalid_or_content_bearing_inputs(change):
    with pytest.raises(ValidationError):
        SpeechEnvelopeHop.model_validate({**sample(), **change})


def test_robot_neutral_expression_channels_are_bounded_and_deterministic():
    a = SpeechExpressionShaper(performer_id="reachy", stream_id="utterance-1")
    b = SpeechExpressionShaper(performer_id="reachy", stream_id="utterance-1")
    expressions = [
        a.render(SpeechEnvelopeHop.model_validate(sample(sequence=i)))
        for i in range(10)
    ]
    assert expressions[0] == b.render(SpeechEnvelopeHop.model_validate(sample()))
    assert any(x.pitch != y.pitch for x, y in pairwise(expressions))
    for x in expressions:
        assert -1 <= x.pitch <= 1 and -1 <= x.yaw <= 1
        assert -1 <= x.roll <= 1 and -1 <= x.antenna <= 1
        assert -1 <= x.x <= 1 and -1 <= x.y <= 1 and -1 <= x.z <= 1
        assert 0 <= x.mouth <= 1
        assert all(math.isfinite(getattr(x, field)) for field in (
            "pitch", "yaw", "roll", "antenna", "x", "y", "z", "mouth",
        ))


def test_silent_or_inactive_speech_is_not_a_mouth_command():
    shaper = SpeechExpressionShaper(performer_id="stackchan", stream_id="utterance-1")
    silent = shaper.render(SpeechEnvelopeHop.model_validate(
        sample(intensity=0, active=False)
    ))
    assert silent.mouth == 0
    assert all(getattr(silent, axis) == 0 for axis in (
        "pitch", "yaw", "roll", "antenna", "x", "y", "z",
    ))


def test_three_robots_have_own_texture_from_identical_speech_source():
    frame = SpeechEnvelopeHop.model_validate(sample())
    values = [
        SpeechExpressionShaper(performer_id=device, stream_id=frame.stream_id).render(frame)
        for device in ("reachy", "stackchan", "soarm101")
    ]
    assert len({(x.pitch, x.yaw) for x in values}) == 3
    assert {x.sequence for x in values} == {0}
    assert {x.mouth for x in values} == {0.8}


def test_duplicate_gap_or_wrong_stream_never_rearms_shaper():
    shaper = SpeechExpressionShaper(performer_id="reachy", stream_id="utterance-1")
    shaper.render(SpeechEnvelopeHop.model_validate(sample(0)))
    with pytest.raises(ValueError, match="out-of-order"):
        shaper.render(SpeechEnvelopeHop.model_validate(sample(0)))
    with pytest.raises(ValueError, match="out-of-order"):
        shaper.render(SpeechEnvelopeHop.model_validate(sample(3)))
    with pytest.raises(ValueError, match="identity"):
        shaper.render(SpeechEnvelopeHop.model_validate(sample(1, stream_id="other")))
    assert shaper.render(SpeechEnvelopeHop.model_validate(sample(1))).sequence == 1


def test_room_envelope_window_is_preview_only_and_bounded():
    window = SpeechEnvelopeWindow()
    assert window.snapshot() == {
        "available": False, "physical_motion_dispatched": False,
    }
    result = window.accept([sample(0), sample(1)], performer_ids=(
        "reachy", "stackchan", "soarm101",
    ))
    assert result["frames_accepted"] == 2
    assert result["physical_motion_dispatched"] is False
    preview = window.snapshot()
    assert preview["available"] is True
    assert preview["last_sequence"] == 1
    assert preview["performers"] == ["reachy", "soarm101", "stackchan"]
    assert preview["timing_source"] == "generated_pcm"
    assert preview["physical_motion_dispatched"] is False
    window.clear()
    assert window.snapshot()["available"] is False


def test_bad_batch_never_partially_updates_speech_window():
    window = SpeechEnvelopeWindow()
    window.accept([sample(0)], performer_ids=("reachy",))
    with pytest.raises(ValueError, match="gap"):
        window.accept([sample(1), sample(3)], performer_ids=("reachy",))
    assert window.snapshot()["last_sequence"] == 0
    with pytest.raises(ValueError, match="one to eight"):
        window.accept([sample(i) for i in range(9)], performer_ids=("reachy",))
    with pytest.raises(ValidationError):
        window.accept([sample(1), {**sample(2), "transcript": "private"}],
                      performer_ids=("reachy",))
    assert window.snapshot()["last_sequence"] == 0
    assert window.accept([sample(1)], performer_ids=("reachy",))["last_sequence"] == 1


def test_attributed_performers_advance_independently():
    """Two robots share one window but must not invalidate each other.

    The room offers one attributed batch per performer; alternating batches
    used to raise 'performer bindings changed' and erase the other robot's
    channels, which silently stopped both robots mid-conversation.
    """
    window = SpeechEnvelopeWindow()
    stream = "room-utterance"

    def offer(performer, sequence):
        return window.accept(
            [sample(sequence=sequence, stream_id=stream)],
            performer_ids=(performer,),
        )

    offer("reachy", 0)
    offer("stackchan", 0)          # must not reset or reject reachy
    offer("reachy", 1)
    offer("stackchan", 1)
    assert window.latest("reachy") is not None
    assert window.latest("stackchan") is not None
    offer("reachy", 2)
    assert window.latest("stackchan") is not None, (
        "a reachy batch erased stackchan's channels"
    )


def test_attributed_performer_still_rejects_its_own_gap():
    window = SpeechEnvelopeWindow()
    window.accept(
        [sample(sequence=0)], performer_ids=("reachy",),
    )
    with pytest.raises(ValueError):
        window.accept([sample(sequence=5)], performer_ids=("reachy",))
