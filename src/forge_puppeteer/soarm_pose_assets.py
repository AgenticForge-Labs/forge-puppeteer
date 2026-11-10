"""Import operator-taught SO-ARM poses into revision-pinned joint assets.

This is an authoring utility only: it never opens hardware or edits the source
PoseLibrary. Every generated trajectory still needs a trusted SDK policy pin.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

POSE_NAMES = (
    "neutral_pup", "breathe_up_pup", "breathe_down_pup", "yes_up_pup",
    "yes_down_pup", "no_left_pup", "no_right_pup", "confused_pup",
    "surprised_pup",
)


@dataclass(frozen=True)
class GestureTiming:
    duration_s: float = 5.0
    rise_fraction: float = 0.6
    loops: int = 3
    entry_s: float = 2.0
    return_s: float = 2.0

    def __post_init__(self) -> None:
        for name in ("duration_s", "entry_s", "return_s"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if not 0.2 <= self.duration_s <= 30 or not 0.2 <= self.entry_s <= 30 or not 0.2 <= self.return_s <= 30:
            raise ValueError("gesture timings must be within 0.2..30 seconds")
        if isinstance(self.loops, bool) or not isinstance(self.loops, int) or not 1 <= self.loops <= 10:
            raise ValueError("loops must be an integer within 1..10")
        if isinstance(self.rise_fraction, bool) or not isinstance(self.rise_fraction, (int, float)) or not math.isfinite(self.rise_fraction) or not 0.1 <= self.rise_fraction <= 0.9:
            raise ValueError("rise_fraction must be within 0.1..0.9")


@dataclass(frozen=True)
class YesTiming:
    """Yes nod timing; cycle_seconds is up→down→up, repeated twice."""
    cycle_seconds: float = 1.0
    loops: int = 2
    entry_s: float = 1.0
    return_s: float = 1.0

    def __post_init__(self) -> None:
        for name in ("cycle_seconds", "entry_s", "return_s"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0.2 <= value <= 30:
                raise ValueError(f"{name} must be finite and within 0.2..30 seconds")
        if isinstance(self.loops, bool) or not isinstance(self.loops, int) or not 1 <= self.loops <= 10:
            raise ValueError("loops must be an integer within 1..10")


def import_pose_library(path: str | Path, *, robot_id: str, calibration_id: str) -> dict:
    """Read selected five-joint poses and return a stable provenance payload."""
    from soarm101_motion.constants import ARM_JOINTS
    from soarm101_motion.poses import PoseLibrary

    library = PoseLibrary(robot_id, path=path)
    poses = {}
    for name in POSE_NAMES:
        saved = library.require(name)
        if saved.source_robot_id is None or saved.source_calibration_id is None:
            raise ValueError(f"pose {name} is missing source robot/calibration provenance")
        if saved.source_robot_id != robot_id:
            raise ValueError(f"pose {name} was captured on a different robot")
        if saved.source_calibration_id != calibration_id:
            raise ValueError(f"pose {name} was captured with a different calibration")
        if saved.target_robot_id not in (None, robot_id) or saved.target_calibration_id not in (None, calibration_id):
            raise ValueError(f"pose {name} targets a different robot or calibration")
        joints = [float(saved.joints[joint]) for joint in ARM_JOINTS]
        if len(joints) != 5 or not all(math.isfinite(x) for x in joints):
            raise ValueError(f"pose {name} must contain five finite arm joints")
        body = {"schema": "forge-puppeteer.soarm-joint-pose.v1", "name": name,
                "robot_id": robot_id, "calibration_id": calibration_id,
                "joints_rad": joints}
        body.update(source_robot_id=saved.source_robot_id,
                    source_calibration_id=saved.source_calibration_id,
                    created_at=saved.created_at)
        body["revision"] = "sha256:" + hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
        poses[name] = body
    payload = {"schema": "forge-puppeteer.soarm-pose-import.v1", "source_path": str(Path(path).resolve()), "robot_id": robot_id,
               "calibration_id": calibration_id, "poses": poses}
    payload["content_revision"] = "sha256:" + hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()
    return payload


def validate_pose_assets(payload: Mapping, *, robot_id: str, calibration_id: str,
                         expected_content_revision: str | None = None) -> str:
    """Validate document hash, pose hashes and identities before reading pose values."""
    if payload.get("schema") != "forge-puppeteer.soarm-pose-import.v1":
        raise ValueError("unsupported imported pose asset schema")
    if payload.get("robot_id") != robot_id or payload.get("calibration_id") != calibration_id:
        raise ValueError("pose asset document identity does not match requested robot/calibration")
    poses = payload.get("poses")
    if not isinstance(poses, Mapping) or set(poses) != set(POSE_NAMES):
        raise ValueError("pose asset document has an incomplete pose set")
    for name, pose in poses.items():
        if not isinstance(pose, Mapping) or pose.get("name") != name:
            raise ValueError(f"invalid pose asset identity for {name}")
        if pose.get("robot_id") != robot_id or pose.get("calibration_id") != calibration_id:
            raise ValueError(f"pose {name} identity does not match requested robot/calibration")
        body = {k: v for k, v in pose.items() if k != "revision"}
        revision = "sha256:" + hashlib.sha256(json.dumps(
            body, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode()).hexdigest()
        if pose.get("revision") != revision:
            raise ValueError(f"pose {name} content revision mismatch")
    body = {k: v for k, v in payload.items() if k != "content_revision"}
    content_revision = "sha256:" + hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()
    if payload.get("content_revision") != content_revision:
        raise ValueError("pose asset document content revision mismatch")
    if expected_content_revision is not None and content_revision != expected_content_revision:
        raise ValueError("pose asset document does not match the expected content revision")
    return content_revision


def write_pose_assets(payload: Mapping, output: str | Path) -> Path:
    """Write an authored copy, never modifying the operator's library."""
    destination = Path(output)
    source = payload.get("source_path") if isinstance(payload, Mapping) else None
    if source and destination.resolve() == Path(source).resolve():
        raise ValueError("output must not overwrite the source pose library")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return destination


