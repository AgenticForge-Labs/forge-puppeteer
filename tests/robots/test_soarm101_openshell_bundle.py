"""OpenShell packaging is a client-only Puppeteer delivery mechanism."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from forge_puppeteer.devices.soarm101_broker_http import SOARM101BrokerHTTP
from forge_puppeteer.mcp.openshell_bundle import (
    CONFIG_PATH,
    MCP_PYTHON,
    agent_config,
    client_files,
    config_file,
    skill_text,
)


def test_sandbox_bundle_contains_puppeteer_not_sdk_motion(tmp_path: Path) -> None:
    sdk_policy = tmp_path / "capability_profile.py"
    sdk_policy.write_text('BROKER_CAPABILITY_ROUTES = {"robot_state": ("GET", "/v1/state")}\n')
    files = client_files(tmp_path, sdk_profile_path=sdk_policy)
    destinations = {dest for _, dest in files}
    assert "/sandbox/soarm101_motion/capability_profile.py" in destinations
    assert "/sandbox/forge_puppeteer/mcp/launcher.py" in destinations
    assert "/sandbox/forge_puppeteer/mcp/soarm101.py" in destinations
    assert "/sandbox/forge_puppeteer/devices/soarm101_broker_http.py" in destinations
    assert not any(dest.startswith("/sandbox/soarm101_motion/") and
                   dest.endswith(("/arm.py", "/broker.py", "/mcp_server.py"))
                   for dest in destinations)
    assert not any("calibration" in dest or "controller" in dest for dest in destinations)
    assert all(file.is_file() for file, _ in files)


def test_sandbox_config_only_contains_operator_policy_not_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Standalone SDK's immutable broker registry is an optional install in
    # the test host, not the sandbox module/command that will be launched.
    import sys
    from types import ModuleType

    sdk = ModuleType("soarm101_motion")
    sdk.__path__ = []  # type: ignore[attr-defined]
    profile = ModuleType("soarm101_motion.capability_profile")
    profile.BROKER_CAPABILITY_ROUTES = {  # type: ignore[attr-defined]
        "robot_state": ("GET", "/v1/state"),
        "robot_health": ("GET", "/v1/health"),
    }
    monkeypatch.setitem(sys.modules, "soarm101_motion", sdk)
    monkeypatch.setitem(sys.modules, "soarm101_motion.capability_profile", profile)
    p = config_file(
        tmp_path,
        allowed_tools=frozenset({"robot_state", "robot_health"}),
        broker_port=9876,
        simulation_only=True,
        physical_authority=False,
    )
    data = json.loads(p.read_text())
    assert data["broker_url"] == "http://host.openshell.internal:9876"
    assert data["allow_openshell_host"] is True
    assert data["broker_token_env"] == "SOARM101_BROKER_TOKEN"
    assert "TOKEN_VALUE" not in p.read_text()
    assert data["simulation_only"] is True
    assert not data["physical_authority"]
    assert data["allowed_tools"] == ["robot_health", "robot_state"]
    with pytest.raises(ValueError, match="unknown"):
        config_file(
            tmp_path,
            allowed_tools=frozenset({"raw_register_write"}),
            broker_port=9876, simulation_only=True, physical_authority=False,
        )


def test_puppeteer_mcp_configs_target_shared_launcher(tmp_path: Path) -> None:
    hermes = tmp_path / "hermes.yaml"
    hermes.write_text("name: one-run\n")
    result = agent_config(tmp_path, agent="hermes", hermes_config=hermes)
    assert result[1] == "/sandbox/.hermes/config.yaml"
    assert "forge_puppeteer.mcp.launcher" in hermes.read_text()
    assert "soarm101_motion.mcp_server" not in hermes.read_text()
    assert MCP_PYTHON in hermes.read_text()
    codex = agent_config(tmp_path, agent="codex")
    assert codex[1] == "/sandbox/.codex/config.toml"
    assert "forge_puppeteer.mcp.launcher" in codex[0].read_text()
    assert CONFIG_PATH in codex[0].read_text()
    assert "token" not in codex[0].read_text().lower() or "BROKER_TOKEN" in codex[0].read_text()
    assert "Forge Puppeteer" in skill_text(agent="hermes")


def test_openshell_host_is_never_accepted_by_default() -> None:
    with pytest.raises(ValueError, match="loopback"):
        SOARM101BrokerHTTP("http://host.openshell.internal:8765", "dummy")
    with pytest.raises(ValueError, match="loopback"):
        SOARM101BrokerHTTP(
            "http://arbitrary.example:8765", "dummy", allow_openshell_host=True,
        )
