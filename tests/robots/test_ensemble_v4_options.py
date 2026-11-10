"""Schema-v4 Reachy/StackChan behavior options: validation, pack building, launcher wiring."""
import json

import pytest

from forge_puppeteer.behavior_contract import BehaviorCue, BehaviorPattern, PerformerBehaviors
from forge_puppeteer.mcp import ensemble_options as opts
from forge_puppeteer.mcp.ensemble_launcher import config_readiness
from forge_puppeteer.mcp.ensemble_options import (
    ReachyOptions,
    StackChanOptions,
    build_reachy_behavior,
    check_reachy_options,
    parse_options,
)

IDENTITY = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
TOOLS = ["head_pose", "play_move", "device_stop"]
EXPRESSIVE_TOOLS = [*TOOLS, "pose_and_antennas"]


def test_defaults_match_the_classic_reachy_and_legacy_stackchan_behavior():
    reachy = ReachyOptions()
    assert (reachy.expression_style, reachy.idle, reachy.speaking) == ("classic", "default", "default")
    assert reachy.startup_behavior == "none" and not reachy.speech_driven
    assert StackChanOptions().speaking_mode == "legacy_size"


@pytest.mark.parametrize("bad", [
    {"speaking": "smooth_v2"},                                              # needs speech_driven
    {"legacy_speech_fallback": True},                                       # needs smooth_v2 + speech_driven
    {"legacy_speech_fallback": True, "speaking": "smooth_v2", "speech_driven": True,
     "expression_style": "expressive"},                                     # classic only
    {"idle": "native_v1"}, {"speaking": "original_v1"},                     # dropped profiles
    {"neutral_antennas_rad": [float("inf"), 0]}, {"speech_driven": "yes"}, {"unknown_key": 1},
])
def test_invalid_reachy_option_sets_are_rejected(bad):
    with pytest.raises(ValueError):
        ReachyOptions.model_validate(bad)


def test_parse_options_per_kind():
    assert parse_options("reachy", None) == ReachyOptions()
    assert parse_options("stackchan", {"speaking_mode": "native_weight"}).speaking_mode == "native_weight"
    assert parse_options("soarm101", None) is None
    with pytest.raises(ValueError, match="takes no options"):
        parse_options("soarm101", {"anything": 1})
    with pytest.raises(ValueError):
        parse_options("reachy", ["not", "an", "object"])


def _neutral(tmp_path, matrix=IDENTITY):
    path = tmp_path / "neutral.json"
    path.write_text(json.dumps(matrix))
    return str(path)


def test_check_reachy_options_against_grants(tmp_path):
    neutral = _neutral(tmp_path)
    check_reachy_options(ReachyOptions(), TOOLS, False, neutral)
    with pytest.raises(ValueError, match="normal native SDK mode"):
        check_reachy_options(ReachyOptions(), TOOLS, True, neutral)
    with pytest.raises(ValueError, match="wake_up"):
        check_reachy_options(ReachyOptions(startup_behavior="wake_up_if_sleeping"), TOOLS, False, neutral)
    check_reachy_options(ReachyOptions(startup_behavior="wake_up_if_sleeping"), [*TOOLS, "wake_up"], False, neutral)
    smooth = ReachyOptions(speaking="smooth_v2", speech_driven=True, neutral_antennas_rad=(-0.17, 0.17))
    with pytest.raises(ValueError, match="pose_and_antennas"):
        check_reachy_options(smooth, TOOLS, False, neutral)
    check_reachy_options(smooth, EXPRESSIVE_TOOLS, False, neutral)
    with pytest.raises(ValueError, match="neutral_antennas_rad"):
        check_reachy_options(ReachyOptions(expression_style="expressive"), EXPRESSIVE_TOOLS, False, neutral)
    with pytest.raises(ValueError, match="adapter bounds"):
        check_reachy_options(ReachyOptions(expression_style="expressive", neutral_antennas_rad=(9.0, 0)),
                             EXPRESSIVE_TOOLS, False, neutral)


