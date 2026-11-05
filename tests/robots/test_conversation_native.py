"""Native behavior authoring is SDK-validated, persona-agnostic and revisioned."""

import hashlib
import json
import math
from pathlib import Path

import anyio
import pytest

from forge_puppeteer.conversation_ensemble import ConversationEnsemble, EnsemblePresence
from forge_puppeteer.conversation_native import (
    reachy_conversation_pack,
    stackchan_conversation_pack,
    stackchan_recorded_assets,
)
from forge_puppeteer.execution import ExecutionRuntime
from forge_puppeteer.reachy_sync import (
    OFFICIAL_REACHY_DATASET,
    recording_url,
    sync_reachy_recordings,
)
from forge_puppeteer.runtime import PerformanceStage

NEUTRAL = [
    [1.0, 0.0, 0.0, 0.0],
    [0.0, 1.0, 0.0, 0.0],
    [0.0, 0.0, 1.0, 0.0],
    [0.0, 0.0, 0.0, 1.0],
]
REV = "sha256:" + "a" * 64


def test_reachy_native_pack_uses_exact_sdk_ops_and_asset_pins():
    pack = reachy_conversation_pack(
        "reachy", neutral_head=NEUTRAL, recording_revision=REV,
        available_recordings=("yes1", "no1", "cheerful1", "curious1"),
        breath_amplitude_m=0.004,
    )
    assert pack.revision == pack.content_revision()
    assert "4g" not in json.dumps(pack.model_dump())
    assert {"idle", "listening", "speaking", "yes", "no", "neutral"} <= set(pack.behaviors)
    assert pack.behaviors["yes"].cues[0].asset_id == "reachy/yes1"
    assert pack.behaviors["yes"].cues[0].asset_revision == REV
    assert pack.behaviors["yes"].cues[0].parameters == {"sound": False}
    assert pack.behaviors["no"].cues[0].asset_id == "reachy/no1"
    assert pack.behaviors["speaking"].cues[0].operation == "head_pose"
    z = [cue.parameters["pose"][2][3] for cue in pack.behaviors["idle"].cues]
    assert pack.behaviors["idle"].period_s == 10.0  # 0.1 Hz breathing
    assert len(z) == 20
    assert z[0] == pytest.approx(0)
    assert max(z) == pytest.approx(0.004)
    assert min(z) == pytest.approx(-0.004)
    assert all(abs(v) <= 0.004 for v in z)
    # All recorded cues remain pinned; never inherit upstream random fallbacks.
    for pattern in pack.behaviors.values():
        for cue in pattern.cues:
            if cue.operation == "play_move":
                assert cue.asset_revision == REV
                assert cue.parameters == {"sound": False}


def test_reachy_operator_can_select_bounded_tilt_speaking_and_listening():
    amplitude = math.radians(3)
    pack = reachy_conversation_pack(
        "reachy", neutral_head=NEUTRAL, recording_revision=REV,
        available_recordings=("yes1", "no1"), speaking_axis="tilt",
        wobble_amplitude_rad=amplitude, listening_tilt_rad=amplitude,
    )
    speaking = pack.behaviors["speaking"].cues
    listening = pack.behaviors["listening"].cues
    assert speaking[0].parameters["pose"][2][1] == pytest.approx(math.sin(amplitude))
    assert speaking[1].parameters["pose"][2][1] == pytest.approx(-math.sin(amplitude))
    assert speaking[2].parameters["pose"] == NEUTRAL
    assert listening[0].parameters["pose"][2][1] == pytest.approx(math.sin(amplitude))
    assert listening[1].parameters["pose"][2][1] == pytest.approx(math.sin(amplitude * 0.75))
    for changes in ({"speaking_axis": "invalid"}, {"listening_tilt_rad": 0.08},
                    {"wobble_amplitude_rad": 0.08}):
        with pytest.raises(ValueError):
            reachy_conversation_pack(
                "reachy", neutral_head=NEUTRAL, recording_revision=REV,
                available_recordings=("yes1", "no1"), **changes,
            )


def test_reachy_listening_hold_has_no_motion_cues():
    pack = reachy_conversation_pack(
        "reachy", neutral_head=NEUTRAL, recording_revision=REV,
        available_recordings=("yes1", "no1"), listening_mode="hold",
    )
    assert pack.behaviors["listening"].mode == "hold"
    assert pack.behaviors["listening"].cues == ()


@pytest.mark.parametrize("bad", [-1, 0.01, float("nan"), float("inf")])
def test_reachy_rejects_unqualified_amplitude(bad):
    with pytest.raises(ValueError):
        reachy_conversation_pack(
            "reachy", neutral_head=NEUTRAL, recording_revision=REV,
            available_recordings=("yes1", "no1"), breath_amplitude_m=bad,
        )


