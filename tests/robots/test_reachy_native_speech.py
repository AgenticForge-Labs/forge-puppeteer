"""Pinned SDK oscillator reference; no robot or audio connection."""

from dataclasses import astuple

import pytest

pytest.importorskip("numpy")

from forge_puppeteer.reachy_native_speech import (
    NativeSpeechGainWindow,
    ReachyNativeSpeechShaper,
    ReachySpeechOffsets,
    compose_original_speech_offset,
)


def test_native_gain_reconstructs_pinned_sdk_six_axis_offsets():
    shaper = ReachyNativeSpeechShaper(seed=7)
    assert astuple(shaper.render(0, 0.0)) == pytest.approx((0, 0, 0, 0, 0, 0))
    result = shaper.render(1, 1.248)
    # Captured from reachy-mini 1.10.0rc5 SwayRollRT with 24 kHz PCM:
    # a silent hop followed by an int16 amplitude of 8192.
    assert astuple(result) == pytest.approx((
        0.005604454297224592, 0.003867986579454588, -0.001671080125010955,
        -0.027372167711966128, -0.08103546606354095, -0.043390329170689655,
    ), abs=1e-12)
    shaper.reset()
    shaper.render(0, 0.0)
    assert shaper.render(1, 1.248) == result


def test_native_gain_rejects_invalid_or_reordered_observations():
    shaper = ReachyNativeSpeechShaper()
    for sequence, gain in [(1, 0), (0, -0.1), (0, 1.6), (0, float("nan")), (0, True)]:
        with pytest.raises(ValueError):
            shaper.render(sequence, gain)
    shaper.render(0, 1.0)
    with pytest.raises(ValueError, match="contiguous"):
        shaper.render(0, 1.0)


def test_world_offset_composes_like_daemon_for_rotated_base():
    np = pytest.importorskip("numpy")
    pytest.importorskip("reachy_mini")
    from scipy.spatial.transform import Rotation

    base = np.eye(4)
    base[:3, :3] = Rotation.from_euler("xyz", [0.12, -0.08, 0.17]).as_matrix()
    base[:3, 3] = [0.01, -0.02, 0.03]
    offsets = ReachySpeechOffsets(0.002, -0.001, 0.003, 0.02, -0.04, 0.09)
    pose = compose_original_speech_offset(base, offsets)
    expected_rotation = Rotation.from_euler("xyz", [0.02, -0.04, 0.09]).as_matrix()
    np.testing.assert_allclose(pose[:3, :3], expected_rotation @ base[:3, :3], atol=1e-12)
    np.testing.assert_allclose(pose[:3, 3], [0.012, -0.021, 0.033], atol=1e-12)
    np.testing.assert_allclose(base[:3, 3], [0.01, -0.02, 0.03], atol=1e-12)


def test_world_offset_rejects_out_of_bounds_and_nonfinite_motion():
    np = pytest.importorskip("numpy")
    pytest.importorskip("reachy_mini")
    with pytest.raises(ValueError, match="bounds"):
        compose_original_speech_offset(np.eye(4), ReachySpeechOffsets(0.1, 0, 0, 0, 0, 0))
    with pytest.raises(ValueError, match="bounds"):
        compose_original_speech_offset(np.eye(4), ReachySpeechOffsets(0, 0, 0, 0, 0, float("nan")))
    with pytest.raises(ValueError, match="base pose"):
        compose_original_speech_offset([[1]], ReachySpeechOffsets(0, 0, 0, 0, 0, 0))


def feature(stream, sequence, gain=0.5):
    return {"schema": "forge.native_speech_gain.v1", "stream_id": stream,
            "sequence": sequence, "offset_ms": sequence * 50, "duration_ms": 50,
            "gain": gain, "timing_source": "generated_pcm", "acoustic_playout_measured": False}


def test_gain_window_is_atomic_and_fences_old_streams(monkeypatch):
    now = [1000]
    monkeypatch.setattr("forge_puppeteer.reachy_native_speech.time.monotonic_ns", lambda: now[0])
    window = NativeSpeechGainWindow(max_streams=2, stale_after_ns=100)
    result = window.accept([feature("a", 0), feature("a", 1)])
    assert result["physical_motion_dispatched"] is False
    first = window.latest()
    with pytest.raises(ValueError):
        window.accept([feature("a", 2), feature("a", 4)])
    assert window.latest() == first
    assert window.accept([feature("a", 2)])["last_sequence"] == 2
    now[0] += 101
    assert window.latest() is None
    window.invalidate()
    with pytest.raises(ValueError, match="old or noninitial"):
        window.accept([feature("a", 0)])
    window.accept([feature("b", 0)])
    with pytest.raises(ValueError, match="budget"):
        window.accept([feature("c", 0)])
    assert window.latest() is not None


def test_gain_window_skips_only_forward_without_replaying_old_offsets():
    window = NativeSpeechGainWindow(allow_gaps=True)
    reference = ReachyNativeSpeechShaper()
    expected = [reference.render(i, 0.5) for i in range(20)]
    assert window.accept([feature("a", 6), feature("a", 7)])['last_sequence'] == 7
    assert window.latest() == expected[7]
    assert window.accept([feature("a", 12)])['last_sequence'] == 12
    assert window.latest() == expected[12]
    with pytest.raises(ValueError, match="out-of-order"):
        window.accept([feature("a", 8)])
    assert window.latest() == expected[12]
    with pytest.raises(ValueError, match="gap"):
        window.accept([feature("a", 1214)])
    assert window.latest() == expected[12]
    window.invalidate()
    with pytest.raises(ValueError, match="old or noninitial"):
        window.accept([feature("a", 13)])


@pytest.mark.parametrize("mutation", [
    {"sequence": True}, {"gain": True}, {"gain": float("nan")},
    {"offset_ms": 1}, {"duration_ms": 25}, {"timing_source": "speaker"},
    {"duration_ms": 50.0}, {"acoustic_playout_measured": 0},
    {"acoustic_playout_measured": True}, {"samples": [1, 2]},
])
def test_gain_window_rejects_malformed_or_private_payloads(mutation):
    window = NativeSpeechGainWindow()
    with pytest.raises(ValueError):
        window.accept([{**feature("a", 0), **mutation}])
    assert window.latest() is None
