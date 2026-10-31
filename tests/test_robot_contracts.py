import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from forge_puppeteer.performance import Performance
from forge_puppeteer.robot_contracts import (
    ActionRecord,
    ActionState,
    AssetDescriptor,
    ControlDescriptor,
    DeviceDescriptor,
)


def test_catalog_accepts_new_asset_names_without_code_changes():
    assets = [
        AssetDescriptor(id=name, kind="recording", source="fixture", revision="sha256:123")
        for name in ("reachy/curious1", "reachy/a-new-motion")
    ]
    assert [asset.id for asset in assets] == ["reachy/curious1", "reachy/a-new-motion"]


def test_asset_rejects_unbounded_duration_and_unknown_fields():
    with pytest.raises(ValidationError):
        AssetDescriptor(id="x", kind="pose", source="fixture", revision="1", duration_s=float("inf"))
    with pytest.raises(ValidationError):
        AssetDescriptor.model_validate({"id": "x", "kind": "pose", "source": "fixture", "revision": "1", "extra": True})


def test_unknown_outcome_is_distinct_from_completion():
    action = ActionRecord(id="a", state=ActionState.UNKNOWN, operation="move")
    assert action.model_dump(mode="json")["state"] == "unknown"
    assert action.completion_evidence is None


def test_device_metadata_preserves_distinct_native_control_schemas():
    controls = (
        ControlDescriptor(id="stackchan.face", description="Face", effect="visual",
                          parameters_schema={"type": "object", "properties": {"eyes": {"type": "string"}}},
                          resources=("face.eyes",)),
        ControlDescriptor(id="reachy.antennas", description="Antennas", effect="motion",
                          parameters_schema={"type": "object", "properties": {"left_rad": {"type": "number"}}},
                          resources=("antennas.left",), unit="rad"),
        ControlDescriptor(id="soarm.gripper", description="Gripper", effect="motion",
                          parameters_schema={"type": "object", "properties": {"position": {"type": "number"}}},
                          resources=("gripper",)),
    )
    descriptor = DeviceDescriptor(id="fixture", provider="fixture", connected=False,
                                  ready=False, controls=controls)
    restored = DeviceDescriptor.model_validate_json(descriptor.model_dump_json())
    assert restored == descriptor
    assert len({tuple(control.parameters_schema["properties"]) for control in controls}) == 3
    assert restored.feedback == "unknown"


def test_metadata_rejects_non_json_values():
    with pytest.raises(ValidationError):
        ActionRecord(id="a", state=ActionState.ACCEPTED, operation="test",
                     parameters={"invalid": object()})


@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
def test_nested_nonfinite_numbers_are_rejected_before_serialization(number):
    with pytest.raises(ValidationError):
        ActionRecord(id="a", state=ActionState.ACCEPTED, operation="test",
                     parameters={"nested": [{"value": number}]})
    with pytest.raises(ValidationError):
        DeviceDescriptor(id="d", provider="fixture", connected=False, ready=False,
                         effective_limits={"axis": {"max": number}})


def test_example_catalog_is_offline_metadata():
    path = Path(__file__).resolve().parents[1] / "examples/robots/metadata.json"
    fixture = json.loads(path.read_text())
    devices = [DeviceDescriptor.model_validate(item) for item in fixture["devices"]]
    assets = [AssetDescriptor.model_validate(item) for item in fixture["assets"]]
    assert {device.provider for device in devices} == {"stackchan", "reachy", "soarm101"}
    assert all(not device.connected and not device.ready for device in devices)
    assert {asset.kind for asset in assets} == {"pose", "recording", "sequence"}


def test_example_performance_uses_existing_semantic_contract():
    path = Path(__file__).resolve().parents[1] / "examples/robots/mock-performance.json"
    performance = Performance.model_validate_json(path.read_text())
    assert [cue.character for cue in performance.cues] == ["stackchan", "reachy", "arm"]
