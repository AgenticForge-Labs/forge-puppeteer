"""Puppeteer-owned SO-ARM101 expressive segment definitions.

Motion is *authored here*, not in the SDK. An operator must inspect each
calibration-bound entire path and explicitly pin its resulting revision in a
trusted policy. No profile grants authority merely by generating a hash.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from .behavior_contract import BehaviorCue, BehaviorPattern, PerformerBehaviors

PRESENCE = frozenset({"idle", "listening", "speaking"})
CORE_EXPRESSIONS = frozenset({"yes", "no", "acknowledge", "neutral"})
EXPECTED = PRESENCE | CORE_EXPRESSIONS


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
        for before, after in zip(frames, frames[1:]):
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


def soarm_conversation_pack(
    performer_id: str,
    programs: Mapping[str, object],
    *,
    approved_revisions: Sequence[str],
) -> PerformerBehaviors:
    """Bind names to ONLY operator-approved, exactly revisioned SDK programs.

    Physical operation requires an independent SDK ChoreographyPolicy; this
    pack is semantic intent and cannot supply or change that policy.
    """
    from soarm101_motion.choreography import JointChoreography

    approved = frozenset(approved_revisions)
    if not EXPECTED <= set(programs):
        raise ValueError("all seven SO-ARM semantic programs are required")
    behaviors = {}
    for name in sorted(EXPECTED):
        program = programs[name]
        if not isinstance(program, JointChoreography) or program.name != name:
            raise ValueError("program must match authored semantic name")
        if program.revision not in approved or program.revision != program.content_revision():
            raise PermissionError("semantic program lacks exact operator revision pin")
        cue = BehaviorCue(
            at_s=0.0, operation="play_choreography", parameters={},
            asset_id=f"soarm101/choreography/{name}", asset_revision=program.revision,
        )
        if name in PRESENCE:
            # The next finite command starts only after the previous one
            # finishes; missed loop cycles are NOT caught up.
            behaviors[name] = BehaviorPattern(
                source="recorded", mode="loop", cues=(cue,),
                period_s=float(program.frames[-1].at_s + 0.5),
                max_cycles=300, max_duration_s=3600,
            )
        else:
            behaviors[name] = BehaviorPattern(
                source="recorded", mode="once", cues=(cue,),
            )
    return PerformerBehaviors.author(performer_id, behaviors)
