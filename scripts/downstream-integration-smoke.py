"""Run the downstream handoff fixture through the installed packaged MCP server."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import anyio

from forge_puppeteer.contracts import PhysicalShotRequest
from forge_puppeteer.mcp.client import MCPClient, ServerConfig

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


async def main() -> None:
    config = EXAMPLES / "robots/reachy/mock-mcp-server.json"
    packs = EXAMPLES / "robots/reachy/behavior-packs.mock.json"
    handoff = json.loads(
        (EXAMPLES / "integrations/studios/reachy-shot-handoff.json").read_text()
    )
    PhysicalShotRequest.model_validate(handoff)
    expected_pack_revision = "sha256:" + hashlib.sha256(packs.read_bytes()).hexdigest()
    assert handoff["metadata"]["behavior_pack_revision"] == expected_pack_revision

    client = MCPClient(
        {"reachy": ServerConfig(
            command="forge-puppeteer-mcp",
            args=("--config", str(config)),
        )},
        behavior_packs_path=packs,
        completion_timeout=5,
    )
    plan = client.compile_behavior_pack(
        "offline-hello", expected_pack_revision, handoff["shot_id"], server="reachy"
    )
    assert dict(plan.manifest)["behavior:offline-hello"] == expected_pack_revision
    assert plan.cues[0].asset_id == handoff["metadata"]["catalog_asset_id"]
    assert plan.cues[0].asset_revision == handoff["metadata"]["catalog_asset_revision"]
    results = await client.execute(plan)
    assert len(results) == 1
    assert results[0]["result"]["state"] == "completed", results

    # Exact behavior-pack pins are fail-closed before the MCP server is launched.
    try:
        client.compile_behavior_pack(
            "offline-hello", "sha256:stale", handoff["shot_id"], server="reachy"
        )
    except ValueError as exc:
        assert "stale_behavior_pack" in str(exc)
    else:
        raise AssertionError("stale pack revision was accepted")

    print(json.dumps({"status": "passed", "take_id": handoff["shot_id"],
                      "behavior_pack_revision": expected_pack_revision,
                      "catalog_asset_id": handoff["metadata"]["catalog_asset_id"],
                      "catalog_asset_revision": handoff["metadata"]["catalog_asset_revision"]}))


if __name__ == "__main__":
    anyio.run(main)
