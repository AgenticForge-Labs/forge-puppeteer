"""Create an exact-revision sequence fixture for the live SDK simulator process test."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from soarm101_motion.asset_catalog import discover_assets
from soarm101_motion.poses import PoseLibrary
from soarm101_motion.sequences import MotionSequence, SequenceLibrary, SequenceStep
from soarm101_motion.trajectories import TrajectoryLibrary

root = Path(sys.argv[1])
root.mkdir(parents=True, exist_ok=True)
poses = root / "poses.json"
sequences_path = root / "sequences.json"
trajectory_root = root / "recordings"
trajectory_root.mkdir(exist_ok=True)
poses.write_text('{"schema_version":1,"robot_id":"so101","poses":{}}\n')
sequence_library = SequenceLibrary("so101", path=sequences_path)
sequence_library.save(MotionSequence(
    "stdio-e2e", (SequenceStep("gripper", {"position": 0.5}),
                 SequenceStep("wait", {"seconds": 0.01})),
    metadata={"source_robot_id": "so101", "source_calibration_id": "simulation"},
))
descriptors = discover_assets(
    PoseLibrary("so101", path=poses),
    TrajectoryLibrary("so101", root=trajectory_root),
    SequenceLibrary("so101", path=sequences_path),
)
asset = next(item for item in descriptors if item.id == "soarm101/sequence/stdio-e2e")
policy = {
    "schema_version": 1,
    "robot_id": "so101",
    "calibration_id": "simulation",
    "allowed_assets": {asset.id: asset.revision},
    "max_speed_scale": 1.0,
    "max_steps": 4,
    "max_duration_s": 2.0,
    "max_start_error_rad": 0.05,
    "max_start_gripper_error": 0.05,
}
(root / "replay-policy.json").write_text(json.dumps(policy, sort_keys=True) + "\n")
