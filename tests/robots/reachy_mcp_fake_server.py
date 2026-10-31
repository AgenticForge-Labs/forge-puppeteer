"""Fake Reachy SDK behind the packaged MCP server and shared runtime."""

import json
import sys
from pathlib import Path

from forge_puppeteer.devices.reachy import ReachyCatalog, ReachyPlaybackProvider
from forge_puppeteer.execution import ExecutionRuntime
from forge_puppeteer.mcp.reachy import register_reachy_tools
from forge_puppeteer.mcp.server import create_server
from forge_puppeteer.runtime import PerformanceStage


class Move:
    def __init__(self, data, sound_path=None):
        self.description, self.duration, self.sound_path = data["description"], data["duration"], sound_path


class Robot:
    def __init__(self, fail_playback=False):
        self.calls = []
        self.fail_playback = fail_playback

    async def async_play_move(self, move, *, sound=False):
        self.calls.append(("play", move.description, sound))
        if self.fail_playback:
            raise RuntimeError("fake SDK lost playback outcome")

    def goto_target(self, **kwargs):
        self.calls.append(("goto", kwargs))

    def cancel_move(self):
        self.calls.append(("cancel",))


def main():
    root = Path(sys.argv[1])
    manifest = json.loads((root / "manifest.json").read_text())
    catalog = ReachyCatalog(root / "assets", manifest, move_factory=Move)
    authorized = len(sys.argv) < 3 or sys.argv[2] == "authorized"
    provider = ReachyPlaybackProvider(catalog, Robot(fail_playback=len(sys.argv) > 3 and sys.argv[3] == "fail"), motion_authorized=authorized)
    runtime = ExecutionRuntime(PerformanceStage())
    runtime.register("reachy", provider)
    allowed = frozenset({"device_info", "device_state", "list_assets", "action_lookup",
                         "action_cancel", "device_stop", "play_move", "head_pose", "antenna_targets"})
    create_server(runtime, provider, "reachy", register_reachy_tools,
                  enabled_tools=allowed).run(transport="stdio")


if __name__ == "__main__":
    main()