def test_original_idle_needs_its_grant_and_the_identity_neutral(tmp_path):
    original = ReachyOptions(idle="original_v1")
    with pytest.raises(ValueError, match="original_idle"):
        check_reachy_options(original, TOOLS, False, _neutral(tmp_path))
    check_reachy_options(original, [*TOOLS, "original_idle"], False, _neutral(tmp_path))
    shifted = [row[:] for row in IDENTITY]
    shifted[0][3] = 0.01
    with pytest.raises(ValueError, match="identity neutral"):
        check_reachy_options(original, [*TOOLS, "original_idle"], False, _neutral(tmp_path, shifted))


def _fake_pack(monkeypatch):
    """Stand in for the SDK-backed catalog: the pack records the style it was asked for."""
    import forge_puppeteer.conversation_native as native
    calls = []

    async def fake(performer_id, catalog, **kwargs):
        calls.append(kwargs)
        speaking = BehaviorPattern(source="procedural", mode="loop", period_s=1, cues=(
            BehaviorCue(at_s=0, operation="head_pose", parameters={"style": kwargs["expression_style"]}),))
        idle = BehaviorPattern(source="procedural", mode="loop", period_s=1, cues=(
            BehaviorCue(at_s=0, operation="head_pose"),))
        return PerformerBehaviors.author(performer_id, {"idle": idle, "listening": idle, "speaking": speaking})

    monkeypatch.setattr(native, "reachy_pack_from_catalog", fake)
    return calls


@pytest.mark.anyio
async def test_default_options_build_one_classic_pack_with_no_overrides(monkeypatch):
    calls = _fake_pack(monkeypatch)
    behavior = await build_reachy_behavior("reachy", object(), ReachyOptions(), IDENTITY)
    assert len(calls) == 1 and calls[0]["expression_style"] == "classic"
    assert calls[0]["wobble_amplitude_rad"] == 0.025 and not calls[0]["wake_up_if_sleeping"]
    assert behavior.motion_patterns == {} and behavior.speech_dynamics is None
    assert behavior.speech_fallback is None


@pytest.mark.anyio
async def test_the_current_reachy_setup_original_idle_smooth_speaking_and_fallback(monkeypatch):
    calls = _fake_pack(monkeypatch)
    options = ReachyOptions(
        startup_behavior="wake_up_if_sleeping", idle="original_v1", speaking="smooth_v2",
        speech_driven=True, legacy_speech_fallback=True, neutral_antennas_rad=(-0.17, 0.17))
    behavior = await build_reachy_behavior("reachy", object(), options, IDENTITY)
    assert [c["expression_style"] for c in calls] == ["classic", "expressive"]
    assert calls[0]["wake_up_if_sleeping"] is True
    idle = behavior.motion_patterns["idle"].behaviors["idle"]
    assert [c.operation for c in idle.cues] == ["original_idle"]
    # smooth speaking uses the expressive pack; the classic speaking pattern is the stale-feature fallback.
    speaking = behavior.motion_patterns["speaking"].behaviors["speaking"]
    assert speaking.cues[0].parameters["style"] == "expressive"
    assert behavior.speech_fallback.cues[0].parameters["style"] == "classic"
    assert behavior.speech_dynamics == "smoothed_v1"


@pytest.mark.anyio
async def test_expressive_style_reuses_its_pack_for_smooth_speaking(monkeypatch):
    calls = _fake_pack(monkeypatch)
    options = ReachyOptions(expression_style="expressive", speaking="smooth_v2", speech_driven=True,
                            neutral_antennas_rad=(-0.17, 0.17), expressive_amplitude_rad=0.1)
    behavior = await build_reachy_behavior("reachy", object(), options, IDENTITY)
    assert len(calls) == 1 and calls[0]["wobble_amplitude_rad"] == 0.1
    assert behavior.motion_patterns["speaking"] is not None and behavior.speech_fallback is None


def test_stackchan_native_weight_speaking_is_a_distinct_authored_pack():
    legacy = opts.build_stackchan_pack("stackchan", StackChanOptions())
    native = opts.build_stackchan_pack("stackchan", StackChanOptions(speaking_mode="native_weight"))
    assert legacy.behaviors["speaking"] != native.behaviors["speaking"]


