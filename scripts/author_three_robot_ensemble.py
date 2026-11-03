"""Create review-only two- or three-robot ensemble configurations.

Does not contact hardware, run SDK motion, arm a device, grant motion, open a
port, or approve any generated path. Fill all operator gates only after review.
Physical mode must be configured separately and deliberately.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def author_config(
    *,
    reachy_manifest: Path,
    reachy_recordings: Path,
    reachy_neutral_file: Path,
    soarm_neutral_file: Path | None = None,
    stackchan_port: str,
    action_journal: Path,
    ownership_db: Path,
    reachy_stackchan_only: bool = False,
) -> dict[str, object]:
    programs = {}
    if not reachy_stackchan_only:
        if soarm_neutral_file is None:
            raise ValueError("three-robot authoring requires --soarm-neutral")
        neutral = json.loads(soarm_neutral_file.read_text(encoding="utf-8"))
        if not isinstance(neutral, list):
            raise TypeError("SO-ARM neutral must be exactly five joint radians as a JSON list")
        from forge_puppeteer.conversation_soarm import author_soarm_reference_programs

        programs = author_soarm_reference_programs(
            robot_id="so101", calibration_id="simulation", neutral_joints_rad=neutral
        )
    config = {
        "schema_version": 3 if reachy_stackchan_only else 1,
        "motion_authority": "REVIEW_REQUIRED_NO_MOTION_AUTHORITY",
        "reachy_neutral_head": str(reachy_neutral_file.absolute()),
        "reachy": {
            "backend": "reachy_sdk", "device": "reachy",
            "instance_id": "reachy", "physical_authority": "REVIEW_REQUIRED",
            "allowed_tools": ["head_pose", "play_move", "device_stop"],
            "asset_root": str(reachy_recordings.absolute()),
            "manifest": str(reachy_manifest.absolute()),
            "sdk_version": "1.10.0rc5",
            "host": "127.0.0.1", "daemon_port": 8000,
            "connection_mode": "localhost_only",
            "daemon": {
                "command": ["REVIEW_REQUIRED_REACHY_DAEMON_LAUNCHER"],
                "startup_timeout_s": 45,
                "persist_on_close": True,
            },
            "expected_daemon_hardware_id": "REVIEW_REQUIRED_READ_FROM_DAEMON",
        },
        "stackchan": {
            "backend": "stackchan_sdk", "device": "stackchan",
            "instance_id": "stackchan",
            "physical_authority": "REVIEW_REQUIRED",
            "allowed_tools": ["head_move", "face_update", "device_stop"],
            "port": stackchan_port, "sdk_version": "0.3.0",
        },
        "soarm101": {
            "instance_id": "soarm101", "robot_id": "so101",
            "calibration_id": "simulation", "mode": "simulation",
            "programs": [
                {
                    "name": name, "revision": program.revision,
                    "frames": [
                        {"at_s": frame.at_s, "joints_rad": list(frame.joints_rad)}
                        for frame in program.frames
                    ],
                } for name, program in sorted(programs.items())
            ],
            "approved_revisions": [
                program.revision for _, program in sorted(programs.items())
            ],
        },
        "action_journal": str(action_journal.absolute()),
        "ownership_db": str(ownership_db.absolute()),
    }
    if reachy_stackchan_only:
        # No simulated SO-ARM profile, runtime, watchdog or program is kept.
        config.pop("soarm101")
        config["stackchan"]["sdk_version"] = "0.4.0"
    return config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reachy-manifest", type=Path, required=True)
    parser.add_argument("--reachy-recordings", type=Path, required=True)
    parser.add_argument("--reachy-neutral", type=Path, required=True)
    parser.add_argument("--soarm-neutral", type=Path)
    parser.add_argument("--reachy-stackchan-only", action="store_true",
                        help="author v3 two-robot config without SO-ARM simulation")
    parser.add_argument("--stackchan-port", required=True)
    parser.add_argument("--action-journal", type=Path, required=True)
    parser.add_argument("--ownership-db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.reachy_stackchan_only and args.soarm_neutral is None:
        parser.error("--soarm-neutral required for three-robot config")
    config = author_config(
        reachy_manifest=args.reachy_manifest,
        reachy_recordings=args.reachy_recordings,
        reachy_neutral_file=args.reachy_neutral,
        soarm_neutral_file=args.soarm_neutral,
        stackchan_port=args.stackchan_port,
        action_journal=args.action_journal,
        ownership_db=args.ownership_db,
        reachy_stackchan_only=args.reachy_stackchan_only,
    )
    if args.output.exists():
        parser.error("will not overwrite an existing review or approval document")
    args.output.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    if args.reachy_stackchan_only:
        print("Authored Reachy+StackChan review candidate; no SO-ARM or simulator.")
    else:
        print(f"Authored {len(config['soarm101']['programs'])} candidate SDK programs.")
    print("Review the full paths and replace REVIEW_REQUIRED fields explicitly.")
    print("No robot has been connected or authorized by this script.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
