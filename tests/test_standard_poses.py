import math

import pytest

from forge_puppeteer.standard_poses import (
    STANDARD_POSE_NAMES,
    MeasuredPose,
    available_kinds,
    load_standard_poses,
    pose_check,
    reachy_measured_pose,
    soarm_measured_pose,
)


def _soarm_state(**offsets_deg):
    poses = load_standard_poses("soarm101", override_dir=None)
    sleep = poses.pose("sleep").channels
    return {"joint_positions_rad": {k: math.radians(v + offsets_deg.get(k, 0.0)) for k, v in sleep.items()}}


def test_every_kind_defines_sleep_and_neutral():
    assert set(available_kinds()) >= {"soarm101", "reachy", "stackchan"}
    for kind in available_kinds():
        poses = load_standard_poses(kind, override_dir=None)
        assert set(poses.poses) == set(STANDARD_POSE_NAMES)


def test_soarm_sleep_shoulder_pan_is_zero():
    assert load_standard_poses("soarm101", override_dir=None).pose("sleep").channels["shoulder_pan"] == 0.0


def test_within_tolerance_is_ok():
    poses = load_standard_poses("soarm101", override_dir=None)
    check = pose_check(soarm_measured_pose(_soarm_state(shoulder_pan=3.0, elbow_flex=-4.9)), poses)
    assert check.ok and not check.needs_confirmation
    assert check.worst_channel == "elbow_flex"


def test_outside_tolerance_reports_deltas():
    poses = load_standard_poses("soarm101", override_dir=None)
    check = pose_check(soarm_measured_pose(_soarm_state(shoulder_lift=12.0)), poses)
    assert not check.ok and not check.needs_confirmation
    assert "shoulder_lift +12.0°" in check.reason
    assert check.as_dict()["deltas_deg"]["shoulder_lift"] == pytest.approx(12.0)


def test_missing_channel_or_unavailable_state_is_not_ok():
    poses = load_standard_poses("soarm101", override_dir=None)
    assert not pose_check(soarm_measured_pose({}), poses).ok
    partial = MeasuredPose({"shoulder_pan": 0.0})
    assert "missing channels" in pose_check(partial, poses).reason


def test_commanded_feedback_needs_confirmation():
    stackchan = load_standard_poses("stackchan", override_dir=None)
    assert pose_check(MeasuredPose({}), stackchan).needs_confirmation
    soarm = load_standard_poses("soarm101", override_dir=None)
    commanded = MeasuredPose(soarm_measured_pose(_soarm_state()).channels, "commanded")
    check = pose_check(commanded, soarm)
    assert not check.ok and check.needs_confirmation


def test_unrecorded_pose_needs_confirmation():
    stackchan = load_standard_poses("stackchan", override_dir=None)
    assert stackchan.pose("neutral").channels is None
    assert pose_check(MeasuredPose({}), stackchan, "neutral").needs_confirmation


def test_reachy_neutral_is_sdk_wake_up_end_pose():
    from reachy_mini.reachy_mini import INIT_ANTENNAS_JOINT_POSITIONS
    init_head = [6.959852054044218e-07, 0.5251518455536499, -0.668710345667336,
                 0.6067086443974802, -0.606711497194891, 0.6687148024583701,
                 -0.5251586523105128]  # SDK goto_sleep() init_positions
    measured = reachy_measured_pose(init_head, INIT_ANTENNAS_JOINT_POSITIONS)
    check = pose_check(measured, load_standard_poses("reachy", override_dir=None), "neutral")
    assert check.ok and abs(check.worst_delta_deg) < 0.1


def test_reachy_reader_matches_sdk_sleep_constants():
    head = [0, -0.9848156658225817, 1.2624661884298831, -0.24390294527381684,
            0.20555342557667577, -1.2363885150358267, 1.0032234352772091]
    measured = reachy_measured_pose(head, [-3.05, 3.05])
    assert pose_check(measured, load_standard_poses("reachy", override_dir=None)).ok
    assert reachy_measured_pose(head[:3], [0, 0]).source == "unavailable"


def test_local_override_merges_per_channel_and_tolerance(tmp_path):
    (tmp_path / "soarm101.yaml").write_text(
        "default_tolerance_deg: 2\n"
        "poses:\n  sleep:\n    channels_deg:\n      wrist_roll: 10.0\n")
    poses = load_standard_poses("soarm101", override_dir=tmp_path)
    sleep = poses.pose("sleep").channels
    assert sleep["wrist_roll"] == 10.0 and sleep["shoulder_pan"] == 0.0
    assert poses.tolerance("elbow_flex") == 2


def test_unknown_kind_and_pose_fail():
    with pytest.raises(ValueError):
        load_standard_poses("nope", override_dir=None)
    with pytest.raises(ValueError):
        load_standard_poses("soarm101", override_dir=None).pose("dance")
