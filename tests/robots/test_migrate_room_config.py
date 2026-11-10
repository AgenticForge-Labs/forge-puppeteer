"""scripts/migrate-room-config.py: legacy v2/v3 configs -> loadable v4 files."""
import importlib.util
import json
from pathlib import Path

import pytest

from forge_puppeteer.ensemble.launcher import config_readiness

spec = importlib.util.spec_from_file_location(
    "migrate_room_config", Path(__file__).resolve().parents[2] / "scripts/migrate-room-config.py")
migrate_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migrate_mod)

TOOLS = ["head_pose", "play_move", "device_stop", "wake_up", "original_idle", "pose_and_antennas"]


def reachy_server(tmp_path):
    return {"backend": "reachy_sdk", "device": "reachy", "instance_id": "reachy-local",
            "allowed_tools": TOOLS, "physical_authority": "I_AUTHORIZE_REACHY_CONTROL",
            "expected_daemon_hardware_id": "hw", "host": "127.0.0.1", "connection_mode": "network",
            "sdk_version": "1.0", "asset_root": str(tmp_path), "manifest": str(tmp_path / "m.json")}


def stackchan_server():
    return {"backend": "stackchan_sdk", "device": "stackchan", "instance_id": "stackchan",
            "allowed_tools": ["head_move", "face_update", "device_stop"],
            "physical_authority": "I_AUTHORIZE_STACKCHAN_CONTROL", "port": "/dev/ttyACM0", "sdk_version": "0.3.0"}


def v2(tmp_path):
    neutral = tmp_path / "neutral.json"
    neutral.write_text(json.dumps([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]))
    return {"schema_version": 2, "motion_authority": "I_AUTHORIZE_ENSEMBLE_ROBOT_MOTION",
            "reachy": reachy_server(tmp_path), "reachy_neutral_head": str(neutral),
            "action_journal": str(tmp_path / "a.db"), "ownership_db": str(tmp_path / "o.db"),
            "reachy_expression_style": "classic", "reachy_startup_behavior": "wake_up_if_sleeping",
            "listening_mode": "tilt", "reachy_neutral_antennas_rad": [-0.1745, 0.1745],
            "motion_profiles": {"reachy-local": {"idle": "original_v1", "speaking": "smooth_v2"}},
            "speech_driven_performers": ["reachy-local"], "reachy_legacy_speech_fallback": True,
            "reachy_original_idle_authority": "I_AUTHORIZE_REACHY_ORIGINAL_IDLE"}


def test_v2_reachy_config_migrates_to_loadable_v4_files(tmp_path):
    files = migrate_mod.migrate(v2(tmp_path))
    out = tmp_path / "v4"
    migrate_mod.write({k: v for k, v in files.items() if not k.startswith("_")}, out)
    room = json.loads((out / "ensemble.json").read_text())
    assert room["physical"] is True and "motion_authority" not in room
    robot = json.loads((out / "reachy.json").read_text())
    assert "physical_authority" not in robot["provider"]
    assert robot["options"] == {
        "expression_style": "classic", "startup_behavior": "wake_up_if_sleeping", "listening_mode": "tilt",
        "neutral_antennas_rad": [-0.1745, 0.1745], "legacy_speech_fallback": True,
        "idle": "original_v1", "speaking": "smooth_v2", "speech_driven": True}
    assert config_readiness(out / "ensemble.json")["performers"] == ["reachy-local"]


def test_v3_duo_migrates_with_stackchan_options(tmp_path):
    old = v2(tmp_path)
    old.update(schema_version=3, stackchan=stackchan_server(), stackchan_speaking_mode="native_weight",
               reachy_expression_style="expressive", reachy_startup_behavior="none",
               motion_profiles={}, speech_driven_performers=["reachy-local", "stackchan"],
               reachy_legacy_speech_fallback=False)
    files = migrate_mod.migrate(old)
    assert files["stackchan.json"]["options"] == {"speaking_mode": "native_weight", "speech_driven": True}
    assert files["reachy.json"]["options"]["speech_driven"] is True
    out = tmp_path / "duo"
    migrate_mod.write({k: v for k, v in files.items() if not k.startswith("_")}, out)
    assert config_readiness(out / "ensemble.json")["performers"] == ["reachy-local", "stackchan"]


