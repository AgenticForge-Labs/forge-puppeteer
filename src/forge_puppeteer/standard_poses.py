"""Device-neutral standard poses (``sleep`` and ``neutral``) for every performer.

Every robot kind defines the same two standard poses as named joint channels in
degrees. ``sleep`` is the parked rest pose used to start and end a session.
``neutral`` is the awake, ready-to-perform pose that idle and gestures return to.

Defaults are committed per robot kind in ``forge_puppeteer/poses/<kind>.yaml``.
A per-unit override can be placed in
``~/.config/forge-puppeteer/standard_poses/<kind>.yaml``. Override values replace
default values channel by channel.

``pose_check`` compares a read-only measurement with a standard pose:

* measured and within tolerance on every channel -> ``ok``;
* measured but outside tolerance -> not ok, with per-channel deltas to report;
* only commanded/unavailable feedback -> ``needs_confirmation`` (ask the user
  once per session).

This module never commands motion. Readers convert each device's native state
into a ``MeasuredPose``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Literal

import yaml

STANDARD_POSE_NAMES = ("sleep", "neutral")
DEFAULT_TOLERANCE_DEG = 5.0
OVERRIDE_DIR = Path.home() / ".config" / "forge-puppeteer" / "standard_poses"

FeedbackSource = Literal["measured", "commanded", "unavailable"]


@dataclass(frozen=True)
class PoseSpec:
    """One standard pose. ``channels`` is ``None`` until it has been captured."""

    name: str
    channels: dict[str, float] | None
    note: str = ""


@dataclass(frozen=True)
class RobotStandardPoses:
    kind: str
    poses: dict[str, PoseSpec]
    default_tolerance_deg: float = DEFAULT_TOLERANCE_DEG
    tolerance_deg: dict[str, float] = field(default_factory=dict)
    feedback: FeedbackSource = "measured"

    def tolerance(self, channel: str) -> float:
        return self.tolerance_deg.get(channel, self.default_tolerance_deg)

    def pose(self, name: str) -> PoseSpec:
        if name not in STANDARD_POSE_NAMES:
            raise ValueError(f"unknown standard pose {name!r}; expected one of {STANDARD_POSE_NAMES}")
        return self.poses[name]


@dataclass(frozen=True)
class MeasuredPose:
    channels: dict[str, float]
    source: FeedbackSource = "measured"


@dataclass(frozen=True)
class PoseCheck:
    pose: str
    ok: bool
    needs_confirmation: bool
    deltas_deg: dict[str, float]
    worst_channel: str | None
    worst_delta_deg: float | None
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "pose": self.pose, "ok": self.ok,
            "needs_confirmation": self.needs_confirmation,
            "deltas_deg": {k: round(v, 2) for k, v in self.deltas_deg.items()},
            "worst_channel": self.worst_channel,
            "worst_delta_deg": None if self.worst_delta_deg is None else round(self.worst_delta_deg, 2),
            "reason": self.reason,
        }


def _parse(kind: str, raw: Mapping) -> RobotStandardPoses:
    if raw.get("kind", kind) != kind:
        raise ValueError(f"standard pose file kind {raw.get('kind')!r} does not match {kind!r}")
    poses_raw = raw.get("poses") or {}
    missing = set(STANDARD_POSE_NAMES) - set(poses_raw)
    if missing:
        raise ValueError(f"{kind} standard poses missing {sorted(missing)}")
    poses = {}
    for name in STANDARD_POSE_NAMES:
        entry = poses_raw[name] or {}
        channels = entry.get("channels_deg")
        if channels is not None:
            channels = {str(k): float(v) for k, v in channels.items()}
            if not channels or not all(math.isfinite(v) for v in channels.values()):
                raise ValueError(f"{kind}.{name} channels must be finite and non-empty")
        poses[name] = PoseSpec(name, channels, str(entry.get("note", "")))
    tolerance = {str(k): float(v) for k, v in (raw.get("tolerance_deg") or {}).items()}
    default_tol = float(raw.get("default_tolerance_deg", DEFAULT_TOLERANCE_DEG))
    if default_tol <= 0 or any(v <= 0 for v in tolerance.values()):
        raise ValueError(f"{kind} tolerances must be positive")
    feedback = raw.get("feedback", "measured")
    if feedback not in ("measured", "commanded", "unavailable"):
        raise ValueError(f"{kind} feedback must be measured, commanded or unavailable")
    return RobotStandardPoses(kind, poses, default_tol, tolerance, feedback)


def _merge(base: dict, override: Mapping) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _merge(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def available_kinds() -> tuple[str, ...]:
    root = resources.files("forge_puppeteer") / "poses"
    return tuple(sorted(p.name.removesuffix(".yaml") for p in root.iterdir() if p.name.endswith(".yaml")))


def load_standard_poses(kind: str, *, override_dir: Path | None = OVERRIDE_DIR) -> RobotStandardPoses:
    """Load the committed default for ``kind`` merged with an optional local override."""
    resource = resources.files("forge_puppeteer") / "poses" / f"{kind}.yaml"
    if not resource.is_file():
        raise ValueError(f"no standard poses for robot kind {kind!r}; known: {available_kinds()}")
    raw = yaml.safe_load(resource.read_text()) or {}
    if override_dir is not None:
        local = Path(override_dir) / f"{kind}.yaml"
        if local.is_file():
            raw = _merge(raw, yaml.safe_load(local.read_text()) or {})
    return _parse(kind, raw)


def pose_check(measured: MeasuredPose, poses: RobotStandardPoses, name: str = "sleep") -> PoseCheck:
    """Compare a read-only measurement with standard pose ``name``."""
    spec = poses.pose(name)
    if spec.channels is None:
        return PoseCheck(name, False, True, {}, None, None,
                         f"{poses.kind} has no {name} pose recorded yet; capture it or confirm with the user")
    source = measured.source if poses.feedback == "measured" else poses.feedback
    if source != "measured":
        return PoseCheck(name, False, True, {}, None, None,
                         f"{poses.kind} reports {source} positions only; confirm with the user once per session")
    absent = sorted(set(spec.channels) - set(measured.channels))
    if absent:
        return PoseCheck(name, False, False, {}, None, None, f"measurement is missing channels {absent}")
    deltas = {ch: measured.channels[ch] - target for ch, target in spec.channels.items()}
    over = {ch: d for ch, d in deltas.items() if abs(d) > poses.tolerance(ch)}
    worst = max(deltas, key=lambda ch: abs(deltas[ch]) / poses.tolerance(ch))
    if over:
        detail = ", ".join(f"{ch} {d:+.1f}° (±{poses.tolerance(ch):g}°)" for ch, d in over.items())
        return PoseCheck(name, False, False, deltas, worst, deltas[worst],
                         f"outside {name} tolerance: {detail}")
    return PoseCheck(name, True, False, deltas, worst, deltas[worst], f"within {name} tolerance")


# --- device readers: native state -> MeasuredPose (read-only) -----------------

SOARM_JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll")
REACHY_HEAD_JOINTS = ("body_rotation", "stewart_1", "stewart_2", "stewart_3",
                      "stewart_4", "stewart_5", "stewart_6")
REACHY_ANTENNAS = ("right_antenna", "left_antenna")


def soarm_measured_pose(state: Mapping) -> MeasuredPose:
    """From SO-ARM broker ``robot_state`` (``joint_positions_rad``). Gripper excluded."""
    joints = state.get("joint_positions_rad")
    if not isinstance(joints, Mapping):
        return MeasuredPose({}, "unavailable")
    return MeasuredPose({k: math.degrees(float(joints[k])) for k in SOARM_JOINTS if k in joints})


def reachy_measured_pose(head_rad: Iterable[float], antennas_rad: Iterable[float]) -> MeasuredPose:
    """From Reachy Mini SDK ``get_current_joint_positions()``."""
    head, antennas = list(head_rad), list(antennas_rad)
    if len(head) != len(REACHY_HEAD_JOINTS) or len(antennas) != len(REACHY_ANTENNAS):
        return MeasuredPose({}, "unavailable")
    channels = dict(zip(REACHY_HEAD_JOINTS, (math.degrees(v) for v in head)))
    channels.update(zip(REACHY_ANTENNAS, (math.degrees(v) for v in antennas)))
    return MeasuredPose(channels)
