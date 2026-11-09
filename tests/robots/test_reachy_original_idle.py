"""Reference samples from the pinned Conversation App's BreathingMove."""

import math

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("reachy_mini")

from forge_puppeteer.conversation_native import reachy_original_idle_sample


def test_original_idle_interpolation_and_breath_samples():
    start = np.eye(4)
    start[0, 3] = 0.01
    head, antennas, body = reachy_original_idle_sample(
        0.5, start_head=start, start_antennas=[0.1, -0.1],
    )
    assert head[0, 3] == pytest.approx(0.005)
    assert antennas == pytest.approx([-0.03725, 0.03725])
    assert body == 0.0

    head, antennas, body = reachy_original_idle_sample(
        1.5, start_head=start, start_antennas=[0.1, -0.1],
    )
    assert head[2, 3] == pytest.approx(0.005 * math.sin(math.pi / 10))
    assert antennas == pytest.approx([math.pi / 12, -math.pi / 12])
    assert body == 0.0


def test_original_idle_retains_upstream_transition_at_one_second():
    start = np.eye(4)
    _, before, _ = reachy_original_idle_sample(
        0.999999, start_head=start, start_antennas=[0.0, 0.0],
    )
    _, after, _ = reachy_original_idle_sample(
        1.0, start_head=start, start_antennas=[0.0, 0.0],
    )
    assert before == pytest.approx([-0.1745, 0.1745], abs=1e-6)
    assert after == pytest.approx([0.0, 0.0])


@pytest.mark.parametrize("t", [-1, float("inf"), float("nan")])
def test_idle_rejects_invalid_time(t):
    with pytest.raises(ValueError):
        reachy_original_idle_sample(t, start_head=np.eye(4), start_antennas=[0, 0])
