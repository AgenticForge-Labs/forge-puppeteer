"""Authored breathing segments preserve the taught endpoints and timing."""

import pytest

pytest.importorskip('soarm101_motion.choreography')
from forge_puppeteer.conversation_soarm import author_breathing_segments


def test_breathing_entry_cycle_and_exit_are_continuous():
    neutral = (0.,) * 5
    bottom = (.01,) * 5
    top = (.02,) * 5
    segments = author_breathing_segments('so101', 'calibration', neutral, bottom, top)
    entry, cycle, exit_ = (segments[name] for name in ('idle_entry', 'idle_cycle', 'idle_exit'))
    assert entry.frames[0].joints_rad == neutral
    assert entry.frames[-1].joints_rad == cycle.frames[0].joints_rad == bottom
    assert cycle.frames[-1].joints_rad == exit_.frames[0].joints_rad == bottom
    assert exit_.frames[-1].joints_rad == neutral
    assert cycle.frames[-1].at_s == pytest.approx(6.)
    assert min(cycle.frames, key=lambda frame: abs(frame.at_s - 4.)).joints_rad == pytest.approx(top)
