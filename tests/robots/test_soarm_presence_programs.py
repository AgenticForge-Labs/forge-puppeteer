"""The smooth breathing and talking programs and the config re-authoring script."""
import importlib.util
import json
import math
from pathlib import Path

import pytest

pytest.importorskip("soarm101_motion")

from forge_puppeteer.conversation_soarm import (
    author_presence_programs,
    program_limits,
)

NEUTRAL = (-0.0046, -0.5125, -0.0015, 1.2183, 0.0)
DELTA = tuple(math.radians(x) for x in (0.0, 5.0, 10.8, 6.9, 0.0))


def programs(**kwargs):
    return author_presence_programs("so101", "cal", NEUTRAL, breath_delta_rad=DELTA, **kwargs)


def test_programs_start_and_end_at_neutral_at_rest_so_loops_have_no_seam():
    for program in programs().values():
        first, last = program.frames[0].joints_rad, program.frames[-1].joints_rad
        assert first == pytest.approx(NEUTRAL) and last == pytest.approx(NEUTRAL)
        # The first and last steps are tiny: zero slope at both ends.
        step = max(abs(a - b) for a, b in zip(program.frames[1].joints_rad, first))
        assert math.degrees(step) < 0.25


def test_breathing_is_slow_and_continuous_with_no_fast_legs():
    idle = programs(breath_period_s=4.0)["idle"]
    speed, accel = program_limits(idle)
    assert idle.frames[-1].at_s == pytest.approx(4.0)
    assert speed < 10 and accel < 30  # the old program ran ~32 deg/s legs


def test_talking_moves_about_five_degrees_and_no_joint_more():
    speaking = programs(talk_amplitude_deg=5.0)["speaking"]
    excursions = [max(abs(f.joints_rad[j] - NEUTRAL[j]) for f in speaking.frames) for j in range(5)]
    assert math.degrees(excursions[3]) == pytest.approx(5.0, abs=0.05)  # the wrist nod
    assert all(math.degrees(e) <= 5.05 for e in excursions)
    assert excursions[0] == excursions[1] == excursions[2] == 0.0


@pytest.mark.parametrize("kwargs", [{"breath_period_s": 0.5}, {"talk_amplitude_deg": 20.0}])
def test_unreasonable_timing_or_amplitude_is_rejected(kwargs):
    with pytest.raises(ValueError):
        programs(**kwargs)


def test_script_rewrites_catalog_and_robot_pins_and_keeps_backups(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "author_presence", Path(__file__).parents[2] / "scripts" / "author-soarm-presence.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    old = author_presence_programs("so101", "cal", NEUTRAL, breath_delta_rad=DELTA,
                                    breath_period_s=6.0, talk_amplitude_deg=1.0)
    hold = {"name": "listening", "frames": [{"at_s": 0.0, "joints_rad": list(NEUTRAL)},
                                           {"at_s": 0.5, "joints_rad": list(NEUTRAL)}], "revision": "sha256:" + "1" * 64}
    def entry(program):
        return {"name": program.name, "frames": [
            {"at_s": f.at_s, "joints_rad": list(f.joints_rad)} for f in program.frames], "revision": program.revision}
    catalog = {"policy": {"robot_id": "so101", "calibration_id": "cal", "max_speed_deg_s": 50.0,
                          "max_acceleration_deg_s2": 500.0,
                          "allowed_revisions": [old["idle"].revision, old["speaking"].revision, hold["revision"]]},
               "programs": [entry(old["idle"]), hold, entry(old["speaking"])]}
    robot = {"provider": {"choreography_revisions": {"idle": old["idle"].revision, "speaking": old["speaking"].revision},
                          "choreography_durations_s": {"idle": 6.0, "speaking": 2.4}}}
    (tmp_path / "catalog.json").write_text(json.dumps(catalog))
    (tmp_path / "robot.json").write_text(json.dumps(robot))

    assert script.main([str(tmp_path), "--breath-period", "4.0"]) == 0
    new_catalog = json.loads((tmp_path / "catalog.json").read_text())
    new_robot = json.loads((tmp_path / "robot.json").read_text())
    revisions = new_robot["provider"]["choreography_revisions"]
    assert revisions["idle"] != old["idle"].revision and revisions["speaking"] != old["speaking"].revision
    assert new_robot["provider"]["choreography_durations_s"]["idle"] == 4.0
    allowed = new_catalog["policy"]["allowed_revisions"]
    assert set(allowed) == {revisions["idle"], revisions["speaking"], hold["revision"]}
    assert (tmp_path / "catalog.json.before-presence").exists()
    assert (tmp_path / "robot.json.before-presence").exists()
