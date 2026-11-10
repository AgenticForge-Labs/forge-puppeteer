"""Real MCP transport for the sleep-pose startup gate with a fake arm.

argv[1]: log file; argv[2]: starting offset in degrees from the sleep pose.
"""

import sys
from pathlib import Path

import anyio

from forge_puppeteer.behavior_contract import BehaviorCue, BehaviorPattern, PerformerBehaviors
from forge_puppeteer.conversation_ensemble import ConversationEnsemble
from forge_puppeteer.execution import Completion, ExecutionRuntime, StopResult
from forge_puppeteer.mcp.ensemble import create_ensemble_server
from forge_puppeteer.runtime import PerformanceStage
from forge_puppeteer.standard_poses import MeasuredPose, load_standard_poses
from forge_puppeteer.startup_gate import StartupGate

POSES = load_standard_poses("soarm101", override_dir=None)


class Arm:
    def __init__(self, offset):
        self.offset = offset

    async def read_pose(self):
        return MeasuredPose({k: v + self.offset for k, v in POSES.pose("sleep").channels.items()})

    async def validate(self, request):
        pass

    def resources_for(self, request):
        return ("motion",)

    async def execute(self, request):
        async with await anyio.open_file(Path(sys.argv[1]), "a", encoding="utf-8") as log:
            await log.write(request.operation + "\n")
        if request.operation == "go_sleep":
            self.offset = 0.0
        return Completion("fake")

    async def stop(self):
        return StopResult(True, "fake stop")


def main():
    hold = BehaviorPattern(source="procedural", mode="hold", cues=())
    pack = PerformerBehaviors.author("arm", {"idle": hold, "listening": hold, "speaking": hold})
    runtime = ExecutionRuntime(PerformanceStage())
    arm = Arm(float(sys.argv[2]))
    runtime.register("arm", arm)
    ensemble = ConversationEnsemble(runtime, [pack])
    gate = StartupGate(runtime, ensemble, {"arm": arm}, {"arm": POSES},
                       {"arm": BehaviorCue(at_s=0, operation="go_sleep")})
    create_ensemble_server(runtime, ensemble, motion_enabled=True, startup_gate=gate,
                           stackchan_performer_id=None).run(transport="stdio")


if __name__ == "__main__":
    main()
