import pytest

pytest.importorskip("soarm101_motion.choreography")

from soarm101_motion.constants import ARM_JOINTS
from soarm101_motion.poses import PoseLibrary, SavedPose

from forge_puppeteer.soarm_pose_assets import (
    POSE_NAMES,
    GestureTiming,
    author_complete_programs,
    author_loop_frames,
    export_launcher_programs,
    import_pose_library,
    validate_pose_assets,
)


def test_import_is_revisioned_calibration_bound_and_preserves_source(tmp_path):
    source = tmp_path / "poses.json"
    library = PoseLibrary("arm", path=source)
    for i, name in enumerate(POSE_NAMES):
        library.save(name, SavedPose(
            joints={joint: i * 0.01 for joint in ARM_JOINTS}, gripper=0.5,
            tcp_xyz_rpy=(0, 0, 0, 0, 0, 0), source_robot_id="arm",
            source_calibration_id="cal-a",
        ))
    original = source.read_bytes()
    result = import_pose_library(source, robot_id="arm", calibration_id="cal-a")
    assert source.read_bytes() == original
    assert set(result["poses"]) == set(POSE_NAMES)
    assert all(p["revision"].startswith("sha256:") for p in result["poses"].values())
    with pytest.raises(ValueError, match="different calibration"):
        import_pose_library(source, robot_id="arm", calibration_id="cal-b")


def test_default_breathe_cycles_and_validation():
    timing = GestureTiming()
    frames = author_loop_frames((0,) * 5, (1,) * 5, timing)
    assert len(frames) == 7
    assert [t for t, _ in frames] == pytest.approx([0, 3, 5, 8, 10, 13, 15])
    assert frames[0][1] == frames[-1][1]
    with pytest.raises(ValueError):
        GestureTiming(loops=0)
    with pytest.raises(ValueError):
        GestureTiming(duration_s=31)


def test_launcher_export_is_review_only_and_strips_authority():
    programs = author_complete_programs(robot_id="arm", calibration_id="cal",
        neutral=(0,) * 5, bottom=(0,) * 5, top=(0,) * 5,
        yes_up=(0,) * 5, yes_down=(0,) * 5)
    template = {"motion_authority": "I_AUTHORIZE", "reachy": {
        "physical_authority": "I_AUTHORIZE_REACHY"}, "soarm101": {
        "approved_revisions": ["trusted-old"], "programs": []}}
    result = export_launcher_programs(programs, template)
    assert "motion_authority" not in result and "physical_authority" not in result["reachy"]
    assert result["soarm101"]["approved_revisions"] == []
    assert result["review_only"] is True
    assert template["motion_authority"] == "I_AUTHORIZE"


def test_yes_cycle_duration_is_full_up_down_up():
    assets = author_complete_programs(robot_id="arm", calibration_id="cal",
        neutral=(0,) * 5, bottom=(0,) * 5, top=(0,) * 5,
        yes_up=(1,) * 5, yes_down=(-1,) * 5)
    points = {round(f.at_s, 6): f.joints_rad for f in assets["yes"].frames}
    assert points[1.0] == (1,) * 5
    assert points[1.5] == (-1,) * 5
    assert points[2.0] == (1,) * 5


def test_complete_programs_are_finite_joint_only_and_neutral_bounded():
    timing = GestureTiming(duration_s=2, entry_s=1, return_s=1, loops=3)
    assets = author_complete_programs(robot_id="arm", calibration_id="cal",
        neutral=(0,) * 5, bottom=(.1,) * 5, top=(.2,) * 5,
        yes_up=(.3,) * 5, yes_down=(.1,) * 5, timing=timing)
    breathe, yes = assets["breathe"], assets["yes"]
    assert breathe.frames[0].joints_rad == (0,) * 5
    at = {round(frame.at_s, 6): frame.joints_rad for frame in breathe.frames}
    assert at[1.0] == (.1,) * 5
    assert at[7.0] == (.1,) * 5
    assert breathe.frames[-1].joints_rad == (0,) * 5
    assert breathe.frames[-1].at_s == 8
    yes_at = {round(frame.at_s, 6): frame.joints_rad for frame in yes.frames}
    assert yes_at[0.0] == (0,) * 5
    assert yes_at[1.0] == (.3,) * 5
    assert yes_at[1.5] == (.1,) * 5
    assert yes_at[2.0] == (.3,) * 5
    assert yes_at[2.5] == (.1,) * 5
    assert yes_at[3.0] == (.3,) * 5
    assert yes.frames[-1].joints_rad == (0,) * 5
    assert yes.frames[-1].at_s == 4
    assert len(breathe.frames) <= 256
    assert all(len(frame.joints_rad) == 5 for program in assets.values() for frame in program.frames)


