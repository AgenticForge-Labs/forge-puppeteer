"""Versioned, software-only embodiment contract for the shared Puppeteer MCP runtime.

No SDK or hardware operation is performed here. This initial adapter is enabled
only for the explicit fake provider. Native adapters must be separately reviewed
and must retain their SDK/broker ownership, STOP and uncertainty semantics.
"""

from __future__ import annotations

import time
from typing import Literal, TYPE_CHECKING

import anyio
from pydantic import BaseModel, ConfigDict, Field

from .execution import ActionRequest
from .robot_contracts import AssetDescriptor

if TYPE_CHECKING:
    from .mcp.server import Gateway

SCHEMA = "forge.embodiment.v1"
PresenceState = Literal["idle", "listening", "speaking", "disconnected"]
TimingSource = Literal["playback", "agent_state", "manual"]


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PresenceObservation(_Contract):
    schema: Literal["forge.embodiment.v1"] = SCHEMA
    event_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    performer_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=0)
    state: PresenceState
    source: TimingSource
    interrupted: bool = False


class ExpressionRequest(_Contract):
    schema: Literal["forge.embodiment.v1"] = SCHEMA
    action_id: str = Field(min_length=1, max_length=128)
    caller_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    performer_id: str = Field(min_length=1, max_length=128)
    kind: Literal["emotion", "dance", "gesture"]
    name: str = Field(min_length=1, max_length=64)
    asset_revision: str = Field(min_length=1, max_length=128)


