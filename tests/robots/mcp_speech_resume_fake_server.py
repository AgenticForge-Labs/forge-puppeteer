"""Real MCP transport with fake devices for startup-to-speech regression."""

import sys
from pathlib import Path

import anyio

from forge_puppeteer.behavior_contract import (
    BehaviorCue,
    BehaviorPattern,
    PerformerBehaviors,
)
from forge_puppeteer.conversation_ensemble import ConversationEnsemble
from forge_puppeteer.execution import Completion, ExecutionRuntime, StopResult
from forge_puppeteer.mcp.ensemble import create_ensemble_server
from forge_puppeteer.runtime import PerformanceStage


class Fake:
    async def validate(self, request):
        if request.operation not in {"startup", "authored_speaking", "head_pose"}:
            raise ValueError("unsupported fake operation")

    def resources_for(self, request):
        return ("head.pose",)

    async def execute(self, request):
        async with await anyio.open_file(Path(sys.argv[1]), "a", encoding="utf-8") as log:
            await log.write(request.operation + "\n")
        return Completion("fake action returned")

    async def stop(self):
        return StopResult(True, "fake stop")


def main():
    startup = BehaviorPattern(source="procedural", mode="once",
                              cues=(BehaviorCue(at_s=0, operation="startup"),))
    speaking = BehaviorPattern(source="procedural", mode="loop", period_s=1,
                               cues=(BehaviorCue(at_s=0, operation="authored_speaking"),))
    pack = PerformerBehaviors.author("reachy", {"speaking": speaking}, startup=startup)
    runtime = ExecutionRuntime(PerformanceStage())
    runtime.register("reachy", Fake())
    ensemble = ConversationEnsemble(runtime, [pack])
    identity = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    create_ensemble_server(runtime, ensemble, motion_enabled=True,
                           speech_driven_performers=("reachy",),
                           speech_neutral_heads={"reachy": identity},
                           speech_dynamics={"reachy": "smoothed_v1"}).run(transport="stdio")


if __name__ == "__main__":
    main()
