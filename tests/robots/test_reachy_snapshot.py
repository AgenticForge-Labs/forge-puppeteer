import asyncio
import hashlib
import json

import pytest

from forge_puppeteer.devices.reachy import ReachyCatalog, ReachyPlaybackProvider
from forge_puppeteer.execution import ActionRequest


class Move:
    def __init__(self, data, sound_path=None):
        self.description = data["description"]
        self.duration = data["duration"]
        self.sound_path = sound_path


def snapshot(root, names):
    root.mkdir()
    for name in names:
        (root / f"{name}.json").write_text(
            json.dumps({"description": name, "duration": 1.0})
        )
    (root / f"{names[0]}.wav").write_bytes(b"audio")
    return {
        "version": 1,
        "dataset_revision": "abc123",
        "sdk_version": "1.10.0rc5",
        "source_url": "https://example.org/dataset",
        "license": "fixture license",
        "notices": "fixture notice",
        "files": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir()
        },
    }


def test_catalogs_are_discovered_and_audio_is_explicit(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    a = ReachyCatalog(first, snapshot(first, ["hello", "wave"]), move_factory=Move)
    b = ReachyCatalog(second, snapshot(second, ["different"]), move_factory=Move)
    assets = asyncio.run(a.list_assets())
    assert [x.id for x in assets] == ["reachy/hello", "reachy/wave"]
    assert assets[0].audio_association == "hello.wav"
    assert [x.id for x in asyncio.run(b.list_assets())] == ["reachy/different"]
    assert a.get("reachy/hello", a.revision).description == "hello"
    with pytest.raises(ValueError, match="unknown"):
        a.get("reachy/missing", a.revision)
    with pytest.raises(ValueError, match="revision"):
        a.get("reachy/hello", "old")
    assert not asyncio.run(a.describe()).ready


def test_catalog_rejects_an_unreviewed_sdk_pin_even_with_fake_factory(tmp_path):
    root = tmp_path / "pack"
    lock = snapshot(root, ["hello"])
    lock["sdk_version"] = "1.11.0"
    with pytest.raises(ValueError, match="unsupported Reachy SDK version"):
        ReachyCatalog(root, lock, move_factory=Move)


def test_snapshot_mutation_missing_and_extra_fail(tmp_path):
    root = tmp_path / "pack"
    lock = snapshot(root, ["hello"])
    catalog = ReachyCatalog(root, lock, move_factory=Move)
    path = root / "hello.json"
    original = path.read_bytes()
    path.write_text("{}")
    with pytest.raises(ValueError, match="mutated"):
        asyncio.run(catalog.list_assets())
    path.unlink()
    with pytest.raises(ValueError, match="missing"):
        catalog.get("reachy/hello", catalog.revision)
    path.write_bytes(original)
    (root / "extra").write_text("unexpected")
    with pytest.raises(ValueError, match="inventory"):
        asyncio.run(catalog.list_assets())


def test_unsafe_and_duplicate_names_fail(tmp_path):
    root = tmp_path / "pack"
    lock = snapshot(root, ["hello"])
    lock["files"]["../escape"] = "0" * 64
    with pytest.raises(ValueError, match="path"):
        ReachyCatalog(root, lock, move_factory=Move)
    del lock["files"]["../escape"]
    (root / "data").mkdir()
    duplicate = root / "data" / "hello.json"
    duplicate.write_bytes((root / "hello.json").read_bytes())
    lock["files"]["data/hello.json"] = hashlib.sha256(
        duplicate.read_bytes()
    ).hexdigest()
    catalog = ReachyCatalog(root, lock, move_factory=Move)
    with pytest.raises(ValueError, match="duplicate"):
        asyncio.run(catalog.list_assets())


def test_build_manifest_is_deterministic_and_checks_inventory(tmp_path):
    from forge_puppeteer.deployment import build_reachy_manifest, verify_reachy_snapshot

    root = tmp_path / "pack"
    snapshot(root, ["hello"])
    options = {
        "dataset_revision": "revision",
        "sdk_version": "1.10.0rc5",
        "source_url": "https://example.org",
        "license": "fixture",
        "notices": "fixture",
    }
    first = build_reachy_manifest(root, **options)
    assert first == build_reachy_manifest(root, **options)
    assert verify_reachy_snapshot(root, first).startswith("sha256:")
    (root / "hello.wav").unlink()
    with pytest.raises(ValueError, match="missing"):
        verify_reachy_snapshot(root, first)


def test_move_bytes_are_rechecked_after_snapshot_verification(tmp_path, monkeypatch):
    from forge_puppeteer.devices import reachy

    root = tmp_path / "pack"
    catalog = ReachyCatalog(root, snapshot(root, ["hello"]), move_factory=Move)
    verify = reachy.verify_reachy_snapshot

    def mutate_after_verification(path, manifest):
        revision = verify(path, manifest)
        (path / "hello.json").write_text('{"description":"changed","duration":1}')
        return revision

    monkeypatch.setattr(reachy, "verify_reachy_snapshot", mutate_after_verification)
    with pytest.raises(ValueError, match="mutated"):
        catalog.get("reachy/hello", catalog.revision)


def test_playback_requires_authority_and_reports_only_sdk_loop_completion(tmp_path):
    root = tmp_path / "pack"
    catalog = ReachyCatalog(root, snapshot(root, ["hello"]), move_factory=Move)

    class Robot:
        def __init__(self):
            self.calls = []

        async def async_play_move(self, move, *, sound=True):
            self.calls.append((move, sound))

        def cancel_move(self):
            self.calls.append("cancel")

    robot = Robot()
    request = ActionRequest(
        id="action-1", performer_id="reachy", caller_id="test", session_id="s",
        operation="play_move", asset_id="reachy/hello", asset_revision=catalog.revision,
    )
    denied = ReachyPlaybackProvider(catalog, robot)
    with pytest.raises(PermissionError, match="authority"):
        asyncio.run(denied.validate(request))
    assert robot.calls == []

    provider = ReachyPlaybackProvider(catalog, robot, motion_authorized=True)
    completion = asyncio.run(provider.execute(request))
    assert "SDK playback loop returned" in completion.evidence
    assert "physical settling unmeasured" in completion.evidence
    assert robot.calls[0][0].description == "hello"
    assert robot.calls[0][1] is False
    stopped = asyncio.run(provider.stop())
    assert not stopped.confirmed
    assert "not confirmed" in stopped.evidence
    assert robot.calls[-1] == "cancel"


def test_playback_rejects_unknown_operations_and_sound_types(tmp_path):
    root = tmp_path / "pack"
    catalog = ReachyCatalog(root, snapshot(root, ["hello"]), move_factory=Move)

    class Robot:
        async def async_play_move(self, *args, **kwargs):
            raise AssertionError("invalid request must not reach SDK")

    provider = ReachyPlaybackProvider(catalog, Robot(), motion_authorized=True)
    base = {
        "id": "a", "performer_id": "reachy", "caller_id": "test", "session_id": "s",
        "asset_id": "reachy/hello", "asset_revision": catalog.revision,
    }
    with pytest.raises(ValueError, match="unsupported"):
        asyncio.run(provider.validate(ActionRequest(**base, operation="native_control")))
    with pytest.raises(ValueError, match="boolean sound"):
        asyncio.run(provider.validate(ActionRequest(
            **base, operation="play_move", parameters={"sound": "yes"}
        )))


def test_native_head_and_antenna_controls_validate_and_wait_for_sdk_completion(tmp_path):
    root = tmp_path / "pack"
    catalog = ReachyCatalog(root, snapshot(root, ["hello"]), move_factory=Move)

    class Robot:
        def __init__(self):
            self.calls = []

        def goto_target(self, **kwargs):
            self.calls.append(kwargs)

    robot = Robot()
    provider = ReachyPlaybackProvider(catalog, robot, motion_authorized=True)
    base = {"id": "native", "performer_id": "reachy", "caller_id": "test", "session_id": "s"}
    pose = [[1.0, 0, 0, 0.02], [0, 1.0, 0, 0], [0, 0, 1.0, 0], [0, 0, 0, 1.0]]
    head = ActionRequest(**base, operation="head_pose", parameters={"pose": pose, "duration_s": 1.0})
    completion = asyncio.run(provider.execute(head))
    assert "SDK goto task completed" in completion.evidence
    assert "not independently measured" in completion.evidence
    assert robot.calls[-1] == {"head": pose, "duration": 1.0, "body_yaw": None}

    antenna = ActionRequest(**(base | {"id": "antenna"}), operation="antenna_targets", parameters={"angles_rad": [0.4, -0.7]})
    asyncio.run(provider.execute(antenna))
    assert robot.calls[-1] == {"antennas": [0.4, -0.7], "duration": 0.5, "body_yaw": None}

    for bad in (
        {"pose": [[1, 0, 0, 0.11], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]},
        {"pose": [[float("nan"), 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]},
        {"pose": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [1, 0, 0, 1]]},
    ):
        with pytest.raises(ValueError):
            asyncio.run(provider.validate(ActionRequest(**base, operation="head_pose", parameters=bad)))
    with pytest.raises(ValueError, match="adapter bounds"):
        asyncio.run(provider.validate(ActionRequest(**base, operation="antenna_targets", parameters={"angles_rad": [1.21, 0]})))
    with pytest.raises(PermissionError, match="authority"):
        asyncio.run(ReachyPlaybackProvider(catalog, robot).validate(head))
