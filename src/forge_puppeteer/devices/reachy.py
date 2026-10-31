"""Offline Reachy recorded-move discovery; no device is opened here."""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from ..deployment import verify_reachy_snapshot
from ..execution import ActionRequest, Completion, StopResult
from ..robot_contracts import AssetDescriptor, DeviceDescriptor

_SOUND_EXTENSIONS = (".wav", ".mp3", ".ogg", ".oga", ".opus", ".flac", ".m4a", ".aac")
_RESOURCES = ("head.pose", "antennas.left", "antennas.right", "body.yaw")
PINNED_REACHY_SDK_VERSION = "1.10.0rc5"
_HEAD_TRANSLATION_LIMIT_M = 0.10
_ANTENNA_ANGLE_LIMIT_RAD = 1.20
_NATIVE_DURATION_LIMIT_S = (0.05, 5.0)


class ReachyCatalog:
    """Load a verified local snapshot through the pinned SDK's RecordedMove.

    This is deliberately discovery-only: SDK playback has no authority,
    ownership, measured completion, or confirmed-stop contract here.
    """

    def __init__(
        self,
        root: Path,
        manifest: dict[str, Any],
        *,
        move_factory: Callable[..., Any] | None = None,
    ):
        self.root = Path(root)
        # Retain a private copy so the caller cannot change the lock in place.
        self.manifest = json.loads(json.dumps(manifest))
        if self.manifest.get("sdk_version") != PINNED_REACHY_SDK_VERSION:
            raise ValueError(
                "unsupported Reachy SDK version: expected "
                f"{PINNED_REACHY_SDK_VERSION}, got {self.manifest.get('sdk_version')!r}"
            )
        self.revision = verify_reachy_snapshot(self.root, self.manifest)
        if move_factory is None:
            try:
                installed = version("reachy-mini")
                from reachy_mini.motion.recorded_move import RecordedMove
            except (ImportError, PackageNotFoundError) as exc:
                raise RuntimeError(
                    "install the pinned reachy-mini SDK to load this catalog"
                ) from exc
            if installed != self.manifest["sdk_version"]:
                raise ValueError(
                    f"Reachy SDK version mismatch: expected {self.manifest['sdk_version']}, got {installed}"
                )
            move_factory = RecordedMove
        self._factory = move_factory

    def _load(self) -> dict[str, Any]:
        if verify_reachy_snapshot(self.root, self.manifest) != self.revision:
            raise ValueError("snapshot manifest changed")
        moves: dict[str, Any] = {}
        for name in sorted(self.manifest["files"]):
            path = Path(name)
            if path.suffix != ".json" or not (
                len(path.parts) == 1 or len(path.parts) == 2 and path.parts[0] == "data"
            ):
                continue
            if path.stem in moves:
                raise ValueError(f"duplicate move ID: {path.stem}")
            sound = next(
                (
                    self.root / path.with_suffix(ext)
                    for ext in _SOUND_EXTENSIONS
                    if str(path.with_suffix(ext)) in self.manifest["files"]
                ),
                None,
            )
            data = (self.root / path).read_bytes()
            if hashlib.sha256(data).hexdigest() != self.manifest["files"][name]:
                raise ValueError(f"missing or mutated snapshot file: {name}")
            moves[path.stem] = self._factory(json.loads(data), sound_path=sound)
        return moves

    async def describe(self) -> DeviceDescriptor:
        return DeviceDescriptor(
            id="reachy",
            provider="reachy",
            sdk_version=self.manifest["sdk_version"],
            connected=False,
            ready=False,
            supported_operations=("list_assets",),
            authority={"motion": "unavailable"},
        )

    async def list_assets(self) -> tuple[AssetDescriptor, ...]:
        moves = self._load()
        return tuple(
            AssetDescriptor(
                id=f"reachy/{name}",
                kind="recording",
                source=self.manifest["source_url"],
                revision=self.revision,
                description=move.description,
                duration_s=move.duration,
                resources=_RESOURCES,
                audio_association=str(move.sound_path.relative_to(self.root))
                if move.sound_path is not None
                else None,
            )
            for name, move in moves.items()
        )

    def get(self, asset_id: str, revision: str) -> Any:
        if revision != self.revision:
            raise ValueError("asset revision mismatch")
        moves = self._load()
        if not asset_id.startswith("reachy/") or asset_id[7:] not in moves:
            raise ValueError(f"unknown Reachy asset: {asset_id}")
        return moves[asset_id[7:]]

    async def stop(self) -> None:
        raise RuntimeError("Reachy device control is unavailable")


