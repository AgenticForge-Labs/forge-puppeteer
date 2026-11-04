from pathlib import Path

import pytest

from forge_puppeteer.cameras import CameraMotionPosition
from forge_puppeteer.obsbot import ObsbotSdkPTZController


def test_obsbot_capabilities_and_limits():
    controller = ObsbotSdkPTZController(device="/dev/video0", helper="/definitely/missing")
    assert controller.controller_id == "obsbot-sdk:/dev/video0"
    assert controller.capabilities.pan
    assert controller.capabilities.tilt
    assert controller.capabilities.zoom
    assert controller.capabilities.absolute
    assert not controller.capabilities.relative
    limits = controller.limits()
    assert limits.pan.minimum == -1.0
    assert limits.pan.maximum == 1.0
    assert limits.zoom.minimum == 0.0
    assert limits.zoom.maximum == 1.0


def test_obsbot_missing_helper_is_unavailable():
    assert not ObsbotSdkPTZController.available("/definitely/missing")


def test_obsbot_requires_connection_before_motion():
    controller = ObsbotSdkPTZController(helper="/definitely/missing")
    with pytest.raises(RuntimeError, match="not connected"):
        controller.position()
    with pytest.raises(RuntimeError, match="not connected"):
        controller.move_absolute(CameraMotionPosition(pan=0.25, tilt=0.0))


def test_installer_and_native_helper_are_present():
    root = Path(__file__).resolve().parents[1]
    assert (root / "scripts" / "install-obsbot-sdk-helper.sh").is_file()
    assert (root / "native" / "obsbot_sdk_helper.cpp").is_file()


def _fake_helper(tmp_path: Path, content: str) -> str:
    script = tmp_path / "fake_obsbot_helper"
    script.write_text("#!/usr/bin/env python3\n" + content, encoding="utf-8")
    script.chmod(0o700)
    return str(script)


def test_obsbot_startup_times_out_with_no_ready_line(tmp_path, monkeypatch):
    helper = _fake_helper(tmp_path, "import time\ntime.sleep(5)\n")
    monkeypatch.setattr(ObsbotSdkPTZController, "STARTUP_TIMEOUT_SECONDS", 0.05)
    controller = ObsbotSdkPTZController(helper=helper)
    with pytest.raises(RuntimeError, match="timed out"):
        controller.connect()
    assert controller._process is None


def test_obsbot_startup_times_out_on_partial_ready_line(tmp_path, monkeypatch):
    helper = _fake_helper(
        tmp_path,
        "import sys, time\nsys.stdout.write('REA')\nsys.stdout.flush()\ntime.sleep(5)\n",
    )
    monkeypatch.setattr(ObsbotSdkPTZController, "STARTUP_TIMEOUT_SECONDS", 0.05)
    controller = ObsbotSdkPTZController(helper=helper)
    with pytest.raises(RuntimeError, match="timed out"):
        controller.connect()
    assert controller._process is None


def test_obsbot_startup_rejects_invalid_ready_and_cleans_up(tmp_path):
    helper = _fake_helper(tmp_path, "print('ERROR\\tdevice missing', flush=True)\n")
    controller = ObsbotSdkPTZController(helper=helper)
    with pytest.raises(RuntimeError, match="failed to initialize"):
        controller.connect()
    assert controller._process is None


def test_obsbot_startup_accepts_valid_ready_line(tmp_path):
    helper = _fake_helper(
        tmp_path,
        "import sys\n"
        "print('READY\\tMockCamera\\t/dev/video17', flush=True)\n"
        "for line in sys.stdin:\n"
        "    if line.strip() == 'QUIT':\n"
        "        print('OK', flush=True)\n"
        "        break\n",
    )
    controller = ObsbotSdkPTZController(helper=helper)
    try:
        controller.connect()
        assert controller.camera_name == "MockCamera"
        assert controller.video_device == "/dev/video17"
    finally:
        controller.disconnect()