def test_reachy_requires_operator_neutral_and_pinned_catalog():
    with pytest.raises(ValueError):
        reachy_conversation_pack(
            "reachy", neutral_head=[[1]], recording_revision=REV,
            available_recordings=("yes1", "no1"),
        )
    # Missing yes1 is fatal; a filtered no1 now falls back to a bounded
    # authored refusal shake instead of raising.
    with pytest.raises(ValueError):
        reachy_conversation_pack(
            "reachy", neutral_head=NEUTRAL, recording_revision=REV,
            available_recordings=("no1",),
        )
    fallback = reachy_conversation_pack(
        "reachy", neutral_head=NEUTRAL, recording_revision=REV,
        available_recordings=("yes1",),
    )
    assert all(c.operation == "head_pose" for c in fallback.behaviors["no"].cues)
    assert max(abs(c.parameters["pose"][0][2]) for c in fallback.behaviors["no"].cues) <= 0.13
    with pytest.raises(ValueError):
        reachy_conversation_pack(
            "reachy", neutral_head=NEUTRAL, recording_revision="sha256:faked",
            available_recordings=("yes1", "no1"),
        )


def test_stackchan_poses_follow_native_tenths_and_valid_protocol_shape():
    from stackchan_control.assets import _step

    pack = stackchan_conversation_pack("stackchan")
    assert pack.revision == pack.content_revision()
    assert "4g" not in pack.model_dump_json()
    assert pack.behaviors["idle"].cues[1].parameters["pitch"]["angle"] == 450
    assert pack.behaviors["speaking"].cues[1].parameters["mouth"]["size"] == 42
    # yes/no are head nods and shakes, not only facial changes.
    yes_cues = pack.behaviors["yes"].cues
    assert [c.operation for c in yes_cues][1] == "head"
    assert yes_cues[2].parameters["pitch"]["angle"] == 450 + 45
    no_cues = pack.behaviors["no"].cues
    assert no_cues[2].parameters["yaw"]["angle"] == -80
    assert all("yaw" in c.parameters for c in no_cues if c.operation == "head")
    # look left/right move both head and eyes.
    left = pack.behaviors["look_left"].cues
    assert any(c.operation == "head" and c.parameters["yaw"]["angle"] == 80 for c in left)
    assert any(
        c.operation == "face" and c.parameters["left_eye"].get("x") == -35 for c in left
    )
    right = pack.behaviors["look_right"].cues
    assert any(c.operation == "head" and c.parameters["yaw"]["angle"] == -80 for c in right)
    assert any(
        c.operation == "face" and c.parameters["left_eye"].get("x") == 35 for c in right
    )
    assert pack.behaviors["no"].cues[2].parameters["yaw"]["angle"] == -80
    assert pack.behaviors["neutral"].cues[1].parameters["pitch"]["angle"] == 450
    assert {"happy", "curious", "confused", "concerned", "excited", "surprised", "sad"} <= set(
        pack.behaviors
    )
    for name in ("concerned", "excited", "surprised", "sad"):
        assert pack.behaviors[name].mode == "once"
        assert pack.behaviors[name].cues[0].operation == "face"
        assert all(c.operation in {"face", "head"} for c in pack.behaviors[name].cues)
    # Pinned stock BlinkModifier and IdleExpressionModifier operate on eye
    # weight and position; these are deterministic native face-only cues.
    for name in ("blink", "glance_left", "glance_right"):
        assert pack.behaviors[name].mode == "once"
        assert {cue.operation for cue in pack.behaviors[name].cues} == {"face"}
        assert all("mouth" not in cue.parameters for cue in pack.behaviors[name].cues)
    assert pack.behaviors["blink"].cues[0].parameters["left_eye"]["weight"] == 25
    assert pack.behaviors["glance_left"].cues[0].parameters["left_eye"] == {"x": -15, "y": 0}
    assert pack.behaviors["glance_right"].cues[-1].parameters["right_eye"] == {"x": 0, "y": 0}
    for pattern in pack.behaviors.values():
        for cue in pattern.cues:
            step = _step({"at_s": 0.0, "operation": cue.operation,
                          "parameters": cue.parameters})
            assert step.operation == cue.operation


@pytest.mark.parametrize(
    "args", [
        {"pitch_center": 0},
        {"pitch_center": 30, "pitch_amplitude": 40},
        {"yaw_amplitude": 500},
        {"yaw_center": 1250, "yaw_amplitude": 120},
        {"pitch_amplitude": 150},
        {"head_speed": 0},
        {"yaw_center": 0.5},
    ],
)
def test_stackchan_rejects_protocol_or_authoring_envelope(args):
    with pytest.raises(ValueError):
        stackchan_conversation_pack("stackchan", **args)


