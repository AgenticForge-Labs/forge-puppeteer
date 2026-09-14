"""OBSBOT SDK camera-motion backend via a small native helper process."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .cameras import AxisRange, CameraMotionCapabilities, CameraMotionLimits, CameraMotionPosition


class ObsbotSdkPTZController:
    """Camera-native PTZ using OBSBOT's SDK rather than V4L2 gimbal controls.

    The native helper owns SDK initialization and remains alive while the
    controller is connected, so camera wake/initialization happens once and
    subsequent moves are low-latency. Position is currently the commanded
    position for this controller session; hardware readback should be added
    before relative nudging is enabled.
    """

    def __init__(
        self,
        device: str | None = None,
        *,
        helper: str | None = None,
        controller_id: str | None = None,
    ) -> None:
        self.device = device
        self.helper = (
            helper
            or os.environ.get("FORGE_PUPPETEER_OBSBOT_HELPER")
            or "forge-puppeteer-obsbot-helper"
        )
        self._controller_id = controller_id or f"obsbot-sdk:{device or 'auto'}"
        self._process: subprocess.Popen[str] | None = None
        self._position = CameraMotionPosition(pan=0.0, tilt=0.0, zoom=0.0)
        self.camera_name: str | None = None
        self.video_device: str | None = None

    @property
    def controller_id(self) -> str:
        return self._controller_id

    @property
    def capabilities(self) -> CameraMotionCapabilities:
        return CameraMotionCapabilities(
            pan=True,
            tilt=True,
            zoom=True,
            absolute=True,
            relative=False,
            stop=False,
        )

    @classmethod
    def available(cls, helper: str | None = None) -> bool:
        candidate = (
            helper
            or os.environ.get("FORGE_PUPPETEER_OBSBOT_HELPER")
            or "forge-puppeteer-obsbot-helper"
        )
        if "/" in candidate:
            return Path(candidate).is_file()
        return shutil.which(candidate) is not None

    def connect(self) -> None:
        if self._process is not None:
            return
        executable = self.helper
        if not self.available(executable):
            raise RuntimeError(
                "OBSBOT SDK helper is not installed; run "
                "scripts/install-obsbot-sdk-helper.sh"
            )
        command = [executable]
        if self.device:
            command.append(self.device)
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        first = process.stdout.readline().strip()
        if not first.startswith("READY\t"):
            stderr = ""
            if process.stderr is not None:
                stderr = process.stderr.read().strip()
            process.terminate()
            raise RuntimeError(
                f"OBSBOT SDK helper failed to initialize: {first or stderr}"
            )
        parts = first.split("\t", 2)
        if len(parts) == 3:
            self.camera_name = parts[1]
            self.video_device = parts[2]
            self._controller_id = f"obsbot-sdk:{self.video_device or self.camera_name}"
        self._process = process

    def disconnect(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            self._request("QUIT", process=process)
        except Exception:
            process.terminate()
        finally:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()

    def position(self) -> CameraMotionPosition:
        self._require_connected()
        return self._position

    def limits(self) -> CameraMotionLimits:
        return CameraMotionLimits(
            pan=AxisRange(-1.0, 1.0),
            tilt=AxisRange(-1.0, 1.0),
            zoom=AxisRange(0.0, 1.0),
        )

    def move_absolute(self, position: CameraMotionPosition) -> None:
        self._require_connected()
        pan = self._position.pan if position.pan is None else position.pan
        tilt = self._position.tilt if position.tilt is None else position.tilt
        zoom = self._position.zoom if position.zoom is None else position.zoom
        assert pan is not None and tilt is not None and zoom is not None

        if position.pan is not None or position.tilt is not None:
            self._request(f"MOVE {pan:.9f} {tilt:.9f}")
        if position.zoom is not None:
            self._request(f"ZOOM {zoom:.9f}")
        self._position = CameraMotionPosition(pan=pan, tilt=tilt, zoom=zoom)

    def move_relative(self, delta: CameraMotionPosition) -> None:
        del delta
        self._require_connected()
        raise RuntimeError(
            "relative OBSBOT movement is disabled until SDK gimbal-position readback "
            "is normalized and hardware-validated; use absolute targets or Stage presets"
        )

    def stop(self) -> None:
        return

    def _request(
        self,
        command: str,
        *,
        process: subprocess.Popen[str] | None = None,
    ) -> str:
        proc = process or self._process
        if proc is None or proc.stdin is None or proc.stdout is None:
            raise RuntimeError("OBSBOT SDK controller is not connected")
        proc.stdin.write(command + "\n")
        proc.stdin.flush()
        response = proc.stdout.readline().strip()
        if response != "OK" and not response.startswith("STATE\t"):
            raise RuntimeError(
                f"OBSBOT SDK command failed: {response or 'helper exited'}"
            )
        return response

    def _require_connected(self) -> None:
        if self._process is None:
            raise RuntimeError("OBSBOT SDK controller is not connected")
