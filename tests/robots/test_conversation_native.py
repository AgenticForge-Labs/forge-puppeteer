"""Native behavior authoring is SDK-validated, persona-agnostic and revisioned."""

import hashlib
import json
import math
from itertools import pairwise
from pathlib import Path

import anyio
import pytest

from forge_puppeteer.conversation_ensemble import ConversationEnsemble, EnsemblePresence
from forge_puppeteer.conversation_native import (
    reachy_conversation_pack,
    stackchan_conversation_pack,
    stackchan_firmware_warp,
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
    idle = pack.behaviors["idle"]
    assert idle.period_s == 10.0  # 0.1 Hz breathing
    # One breath per period, sampled at the spacing the shared runtime can
    # actually deliver: cues offered faster than that are dropped and read as
    # a jittery head rather than a breath.
    from forge_puppeteer.conversation_native import IDLE_BREATH_SAMPLE_S
    assert len(z) == round(10.0 / IDLE_BREATH_SAMPLE_S)
    offsets = [cue.at_s for cue in idle.cues]
    assert offsets == sorted(offsets)
    assert all(
        b - a == pytest.approx(IDLE_BREATH_SAMPLE_S) for a, b in pairwise(offsets)
    )
    assert z[0] == pytest.approx(0)
    assert max(z) == pytest.approx(0.004, abs=1e-3)
    assert min(z) == pytest.approx(-0.004, abs=1e-3)
    assert all(abs(v) <= 0.004 + 1e-9 for v in z)
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
    # Pitch is a rotation about y under the daemon's readback convention,
    # so a tilt lands in matrix element [0][2], not [2][1].
    assert speaking[0].parameters["pose"][0][2] == pytest.approx(math.sin(amplitude))
    assert speaking[1].parameters["pose"][0][2] == pytest.approx(-math.sin(amplitude))
    assert speaking[2].parameters["pose"] == NEUTRAL
    assert listening[0].parameters["pose"][0][2] == pytest.approx(math.sin(amplitude))
    assert listening[1].parameters["pose"][0][2] == pytest.approx(math.sin(amplitude * 0.75))
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
    ensemble.bind_speech_channel(
        "stackchan", Channel(), amplitude=0.08,
        warp=stackchan_firmware_warp(seed=7), interval_s=0.05,
    )

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


def test_head_axes_match_the_daemon_readback_convention():
    """The daemon reads a head matrix with as_euler("xyz") = (roll, pitch, yaw).

    A wrong axis order still yields the right rotation magnitude, so only an
    axis-aware assertion catches it.
    """
    from scipy.spatial.transform import Rotation

    from forge_puppeteer.conversation_native import _head

    identity = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0],
                [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]

    def euler(matrix):
        roll, pitch, yaw = Rotation.from_matrix(
            [row[:3] for row in matrix[:3]]
        ).as_euler("xyz")
        return (math.degrees(roll), math.degrees(pitch), math.degrees(yaw))

    for name, kwargs, axis in (
        ("roll", {"roll_rad": math.radians(10)}, 0),
        ("pitch", {"pitch_rad": math.radians(10)}, 1),
        ("yaw", {"yaw_rad": math.radians(10)}, 2),
    ):
        angles = euler(_head(identity, **kwargs))
        assert abs(angles[axis] - 10.0) < 0.01, (name, angles)
        others = [abs(a) for i, a in enumerate(angles) if i != axis]
        assert max(others) < 0.01, (name, angles)


def test_stackchan_speaking_warp_uses_firmware_style_targets():
    """StackChan keeps its own character: small nods and sways, not a wobble."""
    warp = stackchan_firmware_warp(seed=5)
    cues = [warp({"pitch": 0.5, "yaw": 0.3}, 0.08) for _ in range(40)]
    assert all(cue.operation == "head" for cue in cues)
    yaws = [c.parameters["yaw"]["angle"] for c in cues]
    pitches = [c.parameters["pitch"]["angle"] for c in cues]
    speeds = [c.parameters["yaw"]["speed"] for c in cues]
    # Centred on the configured rest pose, never a large excursion.
    assert max(abs(y) for y in yaws) <= 40
    assert max(p for p in pitches) <= 500 and min(pitches) >= 430
    assert all(100 <= s <= 200 for s in speeds)
    # And it varies rather than repeating one target.
    assert len(set(zip(yaws, pitches, strict=True))) > 5


