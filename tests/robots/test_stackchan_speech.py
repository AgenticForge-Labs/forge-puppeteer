"""Hardware-free StackChan speech-face authoring using existing SDK validation."""

from dataclasses import replace

import pytest

from forge_puppeteer.speech_expression import SpeechEnvelopeHop, SpeechExpressionShaper
from forge_puppeteer.stackchan_speech import (
    StackChanSpeechFacePreview,
    StackChanSpeechFaceProfile,
)


def hop(i: int, *, intensity: float = 0.85, active: bool = True):
    return SpeechEnvelopeHop.model_validate({
        "schema": "forge.speech_envelope.v1",
        "stream_id": "tts-1",
        "sequence": i,
        "offset_ms": 50 * i,
        "duration_ms": 50,
        "active": active,
        "intensity": intensity,
        "rms_dbfs": -15.0 if active else -100.0,
        "timing_source": "generated_pcm",
        "acoustic_playout_measured": False,
    })


def test_native_mouth_preview_never_opens_hardware_or_moves_servos():
    from stackchan_control.assets import _step

    shaping = SpeechExpressionShaper(performer_id="stackchan", stream_id="tts-1")
    preview = StackChanSpeechFacePreview(stream_id="tts-1")
    first = preview.render(shaping.render(hop(0)))
    assert first is not None and first.operation == "face"
    assert set(first.parameters) == {"mouth"}
    assert 0 <= first.parameters["mouth"]["weight"] <= 75
    assert _step({"at_s": 0, "operation": first.operation, "parameters": first.parameters})
    assert preview.render(shaping.render(hop(1))) is None
    assert preview.render(shaping.render(hop(2))) is None
    resumed = preview.render(shaping.render(hop(3)))
    assert resumed is not None and resumed.operation == "face"


def test_inactive_transition_closes_mouth_without_waiting_for_update_interval():
    shaper = SpeechExpressionShaper(performer_id="stackchan", stream_id="tts-1")
    preview = StackChanSpeechFacePreview(stream_id="tts-1")
    assert preview.render(shaper.render(hop(0))) is not None
    closing = preview.render(shaper.render(hop(1, intensity=0.0, active=False)))
    assert closing is not None
    assert closing.parameters == {"mouth": {"weight": 0}}


def test_unqualified_physical_parameters_are_rejected_while_rendering_is_inert():
    with pytest.raises(ValueError):
        StackChanSpeechFaceProfile(max_mouth_weight=101)
    with pytest.raises(ValueError):
        StackChanSpeechFaceProfile(min_interval_ms=50)
    with pytest.raises(ValueError):
        StackChanSpeechFaceProfile(max_mouth_weight=True)
    shaper = SpeechExpressionShaper(performer_id="stackchan", stream_id="tts-1")
    preview = StackChanSpeechFacePreview(stream_id="tts-1")
    frame = shaper.render(hop(0))
    with pytest.raises(ValueError, match="normalized"):
        preview.render(replace(frame, mouth=float("nan")))
    assert preview.render(frame) is not None
    with pytest.raises(ValueError, match="out of order"):
        preview.render(frame)
    with pytest.raises(ValueError, match="out of order"):
        preview.render(replace(frame, sequence=2, offset_ms=100))
