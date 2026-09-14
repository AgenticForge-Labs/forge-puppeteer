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
