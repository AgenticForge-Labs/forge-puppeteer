"""Regression checks for the reviewed offline downstream handoff fixture."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_studios_reachy_handoff_pins_the_mock_pack_and_catalog():
    config = json.loads(
        (ROOT / "examples/robots/reachy/mock-mcp-server.json").read_text()
    )
    pack_path = ROOT / "examples/robots/reachy/behavior-packs.mock.json"
    pack_file = json.loads(pack_path.read_text())
    handoff = json.loads(
        (ROOT / "examples/integrations/studios/reachy-shot-handoff.json").read_text()
    )
    from forge_puppeteer.contracts import PhysicalShotRequest

    PhysicalShotRequest.model_validate(handoff)
    cue = pack_file["packs"][0]["cues"][0]

    assert config["backend"] == "fake"
    assert "play_move" in config["allowed_tools"]
    assert "head_pose" not in config["allowed_tools"]
    assert handoff["metadata"]["behavior_pack_revision"] == (
        "sha256:" + hashlib.sha256(pack_path.read_bytes()).hexdigest()
    )
    assert cue["asset_id"] == handoff["metadata"]["catalog_asset_id"]
    assert cue["asset_revision"] == handoff["metadata"]["catalog_asset_revision"]
    assert cue["asset_revision"] == "sha256:forge-puppeteer-fake-v1"