def _manifest(files):
    return {
        "version": 1,
        "dataset_revision": "a" * 40,
        "sdk_version": "1.10.0rc5",
        "source_url": OFFICIAL_REACHY_DATASET,
        "license": "Apache-2.0",
        "notices": "Pollen Robotics (fixture)",
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
    }


def test_reachy_sync_installs_exact_snapshot_without_in_place_updates(tmp_path):
    files = {
        "yes1.json": b'{"description":"yes","duration":0.4}',
        "yes1.ogg": b"fixture-sound",
        "no1.json": b'{"description":"no","duration":0.4}',
        "subdir/curious1.json": b'{"description":"curious","duration":0.4}',
    }
    manifest = _manifest(files)
    requested = []

    def fetch(url):
        requested.append(url)
        return next(data for name, data in files.items()
                    if url == recording_url(manifest, name))

    dest = tmp_path / "recordings"
    revision = sync_reachy_recordings(manifest, dest, fetch=fetch)
    assert revision.startswith("sha256:")
    assert len(requested) == len(files)
    assert "resolve/" + "a" * 40 in requested[0]
    assert (dest / "yes1.json").read_bytes() == files["yes1.json"]
    assert sync_reachy_recordings(manifest, dest, fetch=lambda _: pytest.fail("unexpected network")) == revision
    (dest / "yes1.json").write_bytes(b"tampered")
    with pytest.raises(ValueError):
        sync_reachy_recordings(manifest, dest, fetch=lambda _: pytest.fail("should not redownload"))


def test_reachy_sync_failed_import_is_not_partial_or_auto_retried(tmp_path):
    files = {"yes1.json": b"original", "no1.json": b"other"}
    manifest = _manifest(files)
    dest = tmp_path / "recordings"

    def corrupted(url):
        if url.endswith("/no1.json?download=true"):
            return b"tampered"
        return files["yes1.json"]

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        sync_reachy_recordings(manifest, dest, fetch=corrupted)
    assert not dest.exists()
    assert not list(tmp_path.glob(".*partial-*"))
    assert not list(tmp_path.glob(".*install.lock"))


def test_reachy_sync_rejects_wrong_source_and_traversal_before_network(tmp_path):
    files = {"yes1.json": b"x"}
    manifest = _manifest(files)
    forbidden = manifest | {"source_url": "https://example.com/"}
    with pytest.raises(ValueError, match="official"):
        sync_reachy_recordings(forbidden, tmp_path / "assets")
    malicious = _manifest({"../escape": b"x"})
    with pytest.raises(ValueError, match="path"):
        sync_reachy_recordings(malicious, tmp_path / "assets")
    invalid_revision = manifest | {"dataset_revision": "latest"}
    with pytest.raises(ValueError, match="commit"):
        sync_reachy_recordings(invalid_revision, tmp_path / "assets")


def test_checked_in_reachy_dataset_lock_contains_native_yes_no_and_license():
    path = Path(__file__).parents[2] / "examples/robots/reachy/emotions.manifest.json"
    manifest = json.loads(path.read_text())
    assert manifest["source_url"] == OFFICIAL_REACHY_DATASET
    assert manifest["sdk_version"] == "1.10.0rc5"
    assert len(manifest["dataset_revision"]) == 40
    assert "Apache" in manifest["license"]
    assert "yes1.json" in manifest["files"]
    assert "no1.json" in manifest["files"]
    for name in manifest["files"]:
        assert "/resolve/" + manifest["dataset_revision"] in recording_url(manifest, name)


def test_stackchan_native_weight_speech_option_uses_stock_mouth_primitive():
    from stackchan_control.assets import _step

    # The existing size-based profile is backward compatible; native weight
    # is opt-in for a reviewed performer configuration.
    legacy = stackchan_conversation_pack("stackchan")
    native = stackchan_conversation_pack("stackchan", speaking_mode="native_weight")
    assert legacy.behaviors["speaking"].cues[1].parameters["mouth"] == {"size": 42}
    speaking = native.behaviors["speaking"]
    assert speaking.mode == "loop"
    assert [cue.parameters["mouth"]["weight"] for cue in speaking.cues] == [
        45, 75, 55, 0,
    ]
    assert all(cue.operation == "face" for cue in speaking.cues)
    for cue in speaking.cues:
        _step({"at_s": 0.0, "operation": cue.operation, "parameters": cue.parameters})
    with pytest.raises(ValueError, match="speaking mode"):
        stackchan_conversation_pack("stackchan", speaking_mode="unapproved")