@pytest.mark.anyio
async def test_two_speaking_performers_do_not_contend_for_one_runtime():
    """Reachy's 250 ms hop and StackChan's 2 s drift share one runtime.

    Without a cross-performer gate, the faster channel submits while the
    slower performer's target is still executing. The gate serializes submit
    and execution, so active executions never exceed one.
    """

    class Channel:
        def latest(self):
            return {"yaw": 0.2, "pitch": 0.2, "roll": 0.0, "z": 0.0, "antenna": 0.0}

    shared = {"active": 0, "peak": 0}

    class SlowProvider:
        """Records *cross-performer* concurrency, the property the gate owns."""

        def __init__(self):
            self.calls = []

        async def validate(self, request):
            return None

        async def execute(self, request):
            from forge_puppeteer.execution import Completion
            shared["active"] += 1
            shared["peak"] = max(shared["peak"], shared["active"])
            self.calls.append(request.operation)
            try:
                await anyio.sleep(0.12)
                return Completion("ok")
            finally:
                shared["active"] -= 1

        def resources_for(self, request):
            return ("head.pose",)

        async def stop(self):
            from forge_puppeteer.execution import StopResult
            return StopResult(True, "stopped")

    reachy_provider = SlowProvider()
    stack_provider = SlowProvider()
    runtime = ExecutionRuntime(PerformanceStage())
    runtime.register("reachy", reachy_provider)
    runtime.register("stackchan", stack_provider)
    packs = (
        reachy_conversation_pack(
            "reachy", neutral_head=NEUTRAL, recording_revision=REV,
            available_recordings=("yes1", "no1"), speaking_axis="tilt",
        ),
        stackchan_conversation_pack("stackchan"),
    )
    ensemble = ConversationEnsemble(runtime, packs)
    ensemble.bind_speech_channel(
        "reachy", Channel(), amplitude=0.05, neutral_head=NEUTRAL, interval_s=0.02,
    )
    ensemble.bind_speech_channel(
        "stackchan", Channel(), amplitude=0.08,
        warp=stackchan_firmware_warp(seed=7), interval_s=0.02,
    )
    async with runtime.run(), ensemble.run():
        await ensemble.observe(EnsemblePresence(
            event_id="event-0", session_id="one-4g-room", sequence=0,
            state="speaking", source="agent_state",
        ))
        await anyio.sleep(0.6)
        await ensemble.observe(EnsemblePresence(
            event_id="event-1", session_id="one-4g-room", sequence=1,
            state="listening", source="agent_state",
        ))
        await anyio.sleep(0.05)
    # Both performers moved, and never at the same time on the shared runtime.
    assert reachy_provider.calls and stack_provider.calls
    assert shared["peak"] == 1, f"two performers executed concurrently: {shared}"


@pytest.mark.anyio
async def test_attributed_envelope_feeds_only_its_own_performer():
    """A batch attributed to one robot must not move the other."""
    from forge_puppeteer.speech_expression import SpeechEnvelopeWindow

    window = SpeechEnvelopeWindow()
    frames = [
        {
            "schema": "forge.speech_envelope.v1", "stream_id": "tts-1",
            "sequence": i, "offset_ms": i * 50, "duration_ms": 50,
            "active": True, "intensity": 0.8, "rms_dbfs": -16.0,
            "timing_source": "generated_pcm",
            "acoustic_playout_measured": False,
        }
        for i in range(4)
    ]
    window.accept(frames, performer_ids=("reachy",))
    assert window.latest("reachy") is not None
    assert window.latest("stackchan") is None