class ReachyPlaybackProvider:
    """Opt-in adapter for the pinned SDK's recorded-move playback loop.

    A caller supplies an already-created SDK client; this provider never opens a
    daemon connection. Playback completion means only that the SDK coroutine
    returned. The SDK exposes no measured settling/completion signal.
    """

    def __init__(
        self,
        catalog: ReachyCatalog,
        robot: Any,
        *,
        motion_authorized: bool = False,
    ):
        self.catalog = catalog
        self.robot = robot
        self.motion_authorized = motion_authorized
        self._lock = threading.Lock()
        self._active = False
        self._active_operation: str | None = None
        self._quarantined = False

    async def describe(self) -> DeviceDescriptor:
        from ..robot_contracts import ControlDescriptor

        return DeviceDescriptor(
            id="reachy",
            provider="reachy",
            sdk_version=self.catalog.manifest["sdk_version"],
            connected=True,
            ready=True,
            supported_operations=("list_assets", "play_move", "head_pose", "antenna_targets", "stop"),
            controls=(ControlDescriptor(
                id="reachy.play_move",
                description="Play an exact recorded move",
                effect="motion",
                parameters_schema={
                    "type": "object",
                    "properties": {"sound": {"type": "boolean"}},
                    "additionalProperties": False,
                },
                resources=_RESOURCES,
                concurrency_constraints=("serialized across all Reachy motion",),
            ), ControlDescriptor(
                id="reachy.head_pose",
                description="Move the head to a bounded rigid pose",
                effect="motion",
                parameters_schema={"type": "object", "properties": {
                    "pose": {"type": "array", "minItems": 4, "maxItems": 4},
                    "duration_s": {"type": "number", "minimum": 0.05, "maximum": 5.0},
                }, "required": ["pose"], "additionalProperties": False},
                resources=("head.pose",), unit="metres and rotation matrix",
                limits={"translation_abs_m": _HEAD_TRANSLATION_LIMIT_M,
                        "duration_s": list(_NATIVE_DURATION_LIMIT_S)},
                concurrency_constraints=("serialized across all Reachy motion",),
            ), ControlDescriptor(
                id="reachy.antenna_targets",
                description="Move both antennas to bounded joint targets",
                effect="motion",
                parameters_schema={"type": "object", "properties": {
                    "angles_rad": {"type": "array", "minItems": 2, "maxItems": 2},
                    "duration_s": {"type": "number", "minimum": 0.05, "maximum": 5.0},
                }, "required": ["angles_rad"], "additionalProperties": False},
                resources=("antennas.left", "antennas.right"), unit="radians",
                limits={"angle_abs_rad": _ANTENNA_ANGLE_LIMIT_RAD,
                        "duration_s": list(_NATIVE_DURATION_LIMIT_S)},
                concurrency_constraints=("serialized across all Reachy motion",),
            )),
            feedback="commanded",
            authority={"motion": "granted" if self.motion_authorized else "denied"},
        )

    async def state(self) -> dict[str, object]:
        return {
            "feedback": "commanded",
            "active": self._active,
            "active_operation": self._active_operation,
            "authority": "granted" if self.motion_authorized else "denied",
            "quarantined": self._quarantined,
        }

    async def permitted(self, tool: str) -> bool:
        """Explicit local tool grant, intersected with current motion authority."""
        known = {"device_info", "device_state", "list_assets", "action_lookup"}
        motion = {"play_move", "head_pose", "antenna_targets", "action_cancel", "device_stop"}
        return tool in known or (tool in motion and self.motion_authorized)

    async def list_assets(self) -> tuple[AssetDescriptor, ...]:
        return await self.catalog.list_assets()

    def resources_for(self, request: ActionRequest) -> tuple[str, ...]:
        return _RESOURCES

    async def validate(self, request: ActionRequest) -> None:
        if request.operation not in {"play_move", "head_pose", "antenna_targets"}:
            raise ValueError("unsupported Reachy operation")
        if not self.motion_authorized:
            raise PermissionError("Reachy motion authority is not granted")
        if self._quarantined:
            raise RuntimeError("Reachy provider is quarantined after an unconfirmed stop; restart only after operator verification")
        if request.operation == "play_move":
            if set(request.parameters) - {"sound"} or (
                "sound" in request.parameters and not isinstance(request.parameters["sound"], bool)
            ):
                raise ValueError("play_move accepts only an optional boolean sound parameter")
            if request.parameters.get("sound", False):
                raise ValueError("audio playback is disabled by the launcher media policy")
            if not request.asset_id or not request.asset_revision:
                raise ValueError("play_move requires an exact asset ID and revision")
            self.catalog.get(request.asset_id, request.asset_revision)
        else:
            self._validate_native(request)
        with self._lock:
            if self._active:
                raise RuntimeError("Reachy playback is already active")

    async def execute(self, request: ActionRequest) -> Completion:
        await self.validate(request)
        with self._lock:
            if self._active:
                raise RuntimeError("Reachy playback is already active")
            self._active = True
            self._active_operation = request.operation
        try:
            if request.operation == "play_move":
                move = self.catalog.get(request.asset_id, request.asset_revision)
                await self.robot.async_play_move(move, sound=request.parameters.get("sound", False))
                evidence = "SDK playback loop returned; physical settling unmeasured"
            else:
                # The pinned SDK's goto_target waits for its daemon task to finish,
                # but returns no measured-pose evidence. Keep its blocking call off
                # the event loop so stop and unrelated runtime work can proceed.
                import asyncio

                args = self._native_sdk_args(request)
                sdk_task = asyncio.create_task(asyncio.to_thread(self.robot.goto_target, **args))
                try:
                    await asyncio.shield(sdk_task)
                except asyncio.CancelledError:
                    # The SDK offers no task handle/cancel operation for goto_target.
                    # Keep ownership until the worker really returns; cancellation
                    # cannot imply the physical task stopped.
                    await sdk_task
                    raise
                evidence = "SDK goto task completed; physical pose not independently measured"
        finally:
            with self._lock:
                self._active = False
                self._active_operation = None
        return Completion(evidence)

    @staticmethod
    def _validate_native(request: ActionRequest) -> None:
        import math

        params = request.parameters
        if request.operation == "head_pose":
            if set(params) - {"pose", "duration_s"} or "pose" not in params:
                raise ValueError("head_pose requires pose and optional duration_s")
            pose = params["pose"]
            if not isinstance(pose, list) or len(pose) != 4 or any(
                not isinstance(row, list) or len(row) != 4 for row in pose
            ):
                raise ValueError("pose must be a 4 by 4 numeric matrix")
            if any(isinstance(x, bool) or not isinstance(x, (int, float)) for row in pose for x in row):
                raise ValueError("pose must contain finite numbers")
            try:
                values = [[float(x) for x in row] for row in pose]
            except (TypeError, ValueError):
                raise ValueError("pose must contain finite numbers") from None
            if any(not math.isfinite(x) for row in values for x in row):
                raise ValueError("pose must contain finite numbers")
            if any(abs(values[0][i] * values[0][j] + values[1][i] * values[1][j] + values[2][i] * values[2][j] - (1 if i == j else 0)) > 0.02
                   for i in range(3) for j in range(3)):
                raise ValueError("pose rotation must be orthonormal")
            determinant = (
                values[0][0] * (values[1][1] * values[2][2] - values[1][2] * values[2][1])
                - values[0][1] * (values[1][0] * values[2][2] - values[1][2] * values[2][0])
                + values[0][2] * (values[1][0] * values[2][1] - values[1][1] * values[2][0])
            )
            if abs(determinant - 1.0) > 0.02:
                raise ValueError("pose rotation must be right handed")
            if any(abs(values[i][3]) > _HEAD_TRANSLATION_LIMIT_M for i in range(3)):
                raise ValueError("pose translation exceeds adapter bounds")
            if any(abs(values[3][i] - (1.0 if i == 3 else 0.0)) > 0.001 for i in range(4)):
                raise ValueError("pose must be a homogeneous transform")
        else:
            if set(params) - {"angles_rad", "duration_s"} or "angles_rad" not in params:
                raise ValueError("antenna_targets requires angles_rad and optional duration_s")
            angles = params["angles_rad"]
            if not isinstance(angles, list) or len(angles) != 2:
                raise ValueError("angles_rad must contain right and left antenna angles")
            if any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in angles):
                raise ValueError("antenna angles must be finite numbers")
            try:
                values = [float(x) for x in angles]
            except (TypeError, ValueError):
                raise ValueError("antenna angles must be finite numbers") from None
            if any(not math.isfinite(x) or abs(x) > _ANTENNA_ANGLE_LIMIT_RAD for x in values):
                raise ValueError("antenna angle exceeds adapter bounds")
        duration = params.get("duration_s", 0.5)
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or not _NATIVE_DURATION_LIMIT_S[0] <= duration <= _NATIVE_DURATION_LIMIT_S[1]:
            raise ValueError("duration_s is outside adapter bounds")

    @staticmethod
    def _native_sdk_args(request: ActionRequest) -> dict[str, Any]:
        duration = float(request.parameters.get("duration_s", 0.5))
        if request.operation == "head_pose":
            return {"head": request.parameters["pose"], "duration": duration, "body_yaw": None}
        return {"antennas": [float(x) for x in request.parameters["angles_rad"]], "duration": duration, "body_yaw": None}

    async def stop(self) -> StopResult:
        # cancel_move only requests cancellation; the SDK has no acknowledgment.
        with self._lock:
            active_operation = self._active_operation
            self._quarantined = True
        if active_operation in (None, "play_move") and hasattr(self.robot, "cancel_move"):
            try:
                self.robot.cancel_move()
            except Exception as exc:  # noqa: BLE001 - contain SDK stop failure and retain quarantine
                return StopResult(False, f"SDK cancellation request failed; provider quarantined: {type(exc).__name__}")
            return StopResult(False, "SDK cancellation requested; stop not confirmed")
        return StopResult(False, "SDK exposes no independent stop acknowledgment for native goto tasks")