class FakeEmbodiment:
    """One-per-server fake performer, callable from MCP or deterministic Python.

    Presence is an observation, not a grant to move. Expression requests use
    Gateway.submit / ExecutionRuntime, exactly like explicit provider actions.
    No physical STOP, playback timing or motor settling is inferred.
    """

    def __init__(self, gateway: Gateway, *, ttl_s: float = 30.0, clock=None):
        if ttl_s <= 0:
            raise ValueError("ttl must be positive")
        self.gateway = gateway
        self.ttl_s = ttl_s
        self._clock = clock or time.monotonic
        self._lock = anyio.Lock()
        self._session: str | None = None
        self._sequence = -1
        self._last_event: PresenceObservation | None = None
        self._state: PresenceState = "idle"
        self._last_at: float | None = None
        self._stale = False
        self._requested: dict[str, ExpressionRequest] = {}

    def _expire(self) -> None:
        if self._last_at is not None and self._clock() - self._last_at >= self.ttl_s:
            self._session = None
            self._state = "disconnected"
            self._stale = True
            self._last_at = None
            # Expiry is observation-only; never claim any physical STOP.

    def _snapshot(self) -> dict[str, object]:
        self._expire()
        return {
            "session_id": self._session,
            "state": self._state,
            "sequence": self._sequence,
            "stale": self._stale,
            "timing": (
                "playback_observed" if self._last_event and
                self._last_event.source == "playback" and not self._stale else
                "approximate_or_unknown"
            ),
        }

    async def discover(self) -> dict[str, object]:
        async with self._lock:
            snapshot = self._snapshot()
            assets = await self.gateway.provider.list_assets()
            expressions = self._expressions(assets)
            return {
                "schema": SCHEMA,
                "performer_id": self.gateway.performer_id,
                "device": getattr(self.gateway.provider.config, "device", "unknown"),
                "execution_mode": "fake",
                "physical_qualification": "not_qualified",
                "controls": {
                    state: {"status": "placeholder", "effect": "state_record_only"}
                    for state in ("idle", "listening", "speaking")
                },
                "expressions": expressions,
                "permissions": sorted(self.gateway.provider.grants),
                "resources": {
                    "expression": [f"{self.gateway.provider.config.device}.fake"],
                    "coexistence": "finite_expression_only_while_idle",
                    "precedence": "listening_or_speaking_denies_new_expression",
                },
                "presence": snapshot,
                "limits": {
                    "presence_ttl_s": self.ttl_s,
                    "timing_guarantee": "none",
                    "physical_stop_guarantee": "none",
                },
            }

    @staticmethod
    def _expressions(assets: tuple[AssetDescriptor, ...]) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        for asset in assets:
            # A fake configured asset is not a native SDK recording.
            parts = asset.id.split(":", 2)
            if len(parts) != 3 or parts[1] not in {"emotion", "dance", "gesture"}:
                continue
            results.append({
                "kind": parts[1], "name": parts[2],
                "asset_id": asset.id, "revision": asset.revision,
                "duration_s": asset.duration_s, "mode": "one_shot",
                "capability": "simulated", "physical_qualification": "not_qualified",
                "resources": [asset.id.split(":", 1)[0] + ".fake"],
            })
        return sorted(results, key=lambda item: (str(item["kind"]), str(item["name"])))

    async def observe(self, event: PresenceObservation) -> dict[str, object]:
        async with self._lock:
            self._expire()
            if event.performer_id != self.gateway.performer_id:
                return {"outcome": "denied", "reason": "unknown_performer", "presence": self._snapshot()}
            if event.interrupted and event.state != "idle":
                return {"outcome": "denied", "reason": "invalid_interruption", "presence": self._snapshot()}
            if self._session is not None and event.session_id != self._session:
                return {"outcome": "denied", "reason": "session_busy", "presence": self._snapshot()}
            if self._session == event.session_id and event.sequence <= self._sequence:
                if event.sequence == self._sequence and event == self._last_event:
                    return {"outcome": "duplicate", "presence": self._snapshot()}
                return {
                    "outcome": "stale" if event.sequence < self._sequence else "denied",
                    "reason": "out_of_order_or_conflicting_event",
                    "presence": self._snapshot(),
                }
            # A new session establishes a new ordered sequence namespace.
            if self._session is None:
                self._sequence = -1
                self._last_event = None
            self._session = event.session_id
            self._sequence = event.sequence
            self._last_event = event
            self._state = event.state
            self._stale = event.state == "disconnected"
            self._last_at = self._clock() if event.state != "disconnected" else None
            if event.state == "disconnected":
                self._session = None
            return {"outcome": "accepted", "presence": self._snapshot()}

    async def express(self, request: ExpressionRequest) -> dict[str, object]:
        async with self._lock:
            self._expire()
            prior = self._requested.get(request.action_id)
            if prior is not None:
                return {"outcome": "duplicate" if prior == request else "denied",
                        "reason": "action_id_already_used", "action_id": request.action_id}
            if request.performer_id != self.gateway.performer_id:
                return {"outcome": "denied", "reason": "unknown_performer"}
            if self._state in {"speaking", "listening"}:
                return {"outcome": "denied", "reason": "resource_conflict"}
            if self._stale:
                return {"outcome": "denied", "reason": "presence_disconnected_or_expired"}
            if self._session is not None and request.session_id != self._session:
                return {"outcome": "denied", "reason": "session_busy"}
            assets = await self.gateway.provider.list_assets()
            expected_id = f"{self.gateway.provider.config.device}:{request.kind}:{request.name}"
            asset = next((a for a in assets if a.id == expected_id), None)
            if asset is None:
                return {"outcome": "unsupported", "reason": "expression_not_in_catalog"}
            if asset.revision != request.asset_revision:
                return {"outcome": "denied", "reason": "stale_asset_revision"}
            # The actual runtime action may still be running. An accepted action
            # is never presented as physically completed, even in a fake run.
            self._requested[request.action_id] = request
            try:
                record = await self.gateway.submit(
                    "expression_request",
                    ActionRequest(
                        id=request.action_id,
                        performer_id=request.performer_id,
                        caller_id=request.caller_id,
                        session_id=request.session_id,
                        operation="play_asset",
                        asset_id=asset.id,
                        asset_revision=asset.revision,
                    ),
                )
            except Exception:
                # Submission with an ambiguous outcome is never retried here.
                return {"outcome": "failed", "reason": "submission_unconfirmed",
                        "action_id": request.action_id, "retry_safe": False}
            return {"outcome": "accepted", "action_id": request.action_id,
                    "execution_mode": "fake", "physical_qualification": "not_qualified",
                    "action": record}


def register_fake_embodiment_tools(server, gateway: Gateway) -> None:
    """Explicit tools only for fake providers; never reflect native SDK methods."""
    facade = FakeEmbodiment(gateway)

    @server.tool()
    async def performer_discover() -> dict[str, object]:
        """Discover fake performer states and exact simulation expressions, revisions, and limits."""
        await gateway.check("performer_discover")
        return await facade.discover()

    @server.tool()
    async def conversation_presence(event: PresenceObservation) -> dict[str, object]:
        """Report an ordered conversation state. This observes state; it cannot actuate a robot."""
        await gateway.check("conversation_presence")
        return await facade.observe(event)

    @server.tool()
    async def expression_request(request: ExpressionRequest) -> dict[str, object]:
        """Request an exact catalog expression once. Unknown names are rejected; no random fallback."""
        await gateway.check("expression_request")
        return await facade.express(request)