@pytest.mark.anyio
async def test_first_speaking_waits_for_startup_to_settle():
    """The opening greeting must not talk over the rise to neutral."""

    class Channel:
        def latest(self):
            return {"yaw": 0.4, "pitch": 0.4, "roll": 0.0, "z": 0.0, "antenna": 0.0}

    class Provider:
        def __init__(self):
            self.calls = []

        async def validate(self, request):
            return None

        async def execute(self, request):
            from forge_puppeteer.execution import Completion
            self.calls.append((request.operation, request.cue_id))
            return Completion("ok")

        def resources_for(self, request):
            return ("head.pose",)

        async def stop(self):
            from forge_puppeteer.execution import StopResult
            return StopResult(True, "stopped")

    provider = Provider()
    runtime = ExecutionRuntime(PerformanceStage())
    runtime.register("reachy", provider)
    pack = reachy_conversation_pack(
        "reachy", neutral_head=NEUTRAL, recording_revision=REV,
        available_recordings=("yes1", "no1"),
        wake_up_if_sleeping=True,
    )
    ensemble = ConversationEnsemble(runtime, [pack], startup_settle_s=0.5)
    ensemble.bind_speech_channel(
        "reachy", Channel(), amplitude=0.12, neutral_head=NEUTRAL, interval_s=0.05,
    )

    def presence(seq, state):
        return EnsemblePresence(
            event_id=f"event-{seq}", session_id="gate-room",
            sequence=seq, state=state, source="agent_state",
        )

    async with runtime.run(), ensemble.run():
        await ensemble.observe(presence(0, "speaking"))
        # Startup owns the robot first; speaking must not dispatch yet.
        await anyio.sleep(0.15)
        startup_cues = [c for c in provider.calls if (c[1] or "").startswith("startup")]
        speaking_cues = [c for c in provider.calls if (c[1] or "").startswith("speaking")]
        assert startup_cues, "startup motion should run first"
        assert not speaking_cues, f"spoke before startup settled: {speaking_cues}"
        # After the settle window the speaking slot is allowed to run.
        await anyio.sleep(0.7)
        assert any((c[1] or "").startswith("speaking") for c in provider.calls)
        await ensemble.observe(presence(1, "listening"))
        await anyio.sleep(0.05)


@pytest.mark.anyio
async def test_transition_after_speech_never_quarantines_performer():
    """A normal presence transition must not block the robot.

    The speech loop used to never signal completion, so every transition
    waited out the stop timeout and then quarantined a healthy performer.
    Idle then never ran again and only one speaking target was ever seen.
    """

    class Channel:
        def latest(self):
            return {"yaw": 0.3, "pitch": 0.3, "roll": 0.0, "z": 0.0, "antenna": 0.0}

    class Provider:
        def __init__(self):
            self.calls = []

        async def validate(self, request):
            return None

        async def execute(self, request):
            from forge_puppeteer.execution import Completion
            self.calls.append(request.cue_id)
            return Completion("ok")

        def resources_for(self, request):
            return ("head.pose", "antennas.left", "antennas.right", "body.yaw")

        async def stop(self):
            from forge_puppeteer.execution import StopResult
            return StopResult(True, "stopped")

    provider = Provider()
    runtime = ExecutionRuntime(PerformanceStage())
    runtime.register("reachy", provider)
    pack = reachy_conversation_pack(
        "reachy", neutral_head=NEUTRAL, recording_revision=REV,
        available_recordings=("yes1", "no1"),
    )
    ensemble = ConversationEnsemble(runtime, [pack])
    ensemble.bind_speech_channel(
        "reachy", Channel(), amplitude=0.12, neutral_head=NEUTRAL, interval_s=0.02,
    )

    def presence(seq, state):
        return EnsemblePresence(
            event_id=f"event-{seq}", session_id="transition-room",
            sequence=seq, state=state, source="agent_state",
        )

    async with runtime.run(), ensemble.run():
        for seq, state in enumerate(("listening", "idle", "speaking", "idle")):
            result = await ensemble.observe(presence(seq, state))
            # Long enough for a cue to land even when the idle sample spacing
            # is the full measured 1.6 s.
            await anyio.sleep(2.0)
            # observe() returns an admission-time snapshot. Read the actual
            # post-transition state after dispatch; otherwise this assertion
            # can miss asynchronous STOP/quarantine outcomes.
            assert result["outcome"] == "accepted"
            status = ensemble.snapshot()["performers"]["reachy"]["status"]
            assert status != "blocked", f"{state} quarantined the performer"
        cues = [cue.split(":")[0] for cue in provider.calls]
        assert cues.count("idle") >= 2, f"idle did not resume: {cues}"
        assert cues.count("speaking") >= 1, f"no speech target ran: {cues}"


