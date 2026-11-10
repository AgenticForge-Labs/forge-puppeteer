#!/usr/bin/env python3
"""Operator control of the startup gate, park and recovery, over the ensemble's MCP tools.

Starts one ``forge-puppeteer-ensemble`` for the given config (so it is the only
controller, per AGENTS.md), makes the one call, prints the JSON result and exits.
Do not run it while another ensemble or Conversationalist is using the same robots.
Starting it moves nothing: motion happens only for ``move-to-sleep`` and ``park``.

    python scripts/ensemble-ctl.py CONFIG check
    python scripts/ensemble-ctl.py CONFIG move-to-sleep PERFORMER
    python scripts/ensemble-ctl.py CONFIG park
    python scripts/ensemble-ctl.py CONFIG recover PERFORMER
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import uuid
from pathlib import Path


async def run(config: Path, command: str, performer: str | None) -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    launcher = shutil.which("forge-puppeteer-ensemble") or str(
        Path(sys.executable).with_name("forge-puppeteer-ensemble"))
    params = StdioServerParameters(command=launcher, args=["--config", str(config)], env={})
    action_id = f"ctl-{uuid.uuid4().hex}"
    async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
        await session.initialize()
        if command == "check":
            tool, args = "ensemble_startup_check", {}
        elif command == "move-to-sleep":
            tool, args = "ensemble_prepare", {
                "performer_id": performer, "action": "move_to_sleep", "action_id": action_id}
        elif command == "park":
            tool, args = "ensemble_park", {"action_id": action_id}
        else:
            tool, args = "ensemble_recover", {"performer_id": performer}
        response = await session.call_tool(tool, args)
        if response.is_error:
            return {"outcome": "error", "tool": tool}
        return response.structured_content or {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("config", type=Path, help="absolute path to the schema-v4 ensemble file")
    parser.add_argument("command", choices=["check", "move-to-sleep", "park", "recover"])
    parser.add_argument("performer", nargs="?", help="robot id (move-to-sleep, recover)")
    args = parser.parse_args(argv)
    if args.command in {"move-to-sleep", "recover"} and not args.performer:
        parser.error(f"{args.command} needs a robot id")
    result = asyncio.run(run(args.config.resolve(), args.command, args.performer))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("outcome") not in {"error", "stopped_on_fault", "failed", "denied"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
