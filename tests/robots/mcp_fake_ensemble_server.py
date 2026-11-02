"""Offline real-stdio MCP three-performer fixture, NO hardware authority."""

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
    def __init__(self):
        self.calls = []

    async def validate(self, request):
        if request.operation not in {"fake_idle", "fake_listening", "fake_speaking",
                                     "fake_yes", "fake_no"}:
            raise ValueError("unsupported fake operation")

    def resources_for(self, request):
        return ("fake.motion",)

    async def execute(self, request):
        self.calls.append(request.operation)
        return Completion("mock-only action")

    async def stop(self):
        return StopResult(True, "mock-only stop")


def pack(name):
    patterns = {
        state: BehaviorPattern(
            source="procedural", mode="loop", period_s=0.5,
            max_cycles=100, max_duration_s=30,
            cues=(BehaviorCue(at_s=0, operation=f"fake_{state}"),),
        ) for state in ("idle", "listening", "speaking")
    }
    for gesture in ("yes", "no"):
        patterns[gesture] = BehaviorPattern(
            source="sequence", mode="once",
            cues=(BehaviorCue(at_s=0, operation=f"fake_{gesture}"),),
        )
    return PerformerBehaviors.author(name, patterns)


def main():
    runtime = ExecutionRuntime(PerformanceStage())
    names = ("reachy", "stackchan", "soarm101")
    for name in names:
        runtime.register(name, Fake())
    ensemble = ConversationEnsemble(runtime, (pack(n) for n in names))
    create_ensemble_server(
        runtime, ensemble, name="Mock 3-Robot Room", motion_enabled=True,
    ).run(transport="stdio")


if __name__ == "__main__":
    main()