def test_idle_breath_loop_does_not_snap_the_head():
    """The 10 s idle loop must end where it started.

    A sine sampled at the achievable target spacing never lands back on
    neutral, so wrapping the loop snapped the head by most of the breath
    amplitude every cycle. That read as a twitch rather than breathing.
    """
    pack = reachy_conversation_pack(
        "reachy", neutral_head=NEUTRAL, recording_revision=REV,
        available_recordings=("yes1", "no1"), breath_amplitude_m=0.004,
    )
    idle = pack.behaviors["idle"]
    z = [cue.parameters["pose"][2][3] for cue in idle.cues]
    assert z[0] == pytest.approx(0.0)
    assert z[-1] == pytest.approx(0.0)
    # The breath still actually moves within the cycle.
    assert min(z) < -0.002 < 0.002 < max(z)
    offsets = [cue.at_s for cue in idle.cues]
    assert all(b - a < idle.period_s for a, b in pairwise(offsets))


@pytest.mark.anyio
async def test_a_conversation_keeps_producing_speech_targets_turn_after_turn():
    """Speech motion must resume on every speaking turn, not just the first.

    The live journal showed speech targets only during the opening greeting and
    nothing afterwards, even though presence kept alternating. Each turn must
    start a fresh speech slot that dispatches while the envelope is fresh.
    """

    class Channel:
        def latest(self):
            return {"yaw": 0.4, "pitch": 0.2, "roll": 0.0, "z": 0.0, "antenna": 0.0}

    class Provider:
        def __init__(self):
            self.calls = []

        async def validate(self, request):
            return None

        async def execute(self, request):
            from forge_puppeteer.execution import Completion
            self.calls.append(request.cue_id)
            return Completion("ok")

        def resources_for(self, request):
            return ("head.pose", "antennas.left", "antennas.right", "body.yaw")

        async def stop(self):
            from forge_puppeteer.execution import StopResult
            return StopResult(True, "stopped")

    provider = Provider()
    runtime = ExecutionRuntime(PerformanceStage())
    runtime.register("reachy", provider)
    pack = reachy_conversation_pack(
        "reachy", neutral_head=NEUTRAL, recording_revision=REV,
        available_recordings=("yes1", "no1"),
    )
    ensemble = ConversationEnsemble(runtime, [pack])
    ensemble.bind_speech_channel(
        "reachy", Channel(), amplitude=0.12, neutral_head=NEUTRAL, interval_s=0.05,
    )

    def presence(seq, state):
        return EnsemblePresence(
            event_id=f"event-{seq}", session_id="conversation-room",
            sequence=seq, state=state, source="agent_state",
        )

    async with runtime.run(), ensemble.run():
        seq = 0
        for _turn in range(3):
            for state in ("listening", "speaking"):
                result = await ensemble.observe(presence(seq, state))
                seq += 1
                await anyio.sleep(0.45)
                assert result["performers"]["reachy"]["status"] != "blocked"
        cues = [cue.split(":")[0] for cue in provider.calls]
        # One speech slot per turn, and each delivers more than one target.
        assert cues.count("speaking") >= 6, f"speech stopped after a turn: {cues}"


@pytest.mark.parametrize("amplitude,interval", [
    (float("nan"), 0.25), (float("inf"), 0.25),
    (-0.1, 0.25), (1.1, 0.25),
    (0.1, float("nan")), (0.1, 0.0),
    (0.1, 6.0),
])
def test_speech_channel_rejects_unsafe_operator_budgets(amplitude, interval):
    runtime = ExecutionRuntime(PerformanceStage())
    pack = reachy_conversation_pack(
        "reachy", neutral_head=NEUTRAL, recording_revision=REV,
        available_recordings=("yes1", "no1"),
    )
    ensemble = ConversationEnsemble(runtime, [pack])
    with pytest.raises(ValueError):
        ensemble.bind_speech_channel(
            "reachy", object(), amplitude=amplitude,
            neutral_head=NEUTRAL, interval_s=interval,
        )


