#!/usr/bin/env python3
"""Add live presence (breathing, listening, audio-synced talking) to an SO-ARM101 config.

Pins one SDK ``JointEnvelope`` in the operator's trusted catalog and adds the matching
``live_presence`` block to the robot file. The envelope's centre is the pose the arm wakes to
(``startup_target``); its range is ``--range-deg`` per joint (the shoulder pan stays locked).
The breathing direction is taken from the old ``idle`` program, scaled to ``--breath-deg``. Backups are saved as
``*.before-live``. Nothing here moves a robot; restart the ensemble after.

    python scripts/author-soarm-live.py CONFIG_DIR [--range-deg 6] [--talk-amplitude-deg 5]
        [--speech-driven] [--dry-run]

Run it with the same Python environment the SDK daemon uses (it imports the SDK).
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("config_dir", type=Path)
    parser.add_argument("--catalog", default="catalog.json")
    parser.add_argument("--robot", default="robot.json")
    parser.add_argument("--name", default="presence")
    parser.add_argument("--range-deg", type=float, default=6.0)
    parser.add_argument("--talk-amplitude-deg", type=float, default=5.0)
    parser.add_argument("--breath-period", type=float, default=4.0)
    parser.add_argument("--breath-deg", type=float, default=4.0,
                        help="largest joint excursion of one breath (the old idle direction, scaled)")
    parser.add_argument("--speech-driven", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    from soarm101_motion.live_stream import JointEnvelope

    catalog_path, robot_path = args.config_dir / args.catalog, args.config_dir / args.robot
    catalog = json.loads(catalog_path.read_text())
    robot = json.loads(robot_path.read_text())
    policy = catalog["policy"]
    center = tuple(catalog["startup_target"]["joints_rad"])
    idle = next(p for p in catalog["programs"] if p["name"] == "idle")["frames"]
    peak = max(idle, key=lambda f: max(abs(a - b) for a, b in zip(f["joints_rad"], center)))
    breath_deg = [math.degrees(a - b) for a, b in zip(peak["joints_rad"], center)]
    breath_deg[0] = 0.0
    scale = args.breath_deg / max(abs(v) for v in breath_deg)
    breath_deg = [v * scale for v in breath_deg]
    range_deg = [0.0] + [args.range_deg] * 4

    envelope = JointEnvelope.author(
        args.name, policy["robot_id"], policy["calibration_id"], center,
        tuple(math.radians(v) for v in range_deg))
    live = {
        "envelope": args.name, "envelope_revision": envelope.revision,
        "breath_delta_deg": [round(v, 3) for v in breath_deg],
        "max_offset_deg": range_deg, "breath_period_s": args.breath_period,
        "talk_amplitude_deg": args.talk_amplitude_deg, "speech_driven": args.speech_driven,
    }
    # Validate against Puppeteer's own model before writing anything.
    from forge_puppeteer.ensemble.config import LivePresenceConfig
    try:
        LivePresenceConfig.model_validate(live)
    except ValueError as exc:
        print(f"live presence does not fit the envelope: {exc}", file=sys.stderr)
        return 1
    print(f"envelope {args.name}: centre = wake pose, range ±{args.range_deg:g} deg, {envelope.revision}")
    print(f"breathing delta deg: {live['breath_delta_deg']}, talking ±{args.talk_amplitude_deg:g} deg")
    if args.dry_run:
        return 0

    for source in (catalog_path, robot_path):
        shutil.copy2(source, source.with_name(source.name + ".before-live"))
    catalog["envelopes"] = [e for e in catalog.get("envelopes", []) if e["name"] != args.name]
    previous = {e["revision"] for e in catalog.get("envelopes", [])}
    catalog["envelopes"].append({
        "name": args.name, "robot_id": envelope.robot_id, "calibration_id": envelope.calibration_id,
        "revision": envelope.revision, "center_rad": list(center),
        "max_offset_rad": list(envelope.max_offset_rad)})
    policy["allowed_revisions"] = [r for r in policy["allowed_revisions"] if r not in previous] + [envelope.revision]
    catalog_path.write_text(json.dumps(catalog, indent=2) + "\n")
    robot["provider"]["live_presence"] = live
    robot_path.write_text(json.dumps(robot, indent=2) + "\n")
    print(f"updated {catalog_path.name} and {robot_path.name}; restart the ensemble")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
