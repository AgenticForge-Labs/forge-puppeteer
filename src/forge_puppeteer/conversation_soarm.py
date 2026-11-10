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

CORE_EXPRESSIONS = frozenset({"yes", "no", "acknowledge", "neutral"})


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