# --- config files -> launcher wiring -------------------------------------------------

def _write_room(tmp_path, reachy_options=None, stackchan_options=None, *, reachy_tools=None):
    from tests.robots.test_ensemble_v4_config import provider  # reuse the provider fixtures

    reachy = provider("reachy", "reachy", tmp_path)
    reachy["allowed_tools"] = reachy_tools or [*TOOLS, "wake_up", "original_idle", "pose_and_antennas"]
    stackchan = provider("stackchan", "stackchan", tmp_path)
    for kind, body, extra in (("reachy", reachy, reachy_options), ("stackchan", stackchan, stackchan_options)):
        data = {"kind": kind, "id": kind, "provider": body}
        if extra is not None:
            data["options"] = extra
        (tmp_path / f"{kind}.json").write_text(json.dumps(data))
    room = {"schema_version": 4, "physical": True,
            "robots": [{"kind": k, "id": k, "file": f"{k}.json"} for k in ("reachy", "stackchan")],
            "action_journal": str(tmp_path / "actions.db"), "ownership_db": str(tmp_path / "owners.db"),
            "reachy_neutral_head": _neutral(tmp_path)}
    path = tmp_path / "ensemble.json"
    path.write_text(json.dumps(room))
    return path


def test_options_in_robot_files_are_validated_when_the_config_loads(tmp_path):
    good = _write_room(tmp_path, {"idle": "original_v1", "startup_behavior": "wake_up_if_sleeping"},
                       {"speaking_mode": "native_weight", "speech_driven": True})
    assert config_readiness(good)["performers"] == ["reachy", "stackchan"]
    with pytest.raises(ValueError, match="smooth_v2 requires speech_driven"):
        config_readiness(_write_room(tmp_path, {"speaking": "smooth_v2"}))
    with pytest.raises(ValueError, match="original_idle"):
        config_readiness(_write_room(tmp_path, {"idle": "original_v1"}, reachy_tools=TOOLS))
    with pytest.raises(ValueError):
        config_readiness(_write_room(tmp_path, None, {"speaking_mode": "loud"}))


def test_build_v4_returns_motion_patterns_and_speech_server_kwargs(tmp_path, monkeypatch):
    from forge_puppeteer.mcp import ensemble_v4

    calls = _fake_pack(monkeypatch)
    path = _write_room(
        tmp_path,
        {"idle": "original_v1", "speaking": "smooth_v2", "speech_driven": True,
         "legacy_speech_fallback": True, "neutral_antennas_rad": [-0.17, 0.17]},
        {"speaking_mode": "native_weight", "speech_driven": True})
    room, robots, options = ensemble_v4.load_v4(path)

    class FakeReachy:
        catalog = object()

        def __init__(self, config):
            self.config = config

        def connected(self):
            raise AssertionError("must not connect")

    class FakeStackChan:
        def __init__(self, config):
            self.config = config

        def connected(self):
            raise AssertionError("must not connect")

    import forge_puppeteer.mcp.launcher as launcher
    monkeypatch.setattr(launcher, "ReachySDKProvider", FakeReachy)
    monkeypatch.setattr(launcher, "StackChanSDKProvider", FakeStackChan)
    runtime, packs, _connected, extras = ensemble_v4.build_v4(room, robots, options=options)
    try:
        assert [p.performer_id for p in packs] == ["reachy", "stackchan"]
        assert set(extras.motion_patterns["reachy"]) == {"idle", "speaking"}
        kwargs = extras.server_kwargs
        assert kwargs["speech_driven_performers"] == ("reachy", "stackchan")
        assert kwargs["speech_neutral_heads"] == {"reachy": IDENTITY}
        assert kwargs["speech_dynamics"] == {"reachy": "smoothed_v1"}
        assert "reachy" in kwargs["speech_fallbacks"]
        assert len(calls) == 2
    finally:
        runtime.close()
