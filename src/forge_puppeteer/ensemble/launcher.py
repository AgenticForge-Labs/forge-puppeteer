"""The ``forge-puppeteer-ensemble`` command: an operator-configured stdio MCP server.

One process owns a set of robots (one to three), one action journal and one
conversation at a time. It is not an LLM, audio player or Platform client. It reuses
the Reachy/StackChan SDK connections, an SDK-owned SO-ARM101 daemon (or an existing
broker), and Puppeteer's single ExecutionRuntime and ConversationEnsemble.

Real hardware needs ``"physical": true`` in the ensemble file. At startup each real
robot is compared with its standard sleep pose (see ``startup_gate``).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from contextlib import AsyncExitStack
from pathlib import Path

import anyio

from ..conversation_ensemble import ConversationEnsemble
from .build import (
    build_ensemble,
    build_startup_gate,
    start_owned_physical,
    start_owned_simulation,
)
from .config import load_config
from .server import create_ensemble_server


def config_readiness(path: str | Path) -> dict[str, object]:
    """Validate the operator config without importing device SDKs or connecting.

    This is deliberately a file/configuration check only. In particular, it
    does not infer that a configured port, lease, SDK daemon, or robot is live.
    """
    ensemble, _robots, _options = load_config(path)
    return {
        "configuration": "valid", "schema_version": 4,
        "performers": [robot.id for robot in ensemble.robots],
        "robot_kinds": [robot.kind for robot in ensemble.robots],
        "robot_files_resolved": True,
        "device_connections_attempted": False,
        "motion_authority_checked": False,
    }


def _start_daemons(ensemble_config, robots):
    """Start the SDK-owned SO-ARM daemons; returns (private tokens, cleanup or None)."""
    owned_cleanup = None
    private_tokens = {}
    for ref in ensemble_config.robots:
        broker = robots.get(ref.kind)
        if ref.kind == "soarm101" and broker.mode == "sdk_simulation_daemon":
            (token, trusted_token), owned_cleanup = start_owned_simulation(broker)
            private_tokens[broker.instance_id] = (token, trusted_token)
        elif ref.kind == "soarm101" and broker.mode == "sdk_physical_daemon":
            (token, trusted_token), owned_cleanup = start_owned_physical(broker)
            private_tokens[broker.instance_id] = (token, trusted_token)
    return private_tokens, owned_cleanup


def serve(config_path: str | Path) -> None:
    ensemble_config, robots, options = load_config(config_path)
    owned_cleanup = None
    try:
        private_tokens, owned_cleanup = _start_daemons(ensemble_config, robots)
        runtime, packs, connected, extras = build_ensemble(
            ensemble_config, robots, private_tokens, options)
        ensemble = ConversationEnsemble(
            runtime, packs, presence_ttl_s=ensemble_config.presence_ttl_s,
            stop_timeout_s=ensemble_config.stop_timeout_s,
            motion_patterns=extras.motion_patterns,
            # SDK completion cannot prove the arm has stopped moving: give it a
            # moment before the first spoken turn is allowed to start.
            startup_settle_s=1.0)
        create_ensemble_server(
            runtime, ensemble, connected=connected, motion_enabled=True,
            startup_gate=build_startup_gate(ensemble_config, robots, runtime, ensemble),
            stackchan_performer_id=next(
                (r.id for r in ensemble_config.robots if r.kind == "stackchan"), None),
            **extras.server_kwargs,
        ).run(transport="stdio")
    finally:
        if owned_cleanup is not None:
            owned_cleanup()


async def prompt_for_sleep(
    gate, *, assume_yes: bool = False, read_line: Callable[[str], str] = input, out=None,
) -> int:
    """Warn about each robot that is off its sleep pose and ask y/N; 0 if all end at sleep."""
    out = out or sys.stdout
    failed = False
    for name, entry in (await gate.check())["performers"].items():
        if entry["status"] in {"at_sleep", "confirmed_by_user"}:
            print(f"Sleep check: {name} is at its sleep position.", file=out)
            continue
        print(f"\nWARNING: {entry['message']}", file=out)
        commanded = entry["status"] == "needs_confirmation"
        if not entry.get("can_move_to_sleep") and not commanded:
            print(f"  {name} cannot be moved to sleep automatically.", file=out)
            failed = True
            continue
        question = (f"Is {name} parked at its sleep position? [y/N] " if commanded
                    else f"Move {name} to its sleep position now? [y/N] ")
        if assume_yes:
            print(f"{question}y (--yes)", file=out)
            answer = "y"
        else:
            out.flush()
            try:
                answer = await anyio.to_thread.run_sync(read_line, question)
            except EOFError:
                answer = ""
        if answer.strip().lower() not in {"y", "yes"}:
            print(f"  Left {name} as it is.", file=out)
            failed = True
            continue
        result = await (gate.confirm_at_sleep(name) if commanded else gate.sleep_robot(name))
        done = result.get("outcome") in {"at_sleep", "confirmed", "ok", "already_at_sleep"}
        print(f"  {name}: {result.get('outcome')}"
              + ("" if done else f" ({result.get('reason') or result.get('error') or ''})"), file=out)
        failed = failed or not done
    return 1 if failed else 0


def preflight(
    config_path: str | Path, *, assume_yes: bool = False,
    read_line: Callable[[str], str] = input, out=None,
) -> int:
    """Command-line check before a conversation: warn about a robot off its sleep pose, ask y/N.

    Plain code, no model involved. A robot that is not at its sleep pose is reported with its
    per-joint deltas; on "yes" it is laid down at sleep (one move, never retried; a leftover
    STOP fence is lifted once on the usual evidence first). A robot that only reports
    commanded positions is asked whether it is parked. Anything else, including end of input,
    leaves the robot as it is. Returns 0 when every robot is at sleep afterwards, else 1.
    """
    out = out or sys.stdout
    ensemble_config, robots, options = load_config(config_path)
    owned_cleanup = None
    try:
        private_tokens, owned_cleanup = _start_daemons(ensemble_config, robots)
        runtime, packs, connected, _extras = build_ensemble(
            ensemble_config, robots, private_tokens, options)
        ensemble = ConversationEnsemble(
            runtime, packs, presence_ttl_s=ensemble_config.presence_ttl_s,
            stop_timeout_s=ensemble_config.stop_timeout_s)
        gate = build_startup_gate(ensemble_config, robots, runtime, ensemble)
        if gate is None:
            print("Sleep check: no robot to check.", file=out)
            return 0

        async def run() -> int:
            async with AsyncExitStack() as stack:
                await stack.enter_async_context(connected())
                await stack.enter_async_context(runtime.run())
                await stack.enter_async_context(ensemble.run())
                return await prompt_for_sleep(gate, assume_yes=assume_yes, read_line=read_line, out=out)

        try:
            return anyio.run(run)
        finally:
            runtime.close()
    finally:
        if owned_cleanup is not None:
            owned_cleanup()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="forge-puppeteer-ensemble")
    parser.add_argument("--config", required=True, help="trusted operator ensemble JSON config (schema v4)")
    parser.add_argument(
        "--preflight", action="store_true",
        help="command-line sleep check: warn about a robot off its sleep pose and ask y/N to lay "
        "it down (the only prompt; no conversation is started)",
    )
    parser.add_argument("--yes", action="store_true", help="with --preflight, answer yes to the sleep move")
    parser.add_argument(
        "--check-config", action="store_true",
        help="validate config and print a readiness summary without connecting devices",
    )
    args = parser.parse_args(argv)
    try:
        if args.check_config:
            print(json.dumps(config_readiness(args.config), indent=2))
            return 0
        if args.preflight:
            return preflight(args.config, assume_yes=args.yes)
        serve(args.config)
    except (OSError, RuntimeError, ValueError, PermissionError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
