#!/usr/bin/env python3
"""Re-author an SO-ARM101 config's breathing (idle) and talking (speaking) programs.

Reads the operator's trusted catalog and robot file, derives the neutral pose and
breathing direction from the programs already there, authors smooth replacements
(see ``conversation_soarm.author_presence_programs``), checks speed and
acceleration against the catalog policy, and rewrites the catalog (programs and
pinned revisions) and the robot file (revisions and durations). Backups are saved
as ``*.before-presence``. Nothing here moves a robot; restart the ensemble after.

    python scripts/author-soarm-presence.py CONFIG_DIR [--breath-period 4.0]
        [--talk-amplitude-deg 5.0] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from forge_puppeteer.conversation_soarm import author_presence_programs, program_limits


def _program(catalog: dict, name: str) -> dict:
    return next(p for p in catalog["programs"] if p["name"] == name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("config_dir", type=Path)
    parser.add_argument("--catalog", default="catalog.json")
    parser.add_argument("--robot", default="robot.json")
    parser.add_argument("--breath-period", type=float, default=4.0)
    parser.add_argument("--talk-amplitude-deg", type=float, default=5.0)
    parser.add_argument("--talk-period", type=float, default=1.6)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    catalog_path, robot_path = args.config_dir / args.catalog, args.config_dir / args.robot
    catalog = json.loads(catalog_path.read_text())
    robot = json.loads(robot_path.read_text())
    policy = catalog["policy"]

    # Neutral is where a hold program stays; breathing direction is the old
    # idle program's largest excursion from it.
    neutral = tuple(_program(catalog, "listening")["frames"][0]["joints_rad"])
    old_idle = _program(catalog, "idle")["frames"]
    peak = max(old_idle, key=lambda f: max(abs(a - b) for a, b in zip(f["joints_rad"], neutral)))
    delta = tuple(a - b for a, b in zip(peak["joints_rad"], neutral))

    programs = author_presence_programs(
        policy["robot_id"], policy["calibration_id"], neutral, breath_delta_rad=delta,
        breath_period_s=args.breath_period, talk_amplitude_deg=args.talk_amplitude_deg,
        talk_period_s=args.talk_period)
    for name, program in programs.items():
        speed, accel = program_limits(program)
        print(f"{name}: {program.frames[-1].at_s:.2f} s, {len(program.frames)} frames, "
              f"peak {speed:.1f} deg/s, {accel:.0f} deg/s^2, {program.revision}")
        if speed > policy["max_speed_deg_s"] or accel > policy["max_acceleration_deg_s2"]:
            print(f"{name} exceeds the catalog policy limits", file=sys.stderr)
            return 1
    if args.dry_run:
        return 0

    old = {n: _program(catalog, n)["revision"] for n in programs}
    for backup_source in (catalog_path, robot_path):
        shutil.copy2(backup_source, backup_source.with_name(backup_source.name + ".before-presence"))
    for name, program in programs.items():
        entry = _program(catalog, name)
        entry["frames"] = [{"at_s": f.at_s, "joints_rad": list(f.joints_rad)} for f in program.frames]
        entry["revision"] = program.revision
    policy["allowed_revisions"] = [
        r for r in policy["allowed_revisions"] if r not in set(old.values())
    ] + [p.revision for p in programs.values()]
    catalog_path.write_text(json.dumps(catalog, indent=2) + "\n")

    provider = robot["provider"]
    for name, program in programs.items():
        provider["choreography_revisions"][name] = program.revision
        provider["choreography_durations_s"][name] = round(program.frames[-1].at_s, 3)
    robot_path.write_text(json.dumps(robot, indent=2) + "\n")
    print(f"updated {catalog_path.name} and {robot_path.name}; restart the ensemble")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
