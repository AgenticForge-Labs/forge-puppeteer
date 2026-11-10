"""Real MCP transport over a fake Reachy SDK; never opens hardware."""

import sys
from pathlib import Path

import numpy as np

from forge_puppeteer.behavior_contract import (
    BehaviorCue,
    BehaviorPattern,
    PerformerBehaviors,
)
from forge_puppeteer.conversation_ensemble import ConversationEnsemble
from forge_puppeteer.devices.reachy import ReachyPlaybackProvider
from forge_puppeteer.ensemble.options import _original_idle_pack
from forge_puppeteer.ensemble.server import create_ensemble_server
from forge_puppeteer.execution import ExecutionRuntime
from forge_puppeteer.runtime import PerformanceStage


class FakeCatalog:
    def __init__(self):
        self.manifest = {"sdk_version": "1.10.0rc5"}


class FakeRobot:
    def get_current_head_pose(self):
        return np.eye(4)

    def get_present_antenna_joint_positions(self):
        return [0.0, 0.0]

    def set_target(self, **target):
        assert set(target) == {"head", "antennas", "body_yaw"}
        with Path(sys.argv[1]).open("a", encoding="utf-8") as log:
            rotated = not np.allclose(target["head"][:3, :3], np.eye(3))
            log.write("speech\n" if rotated else "idle\n")


def main():
    idle = BehaviorPattern(source="procedural", mode="loop", period_s=1,
                           cues=(BehaviorCue(at_s=0, operation="unused"),))
    pack = _original_idle_pack(PerformerBehaviors.author("reachy", {"idle": idle}))
    runtime = ExecutionRuntime(PerformanceStage())
    runtime.register("reachy", ReachyPlaybackProvider(
        FakeCatalog(), FakeRobot(), motion_authorized=True, original_idle_authorized=True,
    ))
    ensemble = ConversationEnsemble(runtime, [pack])
    create_ensemble_server(runtime, ensemble, motion_enabled=True).run(transport="stdio")


if __name__ == "__main__":
    main()
