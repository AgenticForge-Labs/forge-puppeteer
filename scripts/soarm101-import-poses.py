#!/usr/bin/env python3
"""Build review-only SO-ARM101 choreography candidates from SDK saved poses."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from forge_puppeteer.conversation_soarm import import_taught_pose_candidate_file


def _mapping(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("gesture mapping must be NAME=POSE")
    name, pose = value.split("=", 1)
    if not name or not pose:
        raise argparse.ArgumentTypeError("gesture mapping must be NAME=POSE")
    return name, pose


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pose-library", required=True, help="SDK PoseLibrary JSON input")
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--calibration-id", required=True)
    parser.add_argument("--neutral-pose", required=True)
    parser.add_argument("--gesture", action="append", type=_mapping, required=True,
                        metavar="NAME=POSE", help="one-shot expression mapped to a saved pose")
    parser.add_argument("--segment-duration", type=float, default=1.2,
                        help="seconds per neutral-to-pose/pose-to-neutral segment")
    parser.add_argument("--output", default="runs/soarm101/choreography-candidates.json")
    args = parser.parse_args()

    output = Path(args.output).expanduser().resolve()
    repository = Path(__file__).resolve().parents[1]
    try:
        relative = output.relative_to(repository)
    except ValueError:
        pass
    else:
        if not relative.parts or relative.parts[0] != "runs":
            parser.error("output inside the repository must be under ignored runs/")

    document = import_taught_pose_candidate_file(
        args.pose_library,
        robot_id=args.robot_id,
        calibration_id=args.calibration_id,
        neutral_pose=args.neutral_pose,
        gesture_poses=dict(args.gesture),
        segment_duration_s=args.segment_duration,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote review-only candidates to {output}")
    print("No motion authority or approved revision pins were created.")


if __name__ == "__main__":
    main()
