"""Explicit USB StackChan provider; importing this module never opens hardware.

Requires the external-control SDK, including its asset parser and routed USB
facade. Firmware completion is command completion, not measured settling.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

import anyio

from ..execution import ActionRequest, Completion, StopResult
from ..robot_contracts import AssetDescriptor, ControlDescriptor, DeviceDescriptor


def _sdk():
    try:
        from stackchan_control.assets import _step, list_assets, validate_asset
        from stackchan_control.external_control import CAPABILITIES, ExternalControl
        from stackchan_control.playback import play_asset
        from stackchan_control.routed_serial import RoutedSerialClient
        from stackchan_control.neohex import NeoHexFrame
    except ImportError as exc:
        raise RuntimeError(
            "Install a compatible stackchan-control SDK with serial support "
            "(external-control facade, assets and playback)"
        ) from exc
    return (
        _step,
        list_assets,
        validate_asset,
        CAPABILITIES,
        ExternalControl,
        play_asset,
        RoutedSerialClient,
        NeoHexFrame,
    )

# Puppet defines the publicly named MCP tools; the SDK remains authoritative
# for the implementation, firmware-advertised controls and device limits.
_NATIVE_TO_TOOL = {
    "head": "head_move",
    "face": "face_update",
    "lights": "lights_update",
    "neohex": "neohex_update",
    "play_asset": "play_asset",
}
_CAPABILITY_TO_OPERATION = {
    "motion.update.position_only": "head",
    "avatar.update": "face",
    "lights.update": "lights",
    "external_lights.update": "neohex",
}




async def _call(fn, *args, **kwargs):
    return await anyio.to_thread.run_sync(
        partial(fn, *args, **kwargs), abandon_on_cancel=True
    )


class StackChanProvider:
    """One explicit connection and optional operator-configured asset directory.

    All actions claim a common serialization resource. Closing refuses while a
    device operation remains in flight, including abandoned worker threads.
    Closure itself does not stop or reconcile a device.
    """

    def __init__(
        self,
        instance_id: str,
        *,
        asset_directory: str | Path | None = None,
        connector: Callable[..., tuple[Any, Any]] | None = None,
        allowed_tools: frozenset[str] = frozenset(),
    ):
        if not instance_id:
            raise ValueError("instance_id is required")
        self.instance_id = instance_id
        self.asset_directory = (
            Path(asset_directory) if asset_directory is not None else None
        )
        self._connector = connector
        self._allowed_tools = frozenset(allowed_tools)
        self._client = None
        self._robot = None
        self._connecting = False
        self._closing = False
        self._active_operations = 0
        self._lifecycle_lock = threading.Lock()

    async def connect(self, port: str, *, timeout: float = 8.0) -> None:
        with self._lifecycle_lock:
            if not port or self._robot is not None or self._connecting or self._closing:
                raise ValueError("port required and provider must be disconnected")
            self._connecting = True
        try:
            if self._connector is not None:
                client, robot = await anyio.to_thread.run_sync(
                    partial(self._connector, port, timeout=timeout)
                )
            else:
                *_, external, _play, client_type = _sdk()

                def open_usb():
                    client = client_type.open(port)
                    try:
                        return client, external(client, timeout=timeout)
                    except BaseException:
                        client.close()
                        raise

                # Do not abandon a connection being opened: otherwise cancellation leaks it.
                client, robot = await anyio.to_thread.run_sync(open_usb)
            with self._lifecycle_lock:
                self._client, self._robot = client, robot
        finally:
            with self._lifecycle_lock:
                self._connecting = False

    async def close(self) -> None:
        with self._lifecycle_lock:
            if self._connecting or self._closing:
                raise RuntimeError("connection transition in progress")
            if self._active_operations:
                raise RuntimeError("device operation in progress; stop and reconcile first")
            client, self._client, self._robot = self._client, None, None
            self._closing = client is not None
        try:
            if client is not None:
                await anyio.to_thread.run_sync(client.close)
        finally:
            with self._lifecycle_lock:
                self._closing = False

    def _invoke(self, robot, fn, *args, **kwargs):
        """Track the actual worker lifetime, even if its awaiter is cancelled."""
        with self._lifecycle_lock:
            if self._robot is not robot or self._closing:
                raise RuntimeError("StackChan connection changed before dispatch")
            self._active_operations += 1
        try:
            return fn(*args, **kwargs)
        finally:
            with self._lifecycle_lock:
                self._active_operations -= 1

    def _connected(self):
        if self._robot is None:
            raise RuntimeError("StackChan is not connected")
        return self._robot

    def _assets(self):
        if self.asset_directory is None:
            return ()
        return _sdk()[1](self.asset_directory)

    async def state(self) -> dict[str, object]:
        robot = self._connected()
        state = await _call(self._invoke, robot, robot.state)
        return {"feedback": "commanded", "state": state}

    async def permitted(self, tool: str) -> bool:
        """Intersect operator grants with live firmware support on each call."""
        if tool not in self._allowed_tools:
            return False
        if tool in {"device_info", "list_assets", "action_lookup"}:
            return True
        if self._robot is None:
            return False
        if tool in {"device_stop", "action_cancel"}:
            # Stop remains attemptable even if discovery is unavailable.
            return True
        if tool == "device_state":
            robot = self._connected()
            firmware = await _call(self._invoke, robot, robot.discover)
            return "system.state" in firmware.get("capabilities", ())
        if tool == "play_asset":
            return self.asset_directory is not None
        required = {
            "head_move": "motion.update.position_only",
            "face_update": "avatar.update",
            "lights_update": "lights.update",
            "neohex_update": "external_lights.update",
        }
        if tool not in required:
            return False
        robot = self._connected()
        firmware = await _call(self._invoke, robot, robot.discover)
        return required[tool] in firmware.get("capabilities", ())

    async def list_assets(self) -> tuple[AssetDescriptor, ...]:
        assets = await _call(self._assets)
        return tuple(AssetDescriptor.model_validate(a.descriptor()) for a in assets)

    async def describe(self) -> DeviceDescriptor:
        if self._robot is None:
            return DeviceDescriptor(
                id=self.instance_id, provider="stackchan", connected=False, ready=False
            )
        robot = self._connected()
        firmware = await _call(self._invoke, robot, robot.discover)
        state = (
            await _call(self._invoke, robot, robot.state)
            if "system.state" in firmware["capabilities"]
            else None
        )
        # Native schemas describe firmware wire keys (e.g. yawServo), whereas
        # MCP receives reviewed adapter keys (yaw). Advertise the *actual*
        # tool argument schemas while preserving SDK-owned descriptions,
        # effects, limits and resource identities.
        from ..mcp.stackchan import (
            FaceParameters, HeadParameters, LightParameters, NeoHexParameters,
        )

        model_for = {
            "head": HeadParameters,
            "face": FaceParameters,
            "lights": LightParameters,
            "neohex": NeoHexParameters,
        }
        capabilities = _sdk()[3]
        controls = []
        for native_id, operation in _CAPABILITY_TO_OPERATION.items():
            if (native_id not in firmware["capabilities"]
                    or _NATIVE_TO_TOOL[operation] not in self._allowed_tools):
                continue
            native = capabilities[native_id]
            controls.append(ControlDescriptor(
                id=operation,
                description=native.description,
                effect=native.effect,
                parameters_schema=model_for[operation].model_json_schema(),
                resources=native.resources,
                concurrency_constraints=("stackchan.serialized",),
            ))
        operations = [control.id for control in controls]
        if ("play_asset" in self._allowed_tools and self.asset_directory is not None
                and state is not None):
            operations.append("play_asset")
        return DeviceDescriptor(
            id=self.instance_id,
            provider="stackchan",
            connected=True,
            ready=state is not None,
            supported_operations=tuple(operations),
            controls=tuple(controls),
            feedback="commanded",
            effective_limits=(state or {}).get("native_angle_limits_tenths", {}),
            authority={
                "mode": "explicit_usb_connection",
                "physical_authorization": "external",
            },
        )

    def _prepare(self, request: ActionRequest):
        if request.performer_id != self.instance_id:
            raise ValueError("performer mismatch")
        step, _, validate_asset, *_ = _sdk()
        if request.operation not in _NATIVE_TO_TOOL:
            raise ValueError("unsupported operation")
        if _NATIVE_TO_TOOL[request.operation] not in self._allowed_tools:
            raise PermissionError("StackChan operation not in operator grants")
        if request.operation == "play_asset":
            if request.parameters or not request.asset_id or not request.asset_revision:
                raise ValueError(
                    "playback requires only an exact asset ID and revision"
                )
            asset = next(
                (a for a in self._assets() if a.descriptor()["id"] == request.asset_id),
                None,
            )
            if asset is None or asset.revision != request.asset_revision:
                raise ValueError("unknown or stale asset")
            validate_asset(asset)
            return asset
        if request.asset_id is not None or request.asset_revision is not None:
            raise ValueError("native operation cannot specify an asset")
        if request.operation == "neohex":
            _, _, _, _, _, _, _, neohex = _sdk()
            params = request.parameters
            if set(params) != {"pixels", "brightness"}:
                raise ValueError("NeoHEX requires exactly pixels and brightness")
            pixels = params["pixels"]
            if not isinstance(pixels, (list, tuple)):
                raise ValueError("NeoHEX pixels must be an array")
            return neohex(tuple(pixels), params["brightness"])
        return step(
            {
                "at_s": 0,
                "operation": request.operation,
                "parameters": request.parameters,
            }
        )

    async def validate(self, request: ActionRequest) -> None:
        robot = self._connected()
        prepared = await _call(self._prepare, request)
        firmware = await _call(self._invoke, robot, robot.discover)
        state = await _call(self._invoke, robot, robot.state)
        steps = (
            prepared.steps if request.operation == "play_asset" else
            () if request.operation == "neohex" else (prepared,)
        )
        if request.operation == "neohex" and (
            "external_lights.update" not in firmware["capabilities"]
        ):
            raise ValueError("NeoHEX unavailable in firmware")
        required = {
            "head": "motion.update.position_only",
            "face": "avatar.update",
            "lights": "lights.update",
        }
        for step in steps:
            if required[step.operation] not in firmware["capabilities"]:
                raise ValueError("firmware capability unavailable")
            if step.operation == "head":
                limits = state.get("native_angle_limits_tenths")
                if not isinstance(limits, dict):
                    raise ValueError("motion limits unavailable")
                for axis in ("yaw", "pitch"):
                    servo = getattr(step.update, axis)
                    if servo is not None:
                        bounds = limits.get(axis)
                        if (
                            not isinstance(bounds, list)
                            or len(bounds) != 2
                            or any(type(v) is not int for v in bounds)
                            or bounds[0] > bounds[1]
                            or not bounds[0] <= servo.angle <= bounds[1]
                        ):
                            raise ValueError("invalid or exceeded live motion limits")

    def resources_for(self, request: ActionRequest) -> tuple[str, ...]:
        prepared = self._prepare(request)
        if request.operation == "play_asset":
            resources = prepared.descriptor()["resources"]
        else:
            resources = {
                "head": ("head.pose",),
                "face": ("face",),
                "lights": ("lights.builtin",),
                "neohex": ("lights.neohex",),
            }[request.operation]
        return tuple(sorted({"stackchan.serialized", *resources}))

    async def execute(self, request: ActionRequest) -> Completion:
        # Recheck for direct callers as well as the runtime's second validation.
        robot = self._connected()
        # Use the native SDK's stop generation, not a synthetic broker clock.
        # This invalidates preparation if a stop occurred while awaiting
        # firmware discovery or operator action validation.
        generation = (await _call(self._invoke, robot, robot._generation))[0]
        await self.validate(request)
        if self._connected() is not robot:
            raise RuntimeError("StackChan connection changed during preparation")
        if (await _call(self._invoke, robot, robot._generation))[0] != generation:
            raise RuntimeError("StackChan action invalidated by stop")
        prepared = await _call(self._prepare, request)
        if request.operation == "play_asset":
            results = await _call(
                self._invoke,
                robot,
                _sdk()[5],
                robot, prepared,
                expected_revision=request.asset_revision,
            )
            if len(results) != len(prepared.steps) or any(
                not r.events or r.events[-1].event != "completed" for r in results
            ):
                raise RuntimeError("playback completion not confirmed")
        else:
            if request.operation == "head":
                events = await _call(
                    self._invoke, robot, robot.move_head, prepared.update,
                    _generation=generation,
                )
            elif request.operation == "face":
                events = await _call(
                    self._invoke, robot, robot.update_face, prepared.update,
                    expected_generation=generation,
                )
            else:
                events = await _call(
                    self._invoke, robot, robot.update_lights, prepared.update,
                    expected_generation=generation,
                )
            if not events or events[-1].event != "completed":
                raise RuntimeError("firmware completion not confirmed")
        return Completion(
            "firmware completed command(s); physical settling not measured"
        )

    async def stop(self) -> StopResult:
        try:
            robot = self._connected()
            events = await _call(self._invoke, robot, robot.stop)
            if events and events[-1].event == "completed":
                return StopResult(
                    True,
                    "firmware confirmed system.stop; physical settling not measured",
                )
        except Exception:  # noqa: BLE001 - a failed stop must remain unconfirmed
            return StopResult(False, "stop outcome unconfirmed")
        return StopResult(False, "stop outcome unconfirmed")
