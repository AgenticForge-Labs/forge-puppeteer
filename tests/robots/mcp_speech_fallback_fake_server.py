"""Real MCP transport exercising the opt-in head-only fallback with a fake device."""

import sys
from pathlib import Path

import anyio

from forge_puppeteer.behavior_contract import (
    BehaviorCue,
    BehaviorPattern,
    PerformerBehaviors,
)
from forge_puppeteer.conversation_ensemble import ConversationEnsemble
from forge_puppeteer.ensemble.server import create_ensemble_server
from forge_puppeteer.execution import Completion, ExecutionRuntime, StopResult
from forge_puppeteer.runtime import PerformanceStage


class Fake:
    async def validate(self, request):
        if request.operation != "head_pose":
            raise ValueError("head-only fake")

    def resources_for(self, request):
        return ("head.pose",)

    async def execute(self, request):
        async with await anyio.open_file(Path(sys.argv[1]), "a", encoding="utf-8") as log:
            await log.write(request.cue_id + "\n")
        return Completion("fake head action returned")

    async def stop(self):
        return StopResult(True, "fake stop")


def main():
    fallback = BehaviorPattern(source="procedural", mode="loop", period_s=0.4,
                               cues=(BehaviorCue(at_s=0, operation="head_pose"),
                                     BehaviorCue(at_s=0.2, operation="head_pose")))
    pack = PerformerBehaviors.author("reachy", {"speaking": fallback})
    runtime = ExecutionRuntime(PerformanceStage())
    runtime.register("reachy", Fake())
    ensemble = ConversationEnsemble(runtime, [pack])
    identity = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    create_ensemble_server(runtime, ensemble, motion_enabled=True,
                           speech_driven_performers=("reachy",),
                           speech_neutral_heads={"reachy": identity},
                           speech_fallbacks={"reachy": fallback}).run(transport="stdio")


if __name__ == "__main__":
    main()