def author_pose_transition(name: str, *, robot_id: str, calibration_id: str,
                           start: tuple[float, ...], end: tuple[float, ...], duration_s: float):
    """Create a candidate joint-only transition for subsequent operator pinning."""
    from soarm101_motion.choreography import JointChoreography, JointFrame
    if not 0.2 <= duration_s <= 30 or len(start) != 5 or len(end) != 5:
        raise ValueError("transition needs five joints and duration within 0.2..30s")
    if not all(math.isfinite(x) for x in (*start, *end)):
        raise ValueError("joint positions must be finite")
    # Two endpoints preserve the requested exact timing. The SDK evaluates
    # its interpolated path and rejects infeasible speed/acceleration.
    return JointChoreography.author(name, robot_id, calibration_id, (
        JointFrame(0.0, tuple(start)), JointFrame(float(duration_s), tuple(end)),
    ))


def author_loop_frames(bottom: tuple[float, ...], top: tuple[float, ...], timing: GestureTiming,
                       *, rise_fraction: float | None = None) -> tuple[tuple[float, tuple[float, ...]], ...]:
    """Author bottom→top→bottom loops; endpoints are exact and entry/return separate."""
    fraction = timing.rise_fraction if rise_fraction is None else rise_fraction
    if not 0.1 <= fraction <= 0.9 or len(bottom) != 5 or len(top) != 5:
        raise ValueError("invalid rise fraction or pose size")
    frames = [(0.0, bottom)]
    t = 0.0
    for _ in range(timing.loops):
        t += timing.duration_s * fraction
        frames.append((t, top))
        t += timing.duration_s * (1.0 - fraction)
        frames.append((t, bottom))
    return tuple(frames)


def author_complete_programs(*, robot_id: str, calibration_id: str,
                             neutral: tuple[float, ...], bottom: tuple[float, ...],
                             top: tuple[float, ...], yes_up: tuple[float, ...],
                             yes_down: tuple[float, ...],
                             breathe_timing: GestureTiming | None = None,
                             yes_timing: YesTiming | None = None,
                             timing: GestureTiming | None = None):
    """Build finite neutral→pose→cycles→neutral joint-only SDK assets."""
    from soarm101_motion.choreography import JointChoreography, JointFrame

    from .conversation_soarm import smooth_joint_frames
    breathe_timing = breathe_timing or timing or GestureTiming()
    yes_timing = yes_timing or YesTiming()
    poses = (neutral, bottom, top, yes_up, yes_down)
    if any(len(p) != 5 or any(not math.isfinite(x) for x in p) for p in poses):
        raise ValueError("all poses must contain five finite joints")
    def build_breathe():
        points = [(0.0, neutral), (breathe_timing.entry_s, bottom)]
        for cycle in range(1, breathe_timing.loops + 1):
            start = breathe_timing.entry_s + (cycle - 1) * breathe_timing.duration_s
            points.extend(((start + breathe_timing.duration_s * breathe_timing.rise_fraction, top),
                           (start + breathe_timing.duration_s, bottom)))
        end = points[-1][0] + breathe_timing.return_s
        points.append((end, neutral))
        return points
    yes_points = [(0.0, neutral), (yes_timing.entry_s, yes_up)]
    for cycle in range(1, yes_timing.loops + 1):
        start = yes_timing.entry_s + (cycle - 1) * yes_timing.cycle_seconds
        half = yes_timing.cycle_seconds / 2
        yes_points.extend(((start + half, yes_down),
                           (start + yes_timing.cycle_seconds, yes_up)))
    yes_points.append((yes_points[-1][0] + yes_timing.return_s, neutral))
    def author(name, points):
        frames = tuple(JointFrame(float(t), tuple(p)) for t, p in points)
        return JointChoreography.author(name, robot_id, calibration_id, smooth_joint_frames(frames))
    breathe_points = build_breathe()
    return {"breathe": author("breathe", breathe_points), "yes": author("yes", yes_points)}