def test_native_weight_resting_states_close_the_mouth_after_interrupted_speech():
    # An interrupted speaking loop can leave mouth weight non-zero. Resting
    # states must explicitly return it to closed so the face settles neutral.
    native = stackchan_conversation_pack("stackchan", speaking_mode="native_weight")
    for name in ("idle", "listening", "neutral"):
        face_cues = [c for c in native.behaviors[name].cues if c.operation == "face"]
        assert face_cues, name
        assert all(c.parameters["mouth"]["weight"] == 0 for c in face_cues), name
        assert all(c.parameters["mouth"]["size"] == 0 for c in face_cues), name

    # The legacy size profile does not use the weight primitive at all.
    legacy = stackchan_conversation_pack("stackchan")
    for name in ("idle", "listening", "neutral"):
        face_cues = [c for c in legacy.behaviors[name].cues if c.operation == "face"]
        assert all("weight" not in c.parameters.get("mouth", {}) for c in face_cues), name


def test_recorded_replay_envelope_rejects_fast_head_shakes():
    from forge_puppeteer.conversation_native import (
        recorded_move_envelope,
        recorded_move_is_safe,
    )

    class Move:
        def __init__(self, duration, yaw_at):
            self.duration = duration
            self._yaw_at = yaw_at

        def evaluate(self, t):
            yaw = self._yaw_at(t)
            import math
            c, s = math.cos(yaw), math.sin(yaw)
            return ([[c, -s, 0.0, 0.0], [s, c, 0.0, 0.0],
                     [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]], None, 0.0)

    import math
    # no1-style shake: +/-25 deg flipping in ~0.25 s.
    fast = recorded_move_envelope(Move(2.0, lambda t: math.radians(25) * (1 if int(t / 0.25) % 2 else -1)))
    assert fast["yaw_deg"] > 20 and fast["yaw_rate_deg_s"] > 120
    assert not recorded_move_is_safe(fast)
    # A small, slow gesture stays inside the envelope.
    slow = recorded_move_envelope(Move(2.0, lambda t: math.radians(8) * math.sin(t)))
    assert recorded_move_is_safe(slow)


def test_stackchan_recorded_assets_become_pinned_expressions(tmp_path):
    import json

    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "wave.json").write_text(json.dumps({
        "schema_version": 1,
        "id": "wave",
        "kind": "pose",
        "steps": [{"at_s": 0, "operation": "head",
                   "parameters": {"yaw": {"angle": 200, "speed": 200}}}],
    }))
    recorded = stackchan_recorded_assets(assets)
    assert [name for name, _id, _rev in recorded] == ["wave"]
    pack = stackchan_conversation_pack("stackchan", recorded_assets=recorded)
    pattern = pack.behaviors["wave"]
    assert pattern.source == "recorded" and pattern.mode == "once"
    cue = pattern.cues[0]
    assert cue.operation == "play_asset" and cue.asset_id == "wave"
    assert cue.asset_revision.startswith("sha256:")


@pytest.mark.anyio
async def test_speech_channel_drives_speaking_without_changing_other_slots():
    """Audio-driven targets apply only to an active speaking slot."""

    class Channel:
        def latest(self):
            return {"yaw": 0.5, "pitch": -0.5, "roll": 0.0}

    class Provider:
        def __init__(self):
            self.calls = []

        async def validate(self, request):
            if request.operation == "head" and "yaw" not in request.parameters:
                raise ValueError("incomplete head target")

        async def execute(self, request):
            from forge_puppeteer.execution import Completion
            self.calls.append(request.operation)
            return Completion("ok")

        def resources_for(self, request):
            return ("head.pose",)

        async def stop(self):
            from forge_puppeteer.execution import StopResult
            return StopResult(True, "stopped")

    provider = Provider()
    runtime = ExecutionRuntime(PerformanceStage())
    runtime.register("stackchan", provider)
    pack = stackchan_conversation_pack("stackchan")
    ensemble = ConversationEnsemble(runtime, [pack])
    ensemble.bind_speech_channel("stackchan", Channel(), amplitude=0.08)

    def presence(seq, state):
        return EnsemblePresence(
            event_id=f"event-{seq}", session_id="one-4g-room",
            sequence=seq, state=state, source="agent_state",
        )
    async with runtime.run(), ensemble.run():
        await ensemble.observe(presence(0, "speaking"))
        await anyio.sleep(0.35)
        assert "head" in provider.calls
        await ensemble.observe(presence(1, "listening"))
        await anyio.sleep(0.05)
