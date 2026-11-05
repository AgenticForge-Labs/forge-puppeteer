"""Reachy + StackChan duo uses one existing room, never a simulated SO-ARM.

Every test is offline. The sole Reachy and StackChan SDK sessions are fakes;
no hardware is opened or movement authorized by this suite.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from forge_puppeteer.mcp import ensemble_launcher as launcher


def duo_data(tmp_path: Path) -> dict:
    return {
        "schema_version": 3,
        "motion_authority": "I_AUTHORIZE_ENSEMBLE_ROBOT_MOTION",
        "reachy_neutral_head": str(tmp_path / "head.json"),
        "reachy": {
            "backend": "reachy_sdk", "device": "reachy",
            "instance_id": "reachy", "physical_authority": "I_AUTHORIZE_REACHY_CONTROL",
            "allowed_tools": ["head_pose", "play_move", "device_stop"],
            "asset_root": str(tmp_path / "assets"),
            "manifest": str(tmp_path / "manifest.json"),
            "sdk_version": "1.10.0rc5",
            "host": "127.0.0.1", "connection_mode": "localhost_only",
            "daemon_port": 8000,
            "daemon": {"command": ["/bin/true"]},
            "expected_daemon_hardware_id": "reviewed-test-hardware",
        },
        "stackchan": {
            "backend": "stackchan_sdk", "device": "stackchan",
            "instance_id": "stackchan", "physical_authority": "I_AUTHORIZE_STACKCHAN_CONTROL",
            "allowed_tools": ["head_move", "face_update", "device_stop"],
            "port": "/dev/ttyACM0", "sdk_version": "0.4.0",
        },
        "action_journal": str(tmp_path / "actions.sqlite3"),
        "ownership_db": str(tmp_path / "owners.sqlite3"),
    }


def test_duo_requires_exact_reviewed_identity_and_excludes_arm(tmp_path):
    config = launcher.EnsembleConfig.model_validate(duo_data(tmp_path))
    assert config.schema_version == 3
    assert config.soarm101 is None
    assert config.stackchan_display_presets_enabled is False

    for mutation in (
        {"soarm101": None}, {"soarm101": {"mode": "simulation"}},
        {"motion_authority": "REVIEW_REQUIRED"},
        {"reachy_neutral_head": "relative.json"},
        {"ownership_db": str(tmp_path / "actions.sqlite3")},
    ):
        with pytest.raises(ValueError):
            launcher.EnsembleConfig.model_validate(duo_data(tmp_path) | mutation)

    same_id = duo_data(tmp_path)
    same_id["stackchan"]["instance_id"] = "reachy"
    with pytest.raises(ValueError, match="unique performer"):
        launcher.EnsembleConfig.model_validate(same_id)
    without_arm = duo_data(tmp_path)
    without_arm["schema_version"] = 1
    with pytest.raises(ValueError, match="SO-ARM101"):
        launcher.EnsembleConfig.model_validate(without_arm)


def test_duo_validates_optional_face_presentation_grant(tmp_path):
    data = duo_data(tmp_path)
    data["stackchan_display_presets_enabled"] = True
    with pytest.raises(ValueError, match="face_scale_update"):
        launcher.EnsembleConfig.model_validate(data)
    data["stackchan"]["allowed_tools"].append("face_scale_update")
    config = launcher.EnsembleConfig.model_validate(data)
    assert list(launcher.stackchan_display_presets(config)["stackchan"]) == [
        "normal", "enlarged",
    ]
    data["stackchan"]["sdk_version"] = "0.3.0"
    with pytest.raises(ValueError, match="SDK 0.4.0"):
        launcher.EnsembleConfig.model_validate(data)


@pytest.mark.anyio
async def test_duo_builder_connects_only_two_native_providers(tmp_path, monkeypatch):
    connections = []

    class FakeProvider:
        def __init__(self, config):
            self.config = config

        @asynccontextmanager
        async def connected(self):
            connections.append((self.config.instance_id, "open"))
            try:
                yield
            finally:
                connections.append((self.config.instance_id, "closed"))

    monkeypatch.setattr(launcher, "ReachySDKProvider", FakeProvider)
    monkeypatch.setattr(launcher, "StackChanSDKProvider", FakeProvider)

    config = launcher.EnsembleConfig.model_validate(duo_data(tmp_path))
    reachy, stackchan, runtime, connected = launcher.build_reachy_stackchan_ensemble(config)
    assert reachy is not stackchan
    try:
        assert set(runtime._providers) == {"reachy", "stackchan"}
        async with connected():
            assert connections == [("reachy", "open"), ("stackchan", "open")]
        assert connections == [
            ("reachy", "open"), ("stackchan", "open"),
            ("stackchan", "closed"), ("reachy", "closed"),
        ]
    finally:
        runtime.close()


def test_duo_launcher_exposes_both_performers_without_synthetic_arm(tmp_path, monkeypatch):
    from forge_puppeteer.behavior_contract import (
        BehaviorCue,
        BehaviorPattern,
        PerformerBehaviors,
    )

    class FakeProvider:
        def __init__(self, config):
            self.catalog = object()

        @asynccontextmanager
        async def connected(self):
            raise AssertionError("offline serve test must not connect hardware")
            yield  # pragma: no cover

    monkeypatch.setattr(launcher, "ReachySDKProvider", FakeProvider)
    monkeypatch.setattr(launcher, "StackChanSDKProvider", FakeProvider)

    async def fake_reachy_pack(*_args, **_kwargs):
        return PerformerBehaviors.author("reachy", {
            name: BehaviorPattern(
                source="procedural", mode="loop", period_s=1,
                cues=(BehaviorCue(at_s=0, operation="head_pose"),),
            ) for name in ("idle", "speaking", "listening")
        })

    seen = []

    class FakeServer:
        def run(self, *, transport):
            seen.append(("transport", transport))

    def create_server(runtime, ensemble, *, connected, motion_enabled, **kwargs):
        seen.append(("performers", sorted(ensemble.packs)))
        seen.append(("presets", kwargs.get("presentation_presets", {})))
        assert set(runtime._providers) == {"reachy", "stackchan"}
        assert motion_enabled is True
        return FakeServer()

    monkeypatch.setattr(launcher, "reachy_pack_from_catalog", fake_reachy_pack)
    monkeypatch.setattr(launcher, "create_ensemble_server", create_server)
    neutral = tmp_path / "head.json"
    neutral.write_text(json.dumps([[1, 0, 0, 0]] * 4))
    file = tmp_path / "duo.json"
    file.write_text(json.dumps(duo_data(tmp_path)))
    launcher.serve(file)
    assert seen == [
        ("performers", ["reachy", "stackchan"]),
        ("presets", {}),
        ("transport", "stdio"),
    ]


def test_review_only_authoring_requires_no_arm_file_or_sdk(tmp_path):
    from importlib.util import module_from_spec, spec_from_file_location

    source = Path(__file__).resolve().parents[2] / "scripts" / "author_three_robot_ensemble.py"
    spec = spec_from_file_location("ensemble_review_generator", source)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    author_config = module.author_config

    data = author_config(
        reachy_manifest=tmp_path / "manifest.json",
        reachy_recordings=tmp_path / "recordings",
        reachy_neutral_file=tmp_path / "head.json",
        soarm_neutral_file=None,
        stackchan_port="/dev/ttyACM0",
        action_journal=tmp_path / "actions.db",
        ownership_db=tmp_path / "owners.db",
        reachy_stackchan_only=True,
    )
    assert data["schema_version"] == 3
    assert "soarm101" not in data
    assert data["stackchan"]["sdk_version"] == "0.4.0"
    assert data["motion_authority"] != "I_AUTHORIZE_ENSEMBLE_ROBOT_MOTION"
    with pytest.raises(ValueError, match="motion_authority"):
        launcher.EnsembleConfig.model_validate(data)


@pytest.fixture
def anyio_backend():
    return "asyncio"



def test_duo_smooth_stackchan_speaking_selects_native_sdk_face_and_dynamics(
    tmp_path, monkeypatch,
):
    from forge_puppeteer.behavior_contract import (
        BehaviorCue,
        BehaviorPattern,
        PerformerBehaviors,
    )

    class FakeProvider:
        def __init__(self, config):
            self.catalog = object()

        @asynccontextmanager
        async def connected(self):
            raise AssertionError("no real SDK connection in software preflight")
            yield  # pragma: no cover

    async def fake_reachy(*_args, **_kwargs):
        return PerformerBehaviors.author("reachy", {
            name: BehaviorPattern(
                source="procedural", mode="loop", period_s=1.0,
                cues=(BehaviorCue(at_s=0.0, operation="head_pose"),),
            ) for name in ("idle", "listening", "speaking")
        })

    captured = []
    class FakeServer:
        def run(self, *, transport):
            assert transport == "stdio"

    def server(_runtime, ensemble, **kwargs):
        captured.append((ensemble, kwargs))
        return FakeServer()

    monkeypatch.setattr(launcher, "ReachySDKProvider", FakeProvider)
    monkeypatch.setattr(launcher, "StackChanSDKProvider", FakeProvider)
    monkeypatch.setattr(launcher, "reachy_pack_from_catalog", fake_reachy)
    monkeypatch.setattr(launcher, "create_ensemble_server", server)
    (tmp_path / "head.json").write_text(json.dumps([[1, 0, 0, 0]] * 4))
    data = duo_data(tmp_path)
    data["motion_profiles"] = {"stackchan": {"speaking": "smooth_v2"}}
    data["speech_driven_performers"] = ["stackchan"]
    config = tmp_path / "selected.json"
    config.write_text(json.dumps(data))
    launcher.serve(config)

    ensemble, kwargs = captured[0]
    assert kwargs["speech_dynamics"] == {"stackchan": "smoothed_v1"}
    assert kwargs["stackchan_performer_id"] == "stackchan"
    assert ensemble.packs["stackchan"].behaviors["speaking"].cues[0].parameters[
        "mouth"
    ]["weight"] == 45
    assert "weight" not in ensemble.packs["reachy"].behaviors["speaking"].cues[0].parameters
