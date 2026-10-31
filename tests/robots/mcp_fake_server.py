"""Software-only subprocess fixture for MCP wire tests."""

import anyio

from forge_puppeteer.execution import (
    ActionRequest,
    Completion,
    ExecutionRuntime,
    StopResult,
)
from forge_puppeteer.mcp import create_server
from forge_puppeteer.robot_contracts import AssetDescriptor, DeviceDescriptor
from forge_puppeteer.runtime import PerformanceStage


class Fake:
    allowed = True

    async def permitted(self, tool):
        return self.allowed or tool in {
            "device_stop",
            "action_lookup",
            "test_permission",
        }

    async def describe(self):
        return DeviceDescriptor(id="fake", provider="fake", connected=True, ready=True)

    async def state(self):
        return {"feedback": "commanded"}

    async def list_assets(self):
        return tuple(
            AssetDescriptor(
                id=f"fake:{i}", kind="pose", source="fixture", revision="sha256:fixed"
            )
            for i in range(3)
        )

    async def validate(self, request):
        if not self.allowed or request.operation not in {"head", "play"}:
            raise ValueError("denied")
        if request.operation == "play" and (
            request.asset_id != "fake:0" or request.asset_revision != "sha256:fixed"
        ):
            raise ValueError("invalid asset")

    def resources_for(self, request):
        return ("head",)

    async def execute(self, request):
        await anyio.sleep_forever()
        return Completion("finished")

    async def stop(self):
        return StopResult(True, "fake stopped")


def main():
    fake = Fake()
    runtime = ExecutionRuntime(PerformanceStage())
    runtime.register("fake", fake)

    def register(server, gateway):
        @server.tool()
        async def head_move(
            action_id: str, caller_id: str, session_id: str, angle: float
        ):
            return await gateway.submit(
                "head_move",
                ActionRequest(
                    id=action_id,
                    performer_id="fake",
                    caller_id=caller_id,
                    session_id=session_id,
                    operation="head",
                    parameters={"angle": angle},
                ),
            )

        @server.tool()
        async def play_asset(
            action_id: str,
            caller_id: str,
            session_id: str,
            asset_id: str,
            revision: str,
        ):
            return await gateway.submit(
                "play_asset",
                ActionRequest(
                    id=action_id,
                    performer_id="fake",
                    caller_id=caller_id,
                    session_id=session_id,
                    operation="play",
                    asset_id=asset_id,
                    asset_revision=revision,
                ),
            )

        @server.tool()
        async def test_permission(allowed: bool):
            await gateway.check("test_permission")
            fake.allowed = allowed
            return {"allowed": allowed}

    create_server(runtime, fake, "fake", register).run(transport="stdio")


if __name__ == "__main__":
    main()
