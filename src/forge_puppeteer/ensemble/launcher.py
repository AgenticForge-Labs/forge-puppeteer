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
from pathlib import Path

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


def serve(config_path: str | Path) -> None:
    ensemble_config, robots, options = load_config(config_path)
    owned_cleanup = None
    private_tokens = {}
    try:
        for ref in ensemble_config.robots:
            broker = robots.get(ref.kind)
            if ref.kind == "soarm101" and broker.mode == "sdk_simulation_daemon":
                (token, trusted_token), owned_cleanup = start_owned_simulation(broker)
                private_tokens[broker.instance_id] = (token, trusted_token)
            elif ref.kind == "soarm101" and broker.mode == "sdk_physical_daemon":
                (token, trusted_token), owned_cleanup = start_owned_physical(broker)
                private_tokens[broker.instance_id] = (token, trusted_token)
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="forge-puppeteer-ensemble")
    parser.add_argument("--config", required=True, help="trusted operator ensemble JSON config (schema v4)")
    parser.add_argument(
        "--check-config", action="store_true",
        help="validate config and print a readiness summary without connecting devices",
    )
    args = parser.parse_args(argv)
    try:
        if args.check_config:
            print(json.dumps(config_readiness(args.config), indent=2))
            return 0
        serve(args.config)
    except (OSError, RuntimeError, ValueError, PermissionError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
