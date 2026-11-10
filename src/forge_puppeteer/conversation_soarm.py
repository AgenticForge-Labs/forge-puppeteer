"""Puppeteer-owned SO-ARM101 expressive segment definitions.

Motion is *authored here*, not in the SDK. An operator must inspect each
calibration-bound entire path and explicitly pin its resulting revision in a
trusted policy. No profile grants authority merely by generating a hash.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from itertools import pairwise
from pathlib import Path

from .behavior_contract import BehaviorCue, BehaviorPattern, PerformerBehaviors

PRESENCE = frozenset({"idle", "listening", "speaking"})
CORE_EXPRESSIONS = frozenset({"yes", "no", "acknowledge", "neutral"})
EXPECTED = PRESENCE | CORE_EXPRESSIONS


def import_taught_pose_candidates(
    pose_library: Mapping[str, object],
    *,
    robot_id: str,
    calibration_id: str,
    neutral_pose: str,
    gesture_poses: Mapping[str, str],
    segment_duration_s: float = 1.2,
) -> dict[str, object]:
    """Convert SDK saved poses into review-only, neutral-to-pose candidates.

    Source file paths are intentionally not accepted or serialized. The output
    contains calibration-bound joint content and must be stored locally; its
    revisions are content identities, not operator approval pins.
    """
    from soarm101_motion.choreography import JointChoreography, JointFrame
    from soarm101_motion.constants import ARM_JOINTS

    if (not robot_id or not calibration_id or pose_library.get("robot_id") != robot_id
            or pose_library.get("schema_version") != 1):
        raise ValueError("pose library schema and robot identity must match explicit target")
    if (isinstance(segment_duration_s, bool)
            or not isinstance(segment_duration_s, (int, float))
            or not math.isfinite(segment_duration_s)
            or not 0.25 <= segment_duration_s <= 2.5):
        raise ValueError("each candidate segment duration must be within [0.25, 2.5] seconds")
    if not gesture_poses or any(
        name not in CORE_EXPRESSIONS or name in {"neutral"}
        or not isinstance(pose_name, str) or not pose_name
        for name, pose_name in gesture_poses.items()
    ):
        raise ValueError("gesture mapping must use named one-shot expressions and saved pose names")
    raw_poses = pose_library.get("poses")
    if not isinstance(raw_poses, Mapping):
        raise ValueError("SDK pose library must contain a poses mapping")  # noqa: TRY004 - ValueError is the documented contract for bad pose input

    def joint_values(pose_name: str) -> tuple[float, ...]:
        raw = raw_poses.get(pose_name)
        if not isinstance(raw, Mapping):
            raise ValueError(f"saved pose {pose_name!r} is unavailable")  # noqa: TRY004 - ValueError is the documented contract for bad pose input
        identities = (
            (raw.get("source_robot_id"), raw.get("source_calibration_id")),
            (raw.get("target_robot_id"), raw.get("target_calibration_id")),
        )
        matching_identity = False
        for saved_robot, saved_calibration in identities:
            if saved_robot is not None and saved_robot != robot_id:
                raise ValueError("saved pose belongs to a different robot")
            if saved_calibration is not None and saved_calibration != calibration_id:
                raise ValueError("saved pose belongs to a different calibration")
            matching_identity |= saved_robot == robot_id and saved_calibration == calibration_id
        if not matching_identity:
            raise ValueError("saved pose lacks matching robot and calibration provenance")
        joints = raw.get("joints")
        if not isinstance(joints, Mapping) or set(joints) != set(ARM_JOINTS):
            raise ValueError("saved pose must contain exactly the five arm joints")
        values = tuple(joints[name] for name in ARM_JOINTS)
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
               for v in values):
            raise ValueError("saved pose joint values must be finite numbers")
        return tuple(float(v) for v in values)

    neutral = joint_values(neutral_pose)
    steps = math.ceil(segment_duration_s / 0.02)
    programs: list[dict[str, object]] = []
    for name, pose_name in sorted(gesture_poses.items()):
        target = joint_values(pose_name)
        frames = [JointFrame(0.0, neutral)]
        for segment, (start, end) in enumerate(((neutral, target), (target, neutral))):
            base = segment * segment_duration_s
            for index in range(1, steps + 1):
                u = index / steps
                eased = u * u * (3.0 - 2.0 * u)
                frames.append(JointFrame(
                    base + segment_duration_s * u,
                    tuple(a + (b - a) * eased for a, b in zip(start, end, strict=True)),
                ))
        program = JointChoreography.author(
            name, robot_id, calibration_id, tuple(frames),
        )
        programs.append({
            "name": name,
            "source_pose": pose_name,
            "revision": program.revision,
            "frames": [
                {"at_s": frame.at_s, "joints_rad": list(frame.joints_rad)}
                for frame in program.frames
            ],
        })
    return {
        "schema": "forge.soarm101.choreography-candidates.v1",
        "status": "REVIEW_REQUIRED",
        "robot_id": robot_id,
        "calibration_id": calibration_id,
        "neutral_pose": neutral_pose,
        "segment_duration_s": float(segment_duration_s),
        "programs": programs,
        "approval": "No revisions are approved by this import. Review geometry, run SDK preflight, then pin revisions in trusted policy.",
    }


def import_taught_pose_candidate_file(
    pose_library_path: str | Path,
    **kwargs,
) -> dict[str, object]:
    """Read one SDK PoseLibrary JSON file without retaining its local path."""
    data = json.loads(Path(pose_library_path).expanduser().read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise ValueError("SDK pose library must be a JSON object")  # noqa: TRY004 - ValueError is the documented contract for bad pose input
    return import_taught_pose_candidates(data, **kwargs)


def author_soarm_reference_programs(
    *,
    robot_id: str,
    calibration_id: str,
    neutral_joints_rad: Sequence[float],
    idle_wrist_flex_amplitude_rad: float = 0.0,
    listen_shoulder_lift_rad: float = 0.0,
    listen_wrist_roll_rad: float = 0.0,
    speaking_wrist_roll_amplitude_rad: float = 0.0,
    nod_wrist_flex_amplitude_rad: float = 0.0,
    shake_wrist_roll_amplitude_rad: float = 0.0,
) -> dict[str, object]:
    """Author candidate complete trajectories around a taught Overhead neutral.

    They are NOT safety-validated, not physical-robot-frame +Z and not approved
    assets until independent operator review and SDK whole-path preflight.
    Both the start and end are neutral. No gripper commands are authored.
    """
    from soarm101_motion.choreography import JointChoreography, JointFrame
    from soarm101_motion.constants import ARM_JOINTS

    if (len(neutral_joints_rad) != len(ARM_JOINTS)
            or any(isinstance(x, bool) or not isinstance(x, (int, float))
                   or not math.isfinite(x) for x in neutral_joints_rad)):
        raise ValueError("five finite operator-taught neutral joints required")
    max_amplitudes = {
        "idle_wrist_flex_amplitude_rad": (idle_wrist_flex_amplitude_rad, math.radians(3)),
        "listen_shoulder_lift_rad": (listen_shoulder_lift_rad, math.radians(8)),
        "listen_wrist_roll_rad": (listen_wrist_roll_rad, math.radians(30)),
        "speaking_wrist_roll_amplitude_rad": (
            speaking_wrist_roll_amplitude_rad, math.radians(4)
        ),
        "nod_wrist_flex_amplitude_rad": (nod_wrist_flex_amplitude_rad, math.radians(4)),
        "shake_wrist_roll_amplitude_rad": (shake_wrist_roll_amplitude_rad, math.radians(4)),
    }
    for name, (value, ceiling) in max_amplitudes.items():
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or abs(value) > ceiling):
            raise ValueError(f"{name} exceeds candidate authoring bounds")
    neutral = tuple(float(x) for x in neutral_joints_rad)
    indices = {name: ARM_JOINTS.index(name) for name in ARM_JOINTS}

    def frame(at_s: float, **offsets: float):
        values = list(neutral)
        for name, offset in offsets.items():
            values[indices[name]] += offset
        return JointFrame(at_s, tuple(values))

    # Entire periodic motion is intentionally represented as finite programs.
    # The SDK checks all interpolated physical targets as a whole.
    defs = {
        "idle": (
            (frame(0.0), frame(0.5))
            if idle_wrist_flex_amplitude_rad == 0
            else tuple(
                frame(
                    i * 0.5,
                    wrist_flex=idle_wrist_flex_amplitude_rad * math.sin(math.tau * i / 20),
                ) for i in range(21)
            )
        ),  # Full 0.1 Hz wrist-flex cycle only when physically approved.
        "listening": (
            frame(0.0),
            frame(2.5, shoulder_lift=listen_shoulder_lift_rad,
                  wrist_roll=listen_wrist_roll_rad),
            frame(5.0),
        ),
        "speaking": (
            frame(0.0),
            frame(0.8, wrist_roll=speaking_wrist_roll_amplitude_rad),
            frame(1.6, wrist_roll=-speaking_wrist_roll_amplitude_rad),
            frame(2.4),
        ),
        "yes": (
            frame(0.0),
            frame(0.8, wrist_flex=nod_wrist_flex_amplitude_rad),
            frame(1.6, wrist_flex=-nod_wrist_flex_amplitude_rad),
            frame(2.4),
        ),
        "no": (
            frame(0.0),
            frame(0.8, wrist_roll=shake_wrist_roll_amplitude_rad),
            frame(1.6, wrist_roll=-shake_wrist_roll_amplitude_rad),
            frame(2.4),
        ),
        "acknowledge": (
            frame(0.0),
            frame(0.8, wrist_flex=nod_wrist_flex_amplitude_rad),
            frame(1.6),
        ),
        "neutral": (frame(0.0), frame(0.5)),
    }
    def smooth_segment(frames):
        # Interpolate WITHIN the authored control points using a zero-slope
        # cubic easing at each boundary. Straight line control points have
        # acceleration discontinuities that the native SDK rightly rejects
        # under the operator's 25 deg/s² envelope.
        expanded = [frames[0]]
        # The SDK's actual default command stream is 50 Hz (20 ms), not
        # 20 Hz. Sparse 100 ms keyframes produce piecewise-linear slope
        # jumps and spuriously high *real* discrete acceleration under the
        # SDK's correct 50 Hz metrics. Author at its native cadence for short
        # gestures; longer 10 s idle is bounded by the 256-frame contract.
        total_duration = frames[-1].at_s
        sample_period_s = max(0.02, total_duration / 240.0)
        for before, after in pairwise(frames):
            duration = after.at_s - before.at_s
            steps = max(1, math.ceil(duration / sample_period_s))
            for i in range(1, steps + 1):
                u = i / steps
                eased = u * u * (3 - 2 * u)
                expanded.append(JointFrame(
                    before.at_s + duration * u,
                    tuple(a + (b - a) * eased for a, b in zip(
                        before.joints_rad, after.joints_rad, strict=True,
                    )),
                ))
        if len(expanded) > 256:
            raise ValueError("authored choreography exceeds native SDK frame ceiling")
        return tuple(expanded)

    return {
        name: JointChoreography.author(
            name, robot_id, calibration_id, smooth_segment(frames),
        )
        for name, frames in defs.items()
    }


def author_breathing_segments(robot_id, calibration_id, neutral, bottom, top, *,
                              entry_s=4.0, rise_s=4.0, descent_s=2.0):
    """Author separate neutral entry, closed breathing cycle and neutral exit.

    The caller must validate each segment with the SDK and coordinate the
    exit before starting any behavior whose first frame is neutral.
    """
    from soarm101_motion.choreography import JointChoreography, JointFrame

    if min(entry_s, rise_s, descent_s) <= 0:
        raise ValueError("breathing durations must be positive")
    def make(name, points):
        frames = tuple(JointFrame(float(t), tuple(p)) for t, p in points)
        return JointChoreography.author(name, robot_id, calibration_id,
                                        smooth_joint_frames(frames))
    return {
        "idle_entry": make("idle_entry", [(0, neutral), (entry_s, bottom)]),
        "idle_cycle": make("idle_cycle", [(0, bottom), (rise_s, top),
                                         (rise_s + descent_s, bottom)]),
        "idle_exit": make("idle_exit", [(0, bottom), (entry_s, neutral)]),
    }


def smooth_joint_frames(frames: Sequence[object]):
    """Apply the same zero-slope, SDK-cadence easing used by reference programs."""
    from soarm101_motion.choreography import JointFrame

    expanded = [frames[0]]
    total_duration = frames[-1].at_s
    sample_period_s = max(0.02, total_duration / 240.0)
    for before, after in pairwise(frames):
        duration = after.at_s - before.at_s
        steps = max(1, math.ceil(duration / sample_period_s))
        for i in range(1, steps + 1):
            u = i / steps
            eased = u * u * (3 - 2 * u)
            expanded.append(JointFrame(
                before.at_s + duration * u,
                tuple(a + (b - a) * eased for a, b in zip(
                    before.joints_rad, after.joints_rad, strict=True,
                )),
            ))
    if len(expanded) > 256:
        raise ValueError("authored choreography exceeds native SDK frame ceiling")
    return tuple(expanded)


def soarm_conversation_pack(
    performer_id: str,
    programs: Mapping[str, object],
    *,
    approved_revisions: Sequence[str],
    startup_target: object | None = None,
    idle_behavior: str = "legacy",
) -> PerformerBehaviors:
    """Bind names to ONLY operator-approved, exactly revisioned SDK programs.

    Physical operation requires an independent SDK ChoreographyPolicy; this
    pack is semantic intent and cannot supply or change that policy.
    """
    from soarm101_motion.choreography import JointChoreography

    approved = frozenset(approved_revisions)
    optional = set(programs) & {"breathe"}
    if idle_behavior not in {"legacy", "breathe"}:
        raise ValueError("SO-ARM idle behavior must be legacy or breathe")
    if idle_behavior == "breathe" and "breathe" not in programs:
        raise ValueError("breathe idle requires an explicitly approved breathe program")
    if not EXPECTED <= set(programs) or set(programs) - EXPECTED - {"breathe"}:
        raise ValueError("all seven SO-ARM semantic programs are required")
    # Check SDK object types before inspecting trajectory frames. The optional
    # startup target introduces a neutral alignment contract; legacy packs
    # without startup retain their independently approved trajectory endpoints.
    for name, program in programs.items():
        if not isinstance(program, JointChoreography) or program.name != name:
            raise ValueError("program must match authored semantic name")
    neutral = None
    if startup_target is not None:
        neutral = programs["idle"].frames[0].joints_rad
        for name in ("yes", "breathe"):
            if name in programs:
                frames = programs[name].frames
                if frames[0].joints_rad != neutral or frames[-1].joints_rad != neutral:
                    raise ValueError(f"{name} must enter and return to the configured idle neutral")
    behaviors = {}
    for name in sorted(EXPECTED | optional):
        program = programs[name]
        if program.revision not in approved or program.revision != program.content_revision():
            raise PermissionError("semantic program lacks exact operator revision pin")
        cue = BehaviorCue(
            at_s=0.0, operation="play_choreography", parameters={},
            asset_id=f"soarm101/choreography/{name}", asset_revision=program.revision,
        )
        if name in PRESENCE:
            # The next finite command starts only after the previous one
            # finishes; missed loop cycles are NOT caught up.
            idle_cue = cue
            if name == "idle" and idle_behavior == "breathe":
                breathe = programs["breathe"]
                idle_cue = BehaviorCue(
                    at_s=0.0, operation="play_choreography", parameters={},
                    asset_id="soarm101/choreography/breathe",
                    asset_revision=breathe.revision,
                )
                program = breathe
            behaviors[name] = BehaviorPattern(
                source="recorded", mode="loop", cues=(idle_cue,),
                period_s=float(program.frames[-1].at_s + 0.5),
                max_cycles=300, max_duration_s=3600,
            )
        else:
            behaviors[name] = BehaviorPattern(
                source="recorded", mode="once", cues=(cue,),
            )
    startup = None
    if startup_target is not None:
        from soarm101_motion.choreography import JointTarget
        if not isinstance(startup_target, JointTarget) or startup_target.name != "wake_to_neutral":
            raise ValueError("startup requires the exact pinned wake-to-neutral target")
        if startup_target.revision not in approved:
            raise PermissionError("wake-to-neutral target lacks an exact operator revision pin")
        if startup_target.joints_rad != neutral:
            raise ValueError("wake-to-neutral target must exactly match the configured idle neutral")
        startup = BehaviorPattern(
            source="recorded", mode="once",
            cues=(BehaviorCue(
                at_s=0.0, operation="wake_to_neutral", parameters={},
                asset_id="soarm101/target/wake_to_neutral",
                asset_revision=startup_target.revision,
            ),),
        )
    return PerformerBehaviors.author(performer_id, behaviors, startup=startup)
