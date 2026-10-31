"""Fake broker behind the packaged SO-ARM provider's real MCP stdio facade."""

import sys
from types import SimpleNamespace

from forge_puppeteer.devices.soarm101 import SOARM101Provider
from forge_puppeteer.execution import ExecutionRuntime
from forge_puppeteer.mcp import create_server, register_soarm101_tools
from forge_puppeteer.runtime import PerformanceStage


class FakeBroker:
    def __init__(self):
        self.visible = {
            "robot_capabilities", "robot_state", "list_assets", "go_pose",
            "jog_cartesian", "sleep", "stop",
        }
        self.stop_calls = 0
        self.fail_next_action = len(sys.argv) > 1 and sys.argv[1] == "failure-recovery"
        self.failure_recovery = self.fail_next_action

    async def list_tools(self):
        return SimpleNamespace(tools=[SimpleNamespace(name=n) for n in self.visible])

    async def call_tool(self, name, arguments):
        if name == "robot_capabilities":
            result = {"authority": {"motion": "active"}, "capabilities": ["go_pose"]}
        elif name == "robot_state":
            result = {"state": "ready"}
        elif name == "list_assets":
            result = {"items": [], "offset": arguments.get("offset", 0), "next_offset": None}
        elif name == "stop":
            self.stop_calls += 1
            if self.failure_recovery and self.stop_calls == 1:
                # A terminal command status alone does not prove the arm holds.
                result = {"status": "completed"}
            else:
                result = {"completed": True, "holding": True}
        else:
            if name == "go_pose" and self.fail_next_action:
                self.fail_next_action = False
                result = {"accepted": True, "completed": False, "status": "running"}
            else:
                result = {"accepted": True, "completed": True}
            if name == "go_pose":
                self.visible.discard("jog_cartesian")
        return SimpleNamespace(is_error=False, structured_content={"result": result})


def main():
    broker = FakeBroker()
    provider = SOARM101Provider("arm", broker, allowed_tools=frozenset(broker.visible))
    runtime = ExecutionRuntime(PerformanceStage())
    runtime.register("arm", provider)
    enabled = frozenset({
        "device_info", "device_state", "list_assets", "action_lookup", "action_cancel",
        "device_stop", "go_pose", "jog_cartesian", "sleep",
    })
    create_server(
        runtime, provider, "arm", register_soarm101_tools, enabled_tools=enabled,
    ).run(transport="stdio")


if __name__ == "__main__":
    main()
