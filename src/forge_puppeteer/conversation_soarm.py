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


def _raised_cosine(t: float, period_s: float, cycles: int = 1) -> float:
    """0 at both ends and zero slope there; ``cycles`` smooth 0 -> 1 -> 0 bumps."""
    return (1.0 - math.cos(2.0 * math.pi * cycles * t / period_s)) / 2.0


def author_presence_programs(
    robot_id, calibration_id, neutral, *, breath_delta_rad, breath_period_s=4.0,
    talk_amplitude_deg=5.0, talk_period_s=1.6, sample_s=0.05,
):
    """Author the looping ``idle`` (breathing) and ``speaking`` (talking) programs.

    Both begin and end at ``neutral`` with zero velocity, so a loop of repeated
    programs never jerks at the seam, and every joint moves as a raised cosine:
    breathing is one slow swell per cycle (no fast legs), talking is two nods of
    ``talk_amplitude_deg`` on the wrist plus one slight wrist tilt. A program is
    also the longest the robot takes to react to a change of conversation state,
    so the breathing cycle is kept short.
    """
    from soarm101_motion.choreography import JointChoreography, JointFrame

    if len(neutral) != 5 or len(breath_delta_rad) != 5:
        raise ValueError("neutral and breathing delta need five joints")
    if not 1.0 <= breath_period_s <= 8.0 or not 1.0 <= talk_period_s <= 8.0:
        raise ValueError("breathing and talking periods must be within 1..8 seconds")
    if not 0 < talk_amplitude_deg <= 10:
        raise ValueError("talking amplitude must be within 0..10 degrees")
    amplitude = math.radians(talk_amplitude_deg)

    def build(name, period_s, offsets):
        steps = max(2, math.ceil(period_s / sample_s))
        frames = []
        for i in range(steps + 1):
            t = period_s * i / steps
            frames.append(JointFrame(float(t), tuple(
                float(n + offset(t)) for n, offset in zip(neutral, offsets, strict=True))))
        return JointChoreography.author(name, robot_id, calibration_id, tuple(frames))

    def swell(delta):
        return lambda t: delta * _raised_cosine(t, breath_period_s)

    def talk(scale, cycles):
        return lambda t: amplitude * scale * _raised_cosine(t, talk_period_s, cycles)

    still = lambda t: 0.0
    return {
        "idle": build("idle", breath_period_s, [swell(d) for d in breath_delta_rad]),
        "speaking": build("speaking", talk_period_s, [still, still, still, talk(1.0, 2), talk(0.6, 1)]),
    }


def _monotone_cubic(times, values):
    """Fritsch-Carlson shape-preserving cubic through ``values``: no overshoot, smooth speed."""
    import numpy as np

    t = np.asarray(times, dtype=float)
    y = np.asarray(values, dtype=float)
    h = np.diff(t)
    d = np.diff(y) / h
    m = np.zeros_like(y)
    for i in range(1, len(y) - 1):
        if d[i - 1] * d[i] > 0:
            w1, w2 = 2 * h[i] + h[i - 1], h[i] + 2 * h[i - 1]
            m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i])
    # Gentle ends: start and finish at rest.

    def evaluate(x):
        i = int(np.clip(np.searchsorted(t, x, side="right") - 1, 0, len(h) - 1))
        u = (x - t[i]) / h[i]
        h00, h10 = 2 * u**3 - 3 * u**2 + 1, u**3 - 2 * u**2 + u
        h01, h11 = -2 * u**3 + 3 * u**2, u**3 - u**2
        return h00 * y[i] + h10 * h[i] * m[i] + h01 * y[i + 1] + h11 * h[i] * m[i + 1]

    return evaluate


def author_pose_gesture(
    robot_id, calibration_id, neutral, pose, *, name,
    keyframes=((0.0, 0.0), (0.45, 0.30), (1.9, 1.0), (2.7, 1.0), (4.9, 0.0)),
    max_speed_deg_s=47.0, max_accel_deg_s2=450.0, sample_s=0.05,
):
    """Author ``neutral -> pose -> neutral`` as one continuous move with a chosen shape.

    ``keyframes`` are (seconds, fraction of the way to ``pose``). Joints share the shape, so the
    path is a straight line in joint space with a snap first, then a slower rise, a hold and a
    slow return. The program is stretched in time (never clipped) until its peak speed and
    acceleration fit the limits, so the shape is kept and the timing is what gives way.
    """
    from soarm101_motion.choreography import JointChoreography, JointFrame

    if len(neutral) != 5 or len(pose) != 5:
        raise ValueError("neutral and pose need five joints")
    stretch = 1.0
    for _ in range(80):
        shape = _monotone_cubic([t * stretch for t, _ in keyframes], [f for _, f in keyframes])
        end = keyframes[-1][0] * stretch
        steps = max(2, math.ceil(end / sample_s))
        frames = []
        for i in range(steps + 1):
            at = end * i / steps
            g = shape(at)
            frames.append(JointFrame(float(at), tuple(
                float(n + (p - n) * g) for n, p in zip(neutral, pose, strict=True))))
        program = JointChoreography.author(name, robot_id, calibration_id, tuple(frames))
        speed, accel = program_limits(program)
        if speed <= max_speed_deg_s and accel <= max_accel_deg_s2:
            return program
        stretch *= 1.03
    raise ValueError("gesture cannot be made to fit the speed and acceleration limits")


def program_limits(program) -> tuple[float, float]:
    """Peak joint speed (deg/s) and acceleration (deg/s^2) of a program's frames."""
    import numpy as np

    t = np.array([f.at_s for f in program.frames], dtype=float)
    q = np.degrees(np.array([f.joints_rad for f in program.frames], dtype=float))
    v = np.diff(q, axis=0) / np.diff(t)[:, None]
    a = np.diff(v, axis=0) / ((t[2:] - t[:-2]) / 2.0)[:, None]
    return float(np.abs(v).max()), float(np.abs(a).max()) if len(a) else 0.0


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