def test_imported_asset_content_revision_and_identity_are_checked(tmp_path):
    source = tmp_path / "poses.json"
    library = PoseLibrary("arm", path=source)
    for name in POSE_NAMES:
        library.save(name, SavedPose(joints={joint: 0 for joint in ARM_JOINTS},
            gripper=0.5, tcp_xyz_rpy=(0, 0, 0, 0, 0, 0), source_robot_id="arm",
            source_calibration_id="cal"))
    result = import_pose_library(source, robot_id="arm", calibration_id="cal")
    assert validate_pose_assets(result, robot_id="arm", calibration_id="cal") == result["content_revision"]
    tampered = {**result, "poses": {**result["poses"], "neutral_pup": {
        **result["poses"]["neutral_pup"], "joints_rad": [0.1] * 5}}}
    with pytest.raises(ValueError, match="revision mismatch"):
        validate_pose_assets(tampered, robot_id="arm", calibration_id="cal")


def test_import_rejects_missing_source_identity_and_same_output(tmp_path):
    source = tmp_path / "poses.json"
    library = PoseLibrary("arm", path=source)
    for name in POSE_NAMES:
        library.save(name, SavedPose(joints={joint: 0 for joint in ARM_JOINTS},
            gripper=0.5, tcp_xyz_rpy=(0, 0, 0, 0, 0, 0)))
    with pytest.raises(ValueError, match="missing source"):
        import_pose_library(source, robot_id="arm", calibration_id="cal-a")


def test_sdk_simulation_preflights_feasible_and_infeasible_authored_paths(tmp_path):
    from soarm101_motion.agent_control import AgentAuthorityStore
    from soarm101_motion.broker import AgentMotionRates
    from soarm101_motion.broker_sdk import SDKAgentExecutor
    from soarm101_motion.choreography import ChoreographyPolicy, preflight_choreography
    from soarm101_motion.config import SOARM101Config
    from soarm101_motion.constants import ARM_JOINTS
    from soarm101_motion.exceptions import SafetyViolationError

    executor = SDKAgentExecutor(config=SOARM101Config(robot_id="so101"),
        rates=AgentMotionRates(), simulation=True, simulation_actions=True,
        authority_store=AgentAuthorityStore(tmp_path / "lease.json"))
    try:
        executor.execute("state", {})
        arm = executor._session
        origin = tuple(arm.get_joint_positions().positions[name] for name in ARM_JOINTS)
        feasible = author_complete_programs(robot_id="so101", calibration_id="simulation",
            neutral=origin, bottom=origin, top=tuple(x + (0.001 if i == 3 else 0) for i, x in enumerate(origin)),
            yes_up=origin, yes_down=origin)["breathe"]
        policy = ChoreographyPolicy("so101", "simulation", (feasible.revision,),
            max_duration_s=30, max_speed_deg_s=8, max_acceleration_deg_s2=25)
        report = preflight_choreography(arm, feasible, policy)
        assert report.revision == feasible.revision
        bad_frames = tuple(type(f)(f.at_s, tuple(x + (0.2 if index > 0 and i == 3 else 0)
            for i, x in enumerate(f.joints_rad))) for index, f in enumerate(feasible.frames))
        from soarm101_motion.choreography import JointChoreography
        infeasible = JointChoreography.author("infeasible", "so101", "simulation", bad_frames)
        bad_policy = ChoreographyPolicy("so101", "simulation", (infeasible.revision,),
            max_duration_s=30, max_speed_deg_s=0.1, max_acceleration_deg_s2=0.1)
        with pytest.raises(SafetyViolationError):
            preflight_choreography(arm, infeasible, bad_policy)
    finally:
        executor.close()


def test_v4_export_round_trips_sdk_catalog_without_physical_authority(tmp_path):
    import json
    from soarm101_motion.broker import load_trusted_choreography_catalog
    from forge_puppeteer.soarm_pose_assets import export_v4_candidates
    programs = author_complete_programs(robot_id="arm", calibration_id="cal",
        neutral=(0,) * 5, bottom=(0,) * 5, top=(0,) * 5,
        yes_up=(0,) * 5, yes_down=(0,) * 5)
    catalog, robot, room = export_v4_candidates(programs,
        catalog_template={"policy": {"robot_id": "arm", "calibration_id": "cal",
            "allowed_revisions": [], "max_duration_s": 30, "allow_physical_trial": True}, "programs": []},
        robot_template={"kind": "soarm101", "id": "arm", "provider": {"instance_id": "arm"}},
        room_template={"schema_version": 4, "motion_authority": "old", "robots": [
            {"kind": "soarm101", "id": "arm", "file": "old"}]},
        catalog_file="catalog.json", robot_file="robot.json")
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog))
    policy, loaded = load_trusted_choreography_catalog(path)
    assert not policy.allow_physical_trial
    assert set(loaded) == {"idle", "listening", "speaking", "yes"}
    assert robot["provider"]["choreography_revisions"] == {n: p.revision for n, p in loaded.items()}
    assert robot["provider"]["choreography_durations_s"] == {n: p.frames[-1].at_s for n, p in loaded.items()}
    assert "motion_authority" not in room
    assert room["robots"][0]["file"] == "robot.json"
    assert programs["breathe"].name == "breathe"
