"""Client-only OpenShell delivery of Puppeteer's shared robot MCP runtime.

The host SDK owns the broker, STOP and the agent's OpenShell session.
This package owns *only* the MCP Python client bundle and configuration.
No SO-ARM motor/controller/SDK source code or secrets are copied.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from .soarm_guidance import GUIDANCE_RESOURCES

MCP_PYTHON = "/opt/soarm101-mcp/bin/python"
CONFIG_PATH = "/sandbox/puppeteer-soarm101.json"
BROKER_HOST = "host.openshell.internal"
BROKER_PORT = 8765
PACKAGE_ROOT = "/sandbox/forge_puppeteer"
SDK_POLICY_ROOT = "/sandbox/soarm101_motion"


def client_files(root: Path, *, sdk_profile_path: Path) -> tuple[tuple[Path, str], ...]:
    """Return a closed, local source bundle without the physical SDK.

    Copy only installed Puppeteer Python modules and the SDK's standalone
    capability_profile.py (pure stdlib). Never stage SDK motor modules,
    calibration, host private files, or an executable robot controller.
    """
    module_root = Path(__file__).resolve().parents[1]
    if not module_root.joinpath("mcp", "launcher.py").is_file():
        raise ValueError("installed Forge Puppeteer MCP launcher missing")
    if sdk_profile_path.name != "capability_profile.py" or not sdk_profile_path.is_file():
        raise ValueError("SDK broker capability policy source missing")
    package_init = root / "broker-policy-__init__.py"
    package_init.write_text('"""Broker capability registry only, no hardware SDK."""\n', encoding="utf-8")
    modules = sorted(module_root.rglob("*.py"))
    if not modules or any(not file.is_relative_to(module_root) for file in modules):
        raise ValueError("Puppeteer source package is invalid")
    return (
        (package_init, f"{SDK_POLICY_ROOT}/__init__.py"),
        (sdk_profile_path, f"{SDK_POLICY_ROOT}/capability_profile.py"),
        *((file, f"{PACKAGE_ROOT}/{file.relative_to(module_root).as_posix()}") for file in modules),
    )


def config_file(
    root: Path,
    *,
    allowed_tools: frozenset[str],
    broker_port: int,
    simulation_only: bool,
    physical_authority: bool,
) -> Path:
    """Only trusted operator values; this JSON has no token or model input."""
    if not (1 <= broker_port <= 65535):
        raise ValueError("invalid broker port")
    from soarm101_motion.capability_profile import BROKER_CAPABILITY_ROUTES

    if not allowed_tools or not allowed_tools <= frozenset(BROKER_CAPABILITY_ROUTES):
        raise ValueError("unknown broker capability in operator grant")
    if simulation_only and physical_authority:
        raise ValueError("simulation and physical authority cannot be combined")
    data = {
        "backend": "soarm101_broker",
        "device": "soarm101",
        "instance_id": "openshell-soarm101",
        "name": "SO-ARM101 broker through Forge Puppeteer",
        "simulation_only": simulation_only,
        "physical_authority": (
            "I_AUTHORIZE_BROKER_CONTROL" if physical_authority else None
        ),
        "broker_url": f"http://{BROKER_HOST}:{broker_port}",
        "allow_openshell_host": True,
        "broker_token_env": "SOARM101_BROKER_TOKEN",
        "allowed_tools": sorted(allowed_tools),
    }
    path = root / "puppeteer-soarm101.json"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def agent_config(
    root: Path, *, agent: Literal["hermes", "codex"],
    hermes_config: Path | None = None,
) -> tuple[Path, str]:
    """Create the canonical single server reference used by either agent."""
    if agent == "hermes":
        if hermes_config is None or not hermes_config.is_file():
            raise ValueError("Hermes one-run configuration is missing")
        with hermes_config.open("a", encoding="utf-8") as handle:
            handle.write(
                "\nmcp_servers:\n"
                "  soarm101:\n"
                f'    command: "{MCP_PYTHON}"\n'
                f'    args: ["-m", "forge_puppeteer.mcp.launcher", "--config", "{CONFIG_PATH}"]\n'
                "    timeout: 125\n"
                "    env:\n"
                '      SOARM101_BROKER_TOKEN: "${SOARM101_BROKER_TOKEN}"\n'
                "    supports_parallel_tool_calls: false\n"
                "    tools:\n"
                "      resources: true\n"
                "      prompts: true\n"
            )
        return hermes_config, "/sandbox/.hermes/config.yaml"
    if agent == "codex":
        path = root / "codex-mcp-config.toml"
        path.write_text(
            "[mcp_servers.soarm101]\n"
            f'command = "{MCP_PYTHON}"\n'
            f'args = ["-m", "forge_puppeteer.mcp.launcher", "--config", "{CONFIG_PATH}"]\n'
            "startup_timeout_sec = 30\n"
            "tool_timeout_sec = 125\n"
            'env_vars = ["SOARM101_BROKER_TOKEN"]\n',
            encoding="utf-8",
        )
        return path, "/sandbox/.codex/config.toml"
    raise ValueError("unsupported sandbox MCP agent")


def skill_text(*, agent: Literal["hermes", "codex"]) -> str:
    viewer = "Codex's MCP image viewer" if agent == "codex" else "Hermes MCP image content"
    return (
        "---\n"
        f"name: soarm101-openshell-{agent}-puppeteer-mcp\n"
        "description: Call profile-scoped SO-ARM101 capabilities through Puppeteer MCP.\n"
        "---\n\n"
        "# SO-ARM101 through Forge Puppeteer MCP\n\n"
        "Use MCP tools, not robotctl/raw SDK/camera device. Read robot_capabilities "
        "and robot_state before taking action. The broker owns authority and STOP; "
        "a missing/denied capability is not permission to try a different path. "
        "Motion tools use Puppeteer's action_id, caller_id, session_id and typed "
        "parameters. Treat unknown outcomes as unsafe to retry without reconciliation. "
        f"Verify actual camera pixels using {viewer}; if the client cannot inspect "
        "images, say so.\n\n"
        + "\n\n".join(GUIDANCE_RESOURCES.values()) + "\n"
    )