@pytest.mark.anyio
async def test_cancelled_speech_runtime_requires_confirmed_stop():
    class Channel:
        def latest(self):
            return {"yaw": 0.3, "pitch": 0.1, "roll": 0, "z": 0,
                    "antenna": 0, "active": True}

    class SlowProvider:
        def __init__(self):
            self.started = anyio.Event()
            self.stop_calls = 0
            self.executed = 0

        async def validate(self, _request):
            return None

        def resources_for(self, _request):
            return ("head.pose",)

        async def execute(self, _request):
            self.executed += 1
            self.started.set()
            await anyio.sleep(10)
            from forge_puppeteer.execution import Completion
            return Completion("completed")

        async def stop(self):
            self.stop_calls += 1
            from forge_puppeteer.execution import StopResult
            return StopResult(False, "stop_outcome_unknown")

    provider = SlowProvider()
    runtime = ExecutionRuntime(PerformanceStage())
    runtime.register("reachy", provider)
    pack = reachy_conversation_pack(
        "reachy", neutral_head=NEUTRAL, recording_revision=REV,
        available_recordings=("yes1", "no1"),
    )
    ensemble = ConversationEnsemble(runtime, [pack], stop_timeout_s=0.25)
    ensemble.bind_speech_channel(
        "reachy", Channel(), amplitude=0.12,
        neutral_head=NEUTRAL, interval_s=0.02,
    )

    def presence(sequence, state, interrupted=False):
        return EnsemblePresence(
            event_id=f"safety-{sequence}", session_id="stop-safety",
            sequence=sequence, state=state, source="agent_state",
            interrupted=interrupted,
        )

    async with runtime.run(), ensemble.run():
        await ensemble.observe(presence(0, "speaking"))
        with anyio.fail_after(2):
            await provider.started.wait()
        result = await ensemble.observe(presence(1, "listening", interrupted=True))
        assert result["performers"]["reachy"]["status"] == "blocked"
        assert provider.stop_calls >= 1
        await ensemble.observe(presence(2, "idle"))
        await anyio.sleep(0.03)
        assert provider.executed == 1


@pytest.mark.anyio
async def test_speech_does_not_reissue_same_hop_or_animate_inactive_hop():
    class Channel:
        value = {"stream_id": "one", "sequence": 0, "active": True,
                 "yaw": 0.3, "pitch": 0, "roll": 0, "z": 0, "antenna": 0}

        def latest(self):
            return dict(self.value)

    class Provider:
        def __init__(self):
            self.calls = []

        async def validate(self, _request):
            return None

        def resources_for(self, _request):
            return ("head.pose",)

        async def execute(self, request):
            self.calls.append(request.cue_id)
            from forge_puppeteer.execution import Completion
            return Completion("completed")

        async def stop(self):
            from forge_puppeteer.execution import StopResult
            return StopResult(True, "stopped")

    channel = Channel()
    provider = Provider()
    runtime = ExecutionRuntime(PerformanceStage())
    runtime.register("reachy", provider)
    pack = reachy_conversation_pack(
        "reachy", neutral_head=NEUTRAL, recording_revision=REV,
        available_recordings=("yes1", "no1"),
    )
    ensemble = ConversationEnsemble(runtime, [pack])
    ensemble.bind_speech_channel(
        "reachy", channel, amplitude=0.12,
        neutral_head=NEUTRAL, interval_s=0.02,
    )

    async with runtime.run(), ensemble.run():
        await ensemble.observe(EnsemblePresence(
            event_id="start", session_id="room", sequence=0,
            state="speaking", source="agent_state",
        ))
        with anyio.move_on_after(2):
            while len(provider.calls) < 1:
                await anyio.sleep(0.01)
        assert provider.calls, (
            f"no speech dispatch: {ensemble.snapshot()} "
            f"runtime_inhibited={runtime._inhibited} "
            f"entries={[(key, value.record.state, value.record.error) for key, value in runtime._entries.items()]}"
        )
        await anyio.sleep(0.12)
        assert len(provider.calls) == 1
        channel.value["sequence"] = 1
        channel.value["active"] = False
        await anyio.sleep(0.1)
        assert len(provider.calls) == 1
        channel.value["sequence"] = 2
        channel.value["active"] = True
        await anyio.sleep(0.1)
        assert len(provider.calls) == 2
