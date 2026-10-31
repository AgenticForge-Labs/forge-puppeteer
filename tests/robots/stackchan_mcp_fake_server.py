"""Fake StackChan USB facade behind a real local MCP stdio server."""

import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import anyio

from forge_puppeteer.devices.stackchan import StackChanProvider
from forge_puppeteer.execution import ExecutionRuntime
from forge_puppeteer.mcp.server import create_server
from forge_puppeteer.mcp.stackchan import register_stackchan_tools
from forge_puppeteer.runtime import PerformanceStage


class FakeClient:
    def close(self):
        pass


class FakeRobot:
    def __init__(self):
        self.generation = 0
        self.event = threading.Event()

    def _generation(self):
        return self.generation, self.event

    def _generation(self):
        return self.generation, self.event

    def discover(self):
        return {
            "protocol_version": "1.0",
            "capabilities": [
                "system.state",
                "system.stop",
                "avatar.update",
                "motion.update.position_only",
                "lights.update",
                "external_lights.update",
            ],
        }

    def state(self):
        return {"native_angle_limits_tenths": {"yaw": [-500, 500], "pitch": [30, 870]}}

    def _apply(self, _update, *, _generation=None):
        if _generation != self.generation:
            raise RuntimeError("invalidated by stop")
        return (SimpleNamespace(event="completed"),)

    update_face = _apply
    move_head = _apply
    update_lights = _apply

    def update_neohex(self, frame):
        assert len(frame.pixels) == 37
        return (SimpleNamespace(event="completed"),)

    def stop(self):
        self.generation += 1
        self.event.set()
        self.event = threading.Event()
        return (SimpleNamespace(event="completed"),)


def main():
    allowed = frozenset(
        {
            "device_info",
            "device_state",
            "list_assets",
            "head_move",
            "face_update",
            "lights_update",
            "neohex_update",
            "play_asset",
            "device_stop",
            "action_lookup",
            "action_cancel",
        }
    )
    fake = FakeRobot()
    provider = StackChanProvider(
        "stack",
        asset_directory=Path(sys.argv[1]),
        connector=lambda *a, **kw: (FakeClient(), fake),
        allowed_tools=allowed,
    )
    anyio.run(provider.connect, "fake")
    runtime = ExecutionRuntime(PerformanceStage())
    runtime.register("stack", provider)
    create_server(
        runtime, provider, "stack", register_stackchan_tools, enabled_tools=allowed
    ).run(transport="stdio")


if __name__ == "__main__":
    main()