@pytest.mark.parametrize("change,message", [
    ({"schema_version": 1}, "only v2"),
    ({"soarm101": {}}, "SO-ARM"),
    ({"reachy_original_speech_authority": "x"}, "not supported by v4"),
    ({"motion_profiles": {"reachy-local": {"idle": "smooth_v2"}}}, "no longer supported"),
    ({"motion_profiles": {"reachy-local": {"speaking": "original_v1"}}}, "no longer supported"),
    ({"motion_profiles": {"ghost": {"idle": "legacy"}}}, "unknown performers"),
])
def test_unsupported_legacy_options_stop_the_migration(tmp_path, change, message):
    with pytest.raises(migrate_mod.MigrationError, match=message):
        migrate_mod.migrate({**v2(tmp_path), **change})


def test_never_overwrites_and_main_reports_cleanly(tmp_path, capsys):
    old = tmp_path / "old.json"
    old.write_text(json.dumps(v2(tmp_path)))
    out = tmp_path / "out"
    assert migrate_mod.main([str(old), str(out)]) == 0
    before = (out / "reachy.json").read_text()
    assert migrate_mod.main([str(old), str(out)]) == 1
    assert (out / "reachy.json").read_text() == before
    assert "refusing to overwrite" in capsys.readouterr().err
    assert json.loads(old.read_text())["schema_version"] == 2  # input untouched


def test_drop_unsupported_keeps_going_and_reports_every_dropped_option(tmp_path):
    old = {**v2(tmp_path), "reachy_return_to_neutral_after_expression": True,
           "motion_profiles": {"reachy-local": {"idle": "smooth_v2", "speaking": "original_v1"}},
           "reachy_original_speech_authority": "x", "startup_settle_s": 2.0}
    with pytest.raises(migrate_mod.MigrationError):
        migrate_mod.migrate(old)
    files = migrate_mod.migrate(old, drop_unsupported=True)
    notes = " | ".join(files["_warnings"])
    for expected in ("reachy_return_to_neutral_after_expression", "reachy_original_speech_authority",
                     "idle profile 'smooth_v2'", "speaking profile 'original_v1'", "startup_settle_s",
                     "legacy_speech_fallback needs smooth_v2"):
        assert expected in notes
    options = files["reachy.json"]["options"]
    assert "idle" not in options and "speaking" not in options  # both fell back to default
    out = tmp_path / "lenient"
    migrate_mod.write({k: v for k, v in files.items() if not k.startswith("_")}, out)
    assert config_readiness(out / "ensemble.json")["performers"] == ["reachy-local"]


def test_main_flag_prints_notes(tmp_path, capsys):
    old = tmp_path / "old.json"
    old.write_text(json.dumps({**v2(tmp_path), "reachy_return_to_neutral_after_expression": True}))
    assert migrate_mod.main([str(old), str(tmp_path / "strict")]) == 1
    assert migrate_mod.main(["--drop-unsupported", str(old), str(tmp_path / "lenient")]) == 0
    assert "note: reachy_return_to_neutral_after_expression" in capsys.readouterr().out


def test_unknown_startup_behavior_is_strict_by_default_and_mapped_when_lenient(tmp_path):
    old = {**v2(tmp_path), "reachy_startup_behavior": "wake_up"}
    with pytest.raises(migrate_mod.MigrationError, match="startup behavior"):
        migrate_mod.migrate(old)
    files = migrate_mod.migrate(old, drop_unsupported=True)
    assert files["reachy.json"]["options"]["startup_behavior"] == "wake_up_if_sleeping"
    assert any("wake_up_if_sleeping" in note for note in files["_warnings"])
