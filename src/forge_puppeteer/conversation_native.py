"""Persona-independent Reachy Mini and StackChan conversational behavior packs.

These are Puppeteer-authored *intent*, not SDK motion implementations. They
resolve to the existing native provider operations through ExecutionRuntime.
Only an operator-reviewed neutral Reachy head transform and StackChan pose
bounds may enable physical use; SDK calibration, live limits and STOP prevail.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from typing import Literal

from .behavior_contract import BehaviorCue, BehaviorPattern, PerformerBehaviors

_log = logging.getLogger(__name__)

_SHA_PREFIX = "sha256:"
_MAX_REACHY_BREATH_M = 0.005
_MAX_REACHY_WOBBLE_RAD = 0.07
_MAX_REACHY_EXPRESSIVE_RAD = 0.12
_MAX_REACHY_ANTENNA_SWAY_RAD = 0.26
_MAX_STACKCHAN_YAW = 150
_MAX_STACKCHAN_PITCH = 100

# Reviewed motion envelope for replaying the pinned emotion recordings.
# Measured on the actual dataset: several gestures swing the head tens of
# degrees at high angular rate (no1 peaks near 196 deg/s, confused1 and
# curious1 reach 45 deg of yaw at ~150 deg/s while also turning the body up to
# ~60 deg). On this robot that lets the underside of the head contact the body.
# A recording outside this envelope is not offered as a gesture; the pack
# authors a bounded substitute where the semantic cue is still required.
_SAFE_RECORDED_YAW_RATE_DEG_S = 120.0
_SAFE_RECORDED_YAW_DEG = 30.0
_SAFE_RECORDED_BODY_YAW_DEG = 45.0


def _head_yaw_deg(head: Sequence[Sequence[float]]) -> float:
    return math.degrees(math.atan2(float(head[1][0]), float(head[0][0])))


def recorded_move_envelope(
    move: object, *, samples: int = 120,
) -> dict[str, float] | None:
    """Measure a pinned recording against the reviewed replay envelope.

    Sampling is a coarse bound on the authored trajectory, not measured motor
    output; the SDK still owns limits, collision checking and STOP.
    """
    evaluate = getattr(move, "evaluate", None)
    duration = float(getattr(move, "duration", 0.0))
    if not callable(evaluate) or duration <= 0:
        # Without the pinned SDK's trajectory API there is nothing to measure;
        # hardware-free fakes keep working and real SDK moves are always
        # measurable.
        return None
    count = max(2, min(samples, int(duration * 20) + 2))
    previous: tuple[float, float] | None = None
    max_yaw = max_rate = max_body = 0.0
    for index in range(count):
        # Stay strictly inside the recording: the pinned SDK raises when asked
        # to evaluate exactly at its final timestamp.
        at = duration * index / count
        head, _antennas, body_yaw = evaluate(at)
        if head is None:
            continue
        yaw = _head_yaw_deg(head)
        body = abs(math.degrees(float(body_yaw))) if body_yaw is not None else 0.0
        max_yaw = max(max_yaw, abs(yaw))
        max_body = max(max_body, body)
        if previous is not None:
            delta_t = at - previous[0]
            if delta_t > 0:
                max_rate = max(max_rate, abs(yaw - previous[1]) / delta_t)
        previous = (at, yaw)
    return {"yaw_deg": max_yaw, "yaw_rate_deg_s": max_rate, "body_yaw_deg": max_body}


def recorded_move_is_safe(envelope: dict[str, float]) -> bool:
    return (
        envelope["yaw_deg"] <= _SAFE_RECORDED_YAW_DEG
        and envelope["yaw_rate_deg_s"] <= _SAFE_RECORDED_YAW_RATE_DEG_S
        and envelope["body_yaw_deg"] <= _SAFE_RECORDED_BODY_YAW_DEG
    )



def _cue(at_s: float, operation: str, **parameters: object) -> BehaviorCue:
    return BehaviorCue(at_s=at_s, operation=operation, parameters=parameters)


def _pattern(
    cues: Sequence[BehaviorCue], *, period_s: float | None = None,
    source: str = "procedural",
) -> BehaviorPattern:
    if period_s is None:
        return BehaviorPattern(source=source, mode="once", cues=tuple(cues))
    return BehaviorPattern(
        source=source, mode="loop", cues=tuple(cues), period_s=period_s,
        max_cycles=3600, max_duration_s=3600,
    )


def _head(
    base: Sequence[Sequence[float]],
    *,
    pitch_rad: float = 0.0,
    yaw_rad: float = 0.0,
    roll_rad: float = 0.0,
    vertical_m: float = 0.0,
) -> list[list[float]]:
    """Compute a small orientation/translation offset around an OPERATOR pose.

    This creates the transform; Reachy SDK remains the validation/execution
    authority. Physical +Z and clearance are not inferred from this formula.
    """
    cp, sp = math.cos(pitch_rad), math.sin(pitch_rad)
    cy, sy = math.cos(yaw_rad), math.sin(yaw_rad)
    cr, sr = math.cos(roll_rad), math.sin(roll_rad)
    rotation = (
        (1.0, 0.0, 0.0),
        (0.0, cp, -sp),
        (0.0, sp, cp),
    )
    ry = (
        (cy, 0.0, sy),
        (0.0, 1.0, 0.0),
        (-sy, 0.0, cy),
    )
    rz = (
        (cr, -sr, 0.0),
        (sr, cr, 0.0),
        (0.0, 0.0, 1.0),
    )
    pitched_yawed = [
        [sum(rotation[i][k] * ry[k][j] for k in range(3)) for j in range(3)]
        for i in range(3)
    ]
    local = [
        [sum(pitched_yawed[i][k] * rz[k][j] for k in range(3)) for j in range(3)]
        for i in range(3)
    ]
    result = [
        [sum(base[i][k] * local[k][j] for k in range(3)) for j in range(3)]
         + [float(base[i][3]) + (vertical_m if i == 2 else 0.0)]
        for i in range(3)
    ]
    return result + [[0.0, 0.0, 0.0, 1.0]]


def _reachy_move(at_s: float, name: str, revision: str) -> BehaviorCue:
    return BehaviorCue(
        at_s=at_s, operation="play_move", parameters={"sound": False},
        asset_id=f"reachy/{name}", asset_revision=revision,
    )


def reachy_conversation_pack(
    performer_id: str,
    *,
    neutral_head: Sequence[Sequence[float]],
    recording_revision: str,
    available_recordings: Sequence[str],
    breath_amplitude_m: float = 0.0,
    wobble_amplitude_rad: float = 0.025,
    speaking_axis: Literal["yaw", "tilt"] = "yaw",
    listening_tilt_rad: float = 0.02,
    listening_mode: Literal["tilt", "hold"] = "tilt",
    expression_style: Literal["classic", "expressive"] = "classic",
    neutral_antennas_rad: tuple[float, float] | None = None,
    antenna_sway_rad: float = 0.18,
    wake_up_if_sleeping: bool = False,
) -> PerformerBehaviors:
    """Author Reachy motion using its existing native SDK adapter operations.

    A reviewed neutral 4x4 transform MUST be supplied; there is no fabricated
    physical home pose. Breathing displacement defaults to zero pending robot
    qualification. Recorded yes/no and optional emotions use exact catalog pins.
    """
    from .devices.reachy import ReachyPlaybackProvider
    from .execution import ActionRequest

    if (not math.isfinite(breath_amplitude_m) or
            not 0 <= breath_amplitude_m <= _MAX_REACHY_BREATH_M):
        raise ValueError("Reachy breathing amplitude must be bounded and nonnegative")
    max_wobble = (
        _MAX_REACHY_EXPRESSIVE_RAD if expression_style == "expressive"
        else _MAX_REACHY_WOBBLE_RAD
    )
    if (not math.isfinite(wobble_amplitude_rad) or
            not 0 <= wobble_amplitude_rad <= max_wobble):
        raise ValueError("Reachy talking wobble amplitude must be bounded and nonnegative")
    if speaking_axis not in {"yaw", "tilt"}:
        raise ValueError("Reachy speaking axis must be yaw or tilt")
    if (not math.isfinite(listening_tilt_rad) or
            not 0 <= listening_tilt_rad <= _MAX_REACHY_WOBBLE_RAD):
        raise ValueError("Reachy listening tilt must be bounded and nonnegative")
    if listening_mode not in {"tilt", "hold"}:
        raise ValueError("Reachy listening mode must be tilt or hold")
    if expression_style not in {"classic", "expressive"}:
        raise ValueError("Reachy expression style must be classic or expressive")
    if not math.isfinite(antenna_sway_rad) or not 0 <= antenna_sway_rad <= _MAX_REACHY_ANTENNA_SWAY_RAD:
        raise ValueError("Reachy antenna sway must be bounded and nonnegative")
    if expression_style == "expressive":
        if (
            neutral_antennas_rad is None or len(neutral_antennas_rad) != 2
            or any(not isinstance(value, (int, float)) or isinstance(value, bool)
                   or not math.isfinite(value) for value in neutral_antennas_rad)
        ):
            raise ValueError("expressive Reachy requires operator-reviewed antenna neutral")
        from .devices.reachy import _ANTENNA_ANGLE_LIMIT_RAD
        if any(abs(value) + antenna_sway_rad > _ANTENNA_ANGLE_LIMIT_RAD
               for value in neutral_antennas_rad):
            raise ValueError("Reachy expressive antenna targets exceed adapter limits")
    if not recording_revision.startswith(_SHA_PREFIX) or len(recording_revision) != 71:
        raise ValueError("Reachy recordings require a SHA-256 catalog revision")
    if any(ch not in "0123456789abcdef" for ch in recording_revision[7:]):
        raise ValueError("invalid Reachy recording revision")
    if (len(neutral_head) != 4 or any(len(row) != 4 for row in neutral_head)):
        raise ValueError("operator-reviewed neutral head must be a 4x4 transform")
    neutral = [[float(v) for v in row] for row in neutral_head]
    # Reuse existing provider geometric validation; no duplicated limits.
    ReachyPlaybackProvider._validate_native(ActionRequest(
        id="preflight", performer_id=performer_id, caller_id="author",
        session_id="preview", operation="head_pose",
        parameters={"pose": neutral, "duration_s": 0.5},
    ))
    available = frozenset(available_recordings)
    if "yes1" not in available:
        raise ValueError("pinned Reachy catalog lacks the yes1 recording")

    def head_cue(at: float, *, pitch: float = 0.0, yaw: float = 0.0,
                 roll: float = 0.0, z: float = 0.0,
                 antenna_offset: float | None = None,
                 duration: float = 0.5) -> BehaviorCue:
        pose = _head(neutral, pitch_rad=pitch, yaw_rad=yaw,
                     roll_rad=roll, vertical_m=z)
        if antenna_offset is not None:
            assert neutral_antennas_rad is not None  # checked above
            angles = [
                float(neutral_antennas_rad[0] + antenna_offset),
                float(neutral_antennas_rad[1] - antenna_offset),
            ]
            operation = "pose_and_antennas"
            parameters = {"pose": pose, "angles_rad": angles, "duration_s": duration}
        else:
            operation = "head_pose"
            parameters = {"pose": pose, "duration_s": duration}
        ReachyPlaybackProvider._validate_native(ActionRequest(
            id="preflight", performer_id=performer_id, caller_id="author",
            session_id="preview", operation=operation, parameters=parameters,
        ))
        return _cue(at, operation, **parameters)

    patterns: dict[str, BehaviorPattern] = {
        # 0.1 Hz breathing from 20 monotonic samples per 10-second cycle.
        # The native SDK interpolates each bounded target; Puppeteer owns the
        # loop and never writes raw servo positions or bypasses SDK validation.
        "idle": _pattern(tuple(
            head_cue(
                index * 0.5,
                z=breath_amplitude_m * math.sin(2 * math.pi * index / 20),
            )
            for index in range(20)
        ), period_s=10.0),
        "listening": BehaviorPattern(source="procedural", mode="hold", cues=()) if listening_mode == "hold" else _pattern((
            head_cue(0.0, pitch=listening_tilt_rad),
            head_cue(1.4, pitch=listening_tilt_rad * 0.75),
        ), period_s=3.0),
        "speaking": _pattern((
            head_cue(0.0, **{("pitch" if speaking_axis == "tilt" else "roll"): wobble_amplitude_rad}),
            head_cue(0.55, **{("pitch" if speaking_axis == "tilt" else "roll"): -wobble_amplitude_rad}),
            head_cue(1.1),
        ), period_s=1.5),
        "neutral": _pattern((head_cue(0.0),)),
        "yes": _pattern((_reachy_move(0.0, "yes1", recording_revision),), source="recorded"),
        # The pinned no1 recording shakes the head at nearly 200 deg/s while
        # pitched down, which brings the underside of the head into the body on
        # this robot. When it is filtered by the replay envelope, keep the
        # semantic refusal as a bounded authored shake instead.
        "no": _pattern((_reachy_move(0.0, "no1", recording_revision),), source="recorded")
        if "no1" in available else _pattern((
            head_cue(0.0, yaw=0.12, duration=0.3),
            head_cue(0.35, yaw=-0.12, duration=0.3),
            head_cue(0.7, yaw=0.08, duration=0.3),
            head_cue(1.05, yaw=0.0, duration=0.3),
        )),
        "acknowledge": _pattern((_reachy_move(0.0, "yes1", recording_revision),), source="recorded"),
    }
    if expression_style == "expressive":
        # One SDK goto per sample synchronously interpolates head and antennas.
        # Discrete room-state timing is not synchronized to outgoing audio.
        # No vertical translation unless the operator explicitly qualifies it.
        patterns["idle"] = _pattern(tuple(
            head_cue(
                index * 0.5,
                z=breath_amplitude_m * math.sin(2 * math.pi * index / 20),
                antenna_offset=antenna_sway_rad * math.sin(2 * math.pi * index / 4),
            ) for index in range(20)
        ), period_s=10.0)
        if listening_mode != "hold":
            patterns["listening"] = _pattern((
                head_cue(0.0, pitch=listening_tilt_rad, yaw=-wobble_amplitude_rad * 0.35,
                         antenna_offset=antenna_sway_rad * 0.35, duration=0.6),
                head_cue(1.4, pitch=listening_tilt_rad * 0.75,
                         yaw=wobble_amplitude_rad * 0.15,
                         antenna_offset=antenna_sway_rad * 0.1, duration=0.6),
            ), period_s=3.0)
        patterns["speaking"] = _pattern((
            head_cue(0.0, pitch=wobble_amplitude_rad,
                     yaw=-wobble_amplitude_rad * 0.5,
                     roll=-wobble_amplitude_rad * 0.6,
                     antenna_offset=antenna_sway_rad, duration=0.6),
            head_cue(0.6, pitch=-wobble_amplitude_rad * 0.45,
                     yaw=wobble_amplitude_rad * 0.7,
                     roll=wobble_amplitude_rad * 0.35,
                     antenna_offset=-antenna_sway_rad * 0.5, duration=0.6),
            head_cue(1.2, pitch=wobble_amplitude_rad * 0.7,
                     yaw=-wobble_amplitude_rad * 0.65,
                     roll=wobble_amplitude_rad * 0.55,
                     antenna_offset=-antenna_sway_rad, duration=0.6),
            head_cue(1.8, pitch=-wobble_amplitude_rad * 0.8,
                     yaw=wobble_amplitude_rad * 0.25,
                     roll=-wobble_amplitude_rad * 0.35,
                     antenna_offset=antenna_sway_rad * 0.4, duration=0.6),
        ), period_s=2.4)
        patterns["neutral"] = _pattern((head_cue(0.0, antenna_offset=0.0, duration=0.8),))
    for name, recording in (
        ("happy", "cheerful1"), ("curious", "curious1"),
        ("confused", "confused1"), ("excited", "enthusiastic1"),
        # Wider reviewed set from the same pinned dataset revision. Each name
        # is mapped to one exact recording; unmapped emotions stay absent
        # rather than being guessed from a filename.
        ("sad", "sad1"), ("surprised", "surprised1"),
        ("calm", "serenity1"), ("proud", "proud1"),
        ("grateful", "grateful1"), ("thoughtful", "thoughtful1"),
    ):
        if recording in available:
            patterns[name] = _pattern(
                (_reachy_move(0.0, recording, recording_revision),),
                source="recorded",
            )
    startup = (
        BehaviorPattern(
            source="sequence", mode="once",
            cues=(_cue(0.0, "wake_up"),),
        )
        if wake_up_if_sleeping else None
    )
    return PerformerBehaviors.author(performer_id, patterns, startup=startup)


async def reachy_pack_from_catalog(
    performer_id: str,
    catalog,
    *,
    neutral_head: Sequence[Sequence[float]],
    breath_amplitude_m: float = 0.0,
    wobble_amplitude_rad: float = 0.025,
    speaking_axis: Literal["yaw", "tilt"] = "yaw",
    listening_tilt_rad: float = 0.02,
    listening_mode: Literal["tilt", "hold"] = "tilt",
    expression_style: Literal["classic", "expressive"] = "classic",
    neutral_antennas_rad: tuple[float, float] | None = None,
    antenna_sway_rad: float = 0.18,
    wake_up_if_sleeping: bool = False,
) -> PerformerBehaviors:
    """Resolve available recordings only from an already-verified ReachyCatalog.

    The catalog's SDK loading, complete snapshot SHA and native recorded moves
    are authoritative; no model, client argument or guessed filename becomes
    a source of an approved recording.
    """
    from .devices.reachy import ReachyCatalog

    if not isinstance(catalog, ReachyCatalog):
        raise TypeError("a verified native ReachyCatalog is required")
    assets = await catalog.list_assets()
    if any(not item.id.startswith("reachy/") or
           item.revision != catalog.revision for item in assets):
        raise ValueError("Reachy catalog asset revision is inconsistent")
    approved: list[str] = []
    rejected: dict[str, dict[str, float]] = {}
    for item in assets:
        name = item.id[7:]
        envelope = recorded_move_envelope(catalog.get(item.id, item.revision))
        if envelope is None or recorded_move_is_safe(envelope):
            approved.append(name)
        else:
            rejected[name] = envelope
    if rejected:
        _log.warning(
            "recorded Reachy moves excluded by the replay motion envelope: %s",
            ", ".join(
                f"{name} (yaw {values['yaw_deg']:.1f} deg, "
                f"{values['yaw_rate_deg_s']:.0f} deg/s, body {values['body_yaw_deg']:.1f} deg)"
                for name, values in sorted(rejected.items())
            ),
        )
    return reachy_conversation_pack(
        performer_id, neutral_head=neutral_head,
        recording_revision=catalog.revision,
        available_recordings=approved,
        breath_amplitude_m=breath_amplitude_m,
        wobble_amplitude_rad=wobble_amplitude_rad,
        speaking_axis=speaking_axis,
        listening_tilt_rad=listening_tilt_rad,
        listening_mode=listening_mode,
        expression_style=expression_style,
        neutral_antennas_rad=neutral_antennas_rad,
        antenna_sway_rad=antenna_sway_rad,
        wake_up_if_sleeping=wake_up_if_sleeping,
    )


def stackchan_conversation_pack(
    performer_id: str,
    *,
    yaw_center: int = 0,
    pitch_center: int = 450,
    yaw_amplitude: int = 80,
    pitch_amplitude: int = 45,
    head_speed: int = 180,
    speaking_mode: Literal["legacy_size", "native_weight"] = "legacy_size",
) -> PerformerBehaviors:
    """Native SDK face/head/eyes patterns; units are tenths of a degree.

    Defaults are authoring examples, not physical calibration or authorization.
    Provider.validate still checks the connected firmware's reported bounds.
    """
    from .stackchan_validation import validate_stackchan_center

    validate_stackchan_center(yaw_center, pitch_center, yaw_amplitude,
                               pitch_amplitude, head_speed)
    if speaking_mode not in {"legacy_size", "native_weight"}:
        raise ValueError("unsupported StackChan speaking mode")

    def face(
        at: float, *, mouth: int | None = 0,
        eyes: int | None = None, eye_x: int | None = None,
        mouth_weight: int | None = None,
    ) -> BehaviorCue:
        payload: dict[str, object] = {}
        if mouth is not None:
            payload["mouth"] = {"size": mouth}
            if mouth_weight is not None:
                payload["mouth"]["weight"] = mouth_weight
        if eyes is not None or eye_x is not None:
            feature: dict[str, int] = {}
            if eyes is not None:
                feature["weight"] = eyes
            if eye_x is not None:
                # Native firmware applies coordinates as an x/y pair.
                feature.update({"x": eye_x, "y": 0})
            payload["left_eye"] = dict(feature)
            payload["right_eye"] = dict(feature)
        if not payload:
            raise ValueError("StackChan face cue must update at least one feature")
        return _cue(at, "face", **payload)

    def head(at: float, *, yaw: int = 0, pitch: int = 0) -> BehaviorCue:
        return _cue(at, "head", yaw={"angle": yaw_center + yaw, "speed": head_speed},
                    pitch={"angle": pitch_center + pitch, "speed": head_speed})

    # An interrupted native_weight speaking loop can leave the mouth open at a
    # non-zero weight, and size-based cues never clear it. Resting states
    # therefore explicitly return the mouth to closed, so the face settles to
    # neutral after speech ends. Legacy size mode is unchanged.
    rest_mouth_weight = 0 if speaking_mode == "native_weight" else None
    behaviors = {
        "idle": _pattern((
            face(0.0, mouth=0, eyes=45, mouth_weight=rest_mouth_weight), head(0.35),
            head(1.2, yaw=yaw_amplitude // 2), head(2.15),
            face(2.65, mouth=0, eyes=40, mouth_weight=rest_mouth_weight),
        ), period_s=3.2),
        "listening": _pattern((
            face(0.0, mouth=0, eyes=70, mouth_weight=rest_mouth_weight),
            head(0.25, pitch=pitch_amplitude),
            face(1.3, mouth=0, eyes=65, mouth_weight=rest_mouth_weight),
        ), period_s=2.1),
        "speaking": _pattern((
            face(0.0, mouth=12, eyes=50),
            face(0.22, mouth=42),
            face(0.44, mouth=22),
            face(0.66, mouth=0),
        ), period_s=0.9),
        "yes": _pattern((
            head(0.0, pitch=-pitch_amplitude),
            head(0.32, pitch=pitch_amplitude),
            head(0.64, pitch=-pitch_amplitude),
            head(0.96, pitch=pitch_amplitude),
            head(1.28),
        )),
        "no": _pattern((
            head(0.0, yaw=-yaw_amplitude),
            head(0.32, yaw=yaw_amplitude),
            head(0.64, yaw=-yaw_amplitude),
            head(0.96, yaw=yaw_amplitude),
            head(1.28),
        )),
        "acknowledge": _pattern((head(0.0, pitch=pitch_amplitude), head(0.35))),
        "neutral": _pattern((face(0.0, mouth=0, eyes=45, mouth_weight=rest_mouth_weight), head(0.25))),
        "happy": _pattern((face(0.0, mouth=35, eyes=80), head(0.35, yaw=yaw_amplitude // 2))),
        "curious": _pattern((face(0.0, mouth=0, eyes=70), head(0.35, pitch=pitch_amplitude))),
        "confused": _pattern((face(0.0, mouth=0, eyes=30), head(0.35, yaw=-yaw_amplitude // 2))),
        # These share the device-independent expression vocabulary. StackChan
        # renders them using its native eyes/mouth and head primitives; their
        # final visual appearance must still be reviewed on the actual screen.
        "concerned": _pattern((
            face(0.0, mouth=5, eyes=25),
            head(0.35, pitch=-pitch_amplitude // 2),
        )),
        "excited": _pattern((
            face(0.0, mouth=65, eyes=85),
            head(0.2, pitch=pitch_amplitude),
            head(0.5, pitch=-pitch_amplitude // 2),
            head(0.8),
        )),
        "surprised": _pattern((
            face(0.0, mouth=75, eyes=95),
            head(0.35, pitch=pitch_amplitude // 2),
        )),
        "sad": _pattern((
            face(0.0, mouth=5, eyes=20),
            head(0.35, pitch=-pitch_amplitude // 2),
        )),
        # Deterministic counterparts of the pinned stock BlinkModifier and
        # IdleExpressionModifier. They update only face resources, not motors,
        # and restore the explicitly authored default eye baseline/position.
        "blink": _pattern((
            face(0.0, mouth=None, eyes=25),
            face(0.2, mouth=None, eyes=45),
        )),
        "glance_left": _pattern((
            face(0.0, mouth=None, eye_x=-15),
            face(0.4, mouth=None, eye_x=0),
        )),
        "glance_right": _pattern((
            face(0.0, mouth=None, eye_x=15),
            face(0.4, mouth=None, eye_x=0),
        )),
    }
    if speaking_mode == "native_weight":
        # Optional deterministic equivalent of the stock SpeakingModifier,
        # which opens/closes mouth WEIGHT rather than mouth SIZE.
        # Still a Puppeteer-authorized loop through the native SDK, never the
        # firmware's random head-motion modifier or a second motor scheduler.
        behaviors["speaking"] = _pattern((
            _cue(0.0, "face", mouth={"weight": 45},
                 left_eye={"weight": 50}, right_eye={"weight": 50}),
            _cue(0.22, "face", mouth={"weight": 75}),
            _cue(0.44, "face", mouth={"weight": 55}),
            _cue(0.66, "face", mouth={"weight": 0}),
        ), period_s=0.9)
    return PerformerBehaviors.author(performer_id, behaviors)
