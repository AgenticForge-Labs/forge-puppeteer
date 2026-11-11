#!/usr/bin/env python3
"""Add (or replace) an SO-ARM101 gesture authored from a taught pose.

The gesture goes neutral -> taught pose -> neutral as one continuous move: a quick snap,
a slower rise to the pose, a hold, and a slow return. Neutral is the wake pose
(``startup_target``). The program is stretched in time until its peak speed and acceleration fit
the catalog policy; the shape is kept. The program is added to the trusted catalog and pinned
in the robot file (revision and duration). Backups are saved as ``*.before-gesture``. Nothing here
moves a robot; restart the ensemble after.

    python scripts/author-soarm-gesture.py CONFIG_DIR NAME --pose POSE_NAME [--dry-run]
        [--keyframes "0:0,0.35:0.25,1.4:1,1.9:1,3.3:0"]

Run it with the SDK's Python environment. POSE_NAME comes from ``pup-pose-asset.json`` next to
CONFIG_DIR (``--pose-asset`` to override).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from forge_puppeteer.conversation_soarm import author_pose_gesture, program_limits

DEFAULT_KEYFRAMES = "0:0,0.35:0.25,1.4:1,1.9:1,3.3:0"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("config_dir", type=Path)
    parser.add_argument("name")
    parser.add_argument("--pose", required=True)
    parser.add_argument("--pose-asset", type=Path)
    parser.add_argument("--keyframes", default=DEFAULT_KEYFRAMES)
    parser.add_argument("--catalog", default="catalog.json")
    parser.add_argument("--robot", default="robot.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    asset_path = args.pose_asset or args.config_dir.parent / "pup-pose-asset.json"
    pose = json.loads(asset_path.read_text())["poses"][args.pose]["joints_rad"]
    catalog_path, robot_path = args.config_dir / args.catalog, args.config_dir / args.robot
    catalog = json.loads(catalog_path.read_text())
    robot = json.loads(robot_path.read_text())
    policy = catalog["policy"]
    neutral = catalog["startup_target"]["joints_rad"]
    keyframes = tuple(tuple(float(x) for x in pair.split(":")) for pair in args.keyframes.split(","))
    program = author_pose_gesture(
        policy["robot_id"], policy["calibration_id"], neutral, pose, name=args.name,
        keyframes=keyframes,
        max_speed_deg_s=policy["max_speed_deg_s"] * 0.94,
        max_accel_deg_s2=policy["max_acceleration_deg_s2"] * 0.9)
    speed, accel = program_limits(program)
    print(f"{args.name}: {program.frames[-1].at_s:.2f} s, {len(program.frames)} frames, "
          f"peak {speed:.1f} deg/s, {accel:.0f} deg/s^2, {program.revision}")
    if len(program.frames) > policy["max_samples"]:
        print("too many frames for the catalog policy", file=sys.stderr)
        return 1
    if args.dry_run:
        return 0

    for source in (catalog_path, robot_path):
        shutil.copy2(source, source.with_name(source.name + ".before-gesture"))
    entry = {"name": args.name, "robot_id": program.robot_id, "calibration_id": program.calibration_id,
             "revision": program.revision,
             "frames": [{"at_s": f.at_s, "joints_rad": list(f.joints_rad)} for f in program.frames]}
    old = next((p for p in catalog["programs"] if p["name"] == args.name), None)
    if old is not None:
        policy["allowed_revisions"] = [r for r in policy["allowed_revisions"] if r != old["revision"]]
        catalog["programs"] = [p for p in catalog["programs"] if p["name"] != args.name]
    catalog["programs"].append(entry)
    policy["allowed_revisions"].append(program.revision)
    catalog_path.write_text(json.dumps(catalog, indent=2) + "\n")
    provider = robot["provider"]
    provider["choreography_revisions"][args.name] = program.revision
    provider["choreography_durations_s"][args.name] = program.frames[-1].at_s  # exact: the catalog check compares it
    robot_path.write_text(json.dumps(robot, indent=2) + "\n")
    print(f"updated {catalog_path.name} and {robot_path.name}; restart the ensemble")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