def author_programs_from_assets(payload: Mapping, *, robot_id: str, calibration_id: str,
                                expected_content_revision: str,
                                breathe_timing: GestureTiming | None = None,
                                yes_timing: YesTiming | None = None):
    """Verify pinned imported content and identities before extracting joint poses."""
    validate_pose_assets(payload, robot_id=robot_id, calibration_id=calibration_id,
                         expected_content_revision=expected_content_revision)
    poses = payload["poses"]
    joints = {name: tuple(float(x) for x in poses[f"{name}_pup"]["joints_rad"])
              for name in ("neutral", "breathe_up", "breathe_down", "yes_up", "yes_down")}
    return author_complete_programs(robot_id=robot_id, calibration_id=calibration_id,
                                    neutral=joints["neutral"], bottom=joints["breathe_down"],
                                    top=joints["breathe_up"], yes_up=joints["yes_up"],
                                    yes_down=joints["yes_down"],
                                    breathe_timing=breathe_timing, yes_timing=yes_timing)


def export_launcher_programs(programs: Mapping, launcher_config: Mapping) -> dict:
    """Return a review-only candidate; never confer launcher authorization."""
    result = json.loads(json.dumps(launcher_config))
    def strip_authority(value):
        if isinstance(value, dict):
            for key in list(value):
                if "authority" in key.lower() or key.lower() in {"motion_authority", "approved_revisions"}:
                    value.pop(key)
                else:
                    strip_authority(value[key])
        elif isinstance(value, list):
            for item in value:
                strip_authority(item)
    strip_authority(result)
    existing = {item["name"]: item for item in result["soarm101"]["programs"]}
    for name, program in programs.items():
        existing[name] = {"name": name, "revision": program.revision,
                          "frames": [{"at_s": frame.at_s, "joints_rad": list(frame.joints_rad)}
                                     for frame in program.frames]}
    result["soarm101"]["programs"] = list(existing.values())
    result["soarm101"]["approved_revisions"] = []
    result["review_only"] = True
    result["review_required"] = "Operator must separately configure trusted approvals and physical authorization."
    return result


def export_v4_candidates(programs: Mapping, *, catalog_template: Mapping,
                         robot_template: Mapping, room_template: Mapping,
                         catalog_file: str, robot_file: str) -> tuple[dict, dict, dict]:
    """Produce SDK-schema catalog and v4 bindings for review, without physical grants.

    Presence aliases are re-authored because the SDK hashes the program name.
    The policy's exact pins establish content identity, not physical approval.
    """
    from soarm101_motion.choreography import JointChoreography

    if not {"breathe", "yes"} <= set(programs):
        raise ValueError("v4 export requires breathe and yes programs")
    source = {"idle": programs["breathe"], "listening": programs["breathe"],
              "speaking": programs["breathe"], "yes": programs["yes"]}
    authored = {name: JointChoreography.author(name, p.robot_id, p.calibration_id, p.frames)
                for name, p in source.items()}
    ids = {(p.robot_id, p.calibration_id) for p in authored.values()}
    if len(ids) != 1:
        raise ValueError("program robot and calibration identities must match")
    robot_id, calibration_id = ids.pop()
    catalog = {"policy": json.loads(json.dumps(catalog_template["policy"]))}
    policy = catalog["policy"]
    if policy.get("robot_id") != robot_id or policy.get("calibration_id") != calibration_id:
        raise ValueError("catalog policy identity does not match programs")
    policy["allowed_revisions"] = [p.revision for p in authored.values()]
    policy["allow_physical_trial"] = False
    catalog["programs"] = [
        {"name": p.name, "robot_id": p.robot_id, "calibration_id": p.calibration_id,
         "revision": p.revision, "frames": [{"at_s": f.at_s, "joints_rad": list(f.joints_rad)}
                                             for f in p.frames]}
        for p in authored.values()]
    robot = json.loads(json.dumps(robot_template))
    if robot.get("kind") != "soarm101" or robot.get("id") != robot.get("provider", {}).get("instance_id"):
        raise ValueError("v4 robot template identity mismatch")
    provider = robot["provider"]
    provider["choreography_revisions"] = {name: p.revision for name, p in authored.items()}
    provider["choreography_durations_s"] = {name: p.frames[-1].at_s for name, p in authored.items()}
    provider["trusted_choreography_catalog"] = catalog_file
    room = json.loads(json.dumps(room_template))
    if room.get("schema_version") != 4 or len(room.get("robots", [])) != 1:
        raise ValueError("v4 candidate room must select only the SO-ARM")
    ref = room["robots"][0]
    if ref.get("kind") != "soarm101" or ref.get("id") != robot["id"]:
        raise ValueError("v4 room and robot identities must match")
    ref["file"] = robot_file
    room.pop("motion_authority", None)
    return catalog, robot, room
