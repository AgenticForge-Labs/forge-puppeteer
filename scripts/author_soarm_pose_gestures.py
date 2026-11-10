#!/usr/bin/env python3
"""Import reviewed SO-ARM pose data and export candidate launcher programs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from forge_puppeteer.soarm_pose_assets import (
    GestureTiming,
    YesTiming,
    author_programs_from_assets,
    export_launcher_programs,
    export_v4_candidates,
    import_pose_library,
    validate_pose_assets,
    write_pose_assets,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pose-library", type=Path, required=True)
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--calibration-id", required=True)
    parser.add_argument("--asset-output", type=Path, required=True)
    parser.add_argument("--asset-revision",
                        help="expected SHA-256 revision (omit to verify the file's self revision)")
    parser.add_argument("--launcher-template", type=Path,
                        help="operator config JSON containing a complete soarm101 section")
    parser.add_argument("--launcher-output", type=Path)
    parser.add_argument("--v4-catalog-template", type=Path)
    parser.add_argument("--v4-robot-template", type=Path)
    parser.add_argument("--v4-room-template", type=Path)
    parser.add_argument("--v4-output-dir", type=Path)
    parser.add_argument("--breathe-cycle-seconds", type=float, default=5.0)
    parser.add_argument("--breathe-rise-fraction", type=float, default=0.6)
    parser.add_argument("--breathe-loops", type=int, default=3)
    parser.add_argument("--breathe-entry-seconds", type=float, default=2.0)
    parser.add_argument("--breathe-return-seconds", type=float, default=2.0)
    parser.add_argument("--yes-cycle-seconds", type=float, default=1.0,
                        help="full up→down→up cycle duration")
    parser.add_argument("--yes-loops", type=int, default=2)
    parser.add_argument("--yes-entry-seconds", type=float, default=1.0)
    parser.add_argument("--yes-return-seconds", type=float, default=1.0)
    args = parser.parse_args()

    v4 = (args.v4_catalog_template, args.v4_robot_template, args.v4_room_template, args.v4_output_dir)
    if any(v4) and not all(v4):
        parser.error("all four v4 template/output options are required")
    if bool(args.launcher_template) != bool(args.launcher_output) or not (args.launcher_template or all(v4)):
        parser.error("supply a legacy template/output pair and/or all v4 options")
    inputs = tuple(x for x in (args.pose_library, args.launcher_template, *v4[:3]) if x)
    outputs = (args.asset_output,) + ((args.launcher_output,) if args.launcher_output else ()) + ((args.v4_output_dir / "catalog.json", args.v4_output_dir / "robot.json", args.v4_output_dir / "room.json") if all(v4) else ())
    if any(output.resolve() in {source.resolve() for source in inputs} for output in outputs):
        parser.error("output paths must not overwrite the pose library or launcher template")
    if len({p.resolve() for p in outputs}) != len(outputs):
        parser.error("asset and launcher outputs must be different files")

    payload = import_pose_library(args.pose_library, robot_id=args.robot_id,
                                  calibration_id=args.calibration_id)
    write_pose_assets(payload, args.asset_output)
    # Re-read and verify the exported copy before extracting any joint values.
    payload = json.loads(args.asset_output.read_text(encoding="utf-8"))
    revision = args.asset_revision or payload.get("content_revision")
    validate_pose_assets(payload, robot_id=args.robot_id,
                         calibration_id=args.calibration_id,
                         expected_content_revision=revision)
    programs = author_programs_from_assets(
        payload, robot_id=args.robot_id, calibration_id=args.calibration_id,
        expected_content_revision=revision,
        breathe_timing=GestureTiming(
            duration_s=args.breathe_cycle_seconds, rise_fraction=args.breathe_rise_fraction,
            loops=args.breathe_loops, entry_s=args.breathe_entry_seconds,
            return_s=args.breathe_return_seconds,
        ),
        yes_timing=YesTiming(
            cycle_seconds=args.yes_cycle_seconds, loops=args.yes_loops,
            entry_s=args.yes_entry_seconds, return_s=args.yes_return_seconds,
        ),
    )
    if args.launcher_template:
        template = json.loads(args.launcher_template.read_text(encoding="utf-8"))
        output = export_launcher_programs(programs, template)
        args.launcher_output.parent.mkdir(parents=True, exist_ok=True)
        args.launcher_output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    if all(v4):
        templates = [json.loads(path.read_text(encoding="utf-8")) for path in v4[:3]]
        candidates = export_v4_candidates(programs, catalog_template=templates[0],
            robot_template=templates[1], room_template=templates[2],
            catalog_file="catalog.json", robot_file="robot.json")
        args.v4_output_dir.mkdir(parents=True, exist_ok=True)
        for name, candidate in zip(("catalog.json", "robot.json", "room.json"), candidates):
            (args.v4_output_dir / name).write_text(json.dumps(candidate, indent=2) + "\n", encoding="utf-8")
        print(f"V4 review candidates: {args.v4_output_dir}")
        if any(p["frames"][-1]["at_s"] > candidates[0]["policy"].get("max_duration_s", 0)
               for p in candidates[0]["programs"]):
            print("Candidate duration exceeds catalog policy; operator review of policy and timing is required.")
    print(f"Imported pose asset revision: {payload['content_revision']}")
    print("Review-only candidates; physical authorization and path qualification remain separate.")


if __name__ == "__main__":
    main()
