"""Client for the SDK broker's private, exact choreography host interface."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlsplit

import anyio

from ..execution import ActionRefused, ActionRequest, Completion, StopResult

# SDK broker refusals that are issued before any motion is dispatched. Anything
# else the broker reports mid-request is treated as an unknown outcome.
PRE_DISPATCH_REFUSALS = (
    "active bound host watchdog required",
    "unknown or stale operator-pinned choreography",
    "persistent SDK executor/watchdog unavailable",
    "request cancelled by a newer STOP",
)
# The SDK validates the whole path against its joint limits before it moves.
_LIMIT_REFUSAL = re.compile(r"joint \w+ target -?\d+(\.\d+)? rad is outside \[-?\d+(\.\d+)?, -?\d+(\.\d+)?\]")


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class SOARM101TrustedChoreographyHTTP:
    defer_normal_transition = True

    def __init__(self, broker_url: str, token: str, revisions: dict[str, str], *,
                 finish_before_interrupt_s: float = 3.5, durations_s: dict[str, float] | None = None,
                 startup_target_revision: str | None = None,
                 pose_targets: dict[str, str] | None = None):
        if not token or not (revisions or pose_targets):
            raise ValueError("trusted-host token and exact choreography pins required")
        parsed = urlsplit(broker_url)
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parsed.port is None or parsed.username is not None or parsed.password is not None
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            raise ValueError("trusted choreography broker must use explicit loopback HTTP")
        if not 0 < finish_before_interrupt_s <= 5:
            raise ValueError("transition grace must be within 5 seconds")
        self.finish_before_interrupt_s = finish_before_interrupt_s
        self.url = broker_url.rstrip("/") + "/v1/trusted/choreography"
        self.stop_url = broker_url.rstrip("/") + "/v1/trusted/stop"
        self.target_url = broker_url.rstrip("/") + "/v1/trusted/target"
        self.startup_target_revision = startup_target_revision
        self.pose_targets = dict(pose_targets or {})
        self.token = token
        self.revisions = dict(revisions)
        self.durations_s = dict(durations_s or {})

    def _post(self, url: str, payload: dict[str, object]) -> dict[str, object]:
        request = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        try:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}), _NoRedirects()
            )
            with opener.open(request, timeout=45) as response:
                result = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            refusal = self._refusal(exc)
            if refusal is not None:
                raise ActionRefused(f"SDK declined before moving: {refusal}") from None
            raise RuntimeError("SDK trusted choreography broker outcome unknown") from None
        except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError, ValueError):
            raise RuntimeError("SDK trusted choreography broker outcome unknown") from None
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise RuntimeError("SDK trusted choreography broker rejected request")
        return result

    @staticmethod
    def _refusal(error: urllib.error.HTTPError) -> str | None:
        """The broker's error text when it is a definitive pre-dispatch refusal."""
        try:
            body = json.loads(error.read())
        except (OSError, ValueError):
            return None
        message = body.get("error") if isinstance(body, dict) else None
        if error.code == 409 and isinstance(message, str) and (
                message in PRE_DISPATCH_REFUSALS or _LIMIT_REFUSAL.fullmatch(message)):
            return message
        return None

    def _catalog(self) -> dict[str, dict[str, object]]:
        request = urllib.request.Request(self.url.removesuffix("/choreography") + "/catalog",
            headers={"Authorization": f"Bearer {self.token}"})
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirects())
            with opener.open(request, timeout=5) as response:
                result = json.loads(response.read())
        except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError, ValueError):
            raise RuntimeError("SDK trusted choreography catalog unavailable") from None
        body = result.get("result") if isinstance(result, dict) and result.get("ok") is True else None
        programs = body.get("programs") if isinstance(body, dict) else None
        if not isinstance(programs, list):
            raise TypeError("invalid SDK trusted choreography catalog")
        if self.startup_target_revision is not None and body.get("startup_target") != {
            "name": "wake_to_neutral", "revision": self.startup_target_revision,
        }:
            raise RuntimeError("SDK startup target catalog mismatch")
        advertised_targets = {item.get("name"): item.get("revision")
            for item in body.get("targets", []) if isinstance(item, dict)}
        if any(advertised_targets.get(name) != revision for name, revision in self.pose_targets.items()):
            raise RuntimeError("SDK pinned pose target catalog mismatch")
        catalog = {}
        for item in programs:
            if (not isinstance(item, dict) or set(item) != {"name", "revision", "duration_s"}
                    or not isinstance(item["name"], str)
                    or not isinstance(item["revision"], str)
                    or type(item["duration_s"]) not in (int, float)):
                raise RuntimeError("invalid SDK trusted choreography catalog")
            catalog[item["name"]] = item
        return catalog

    async def validate_catalog(self, durations_s: dict[str, float]) -> None:
        catalog = await anyio.to_thread.run_sync(self._catalog)
        for name, revision in self.revisions.items():
            item = catalog.get(name)
            if (item is None or item["revision"] != revision
                    or abs(float(item["duration_s"]) - durations_s[name]) > 1e-9):
                raise RuntimeError(f"SDK choreography catalog mismatch for {name}")

    async def validate(self, request: ActionRequest) -> None:
        if request.operation == "play_joint_target":
            name = (request.asset_id or "").removeprefix("soarm101/target/")
            if (request.parameters or request.asset_id != f"soarm101/target/{name}"
                    or name not in self.pose_targets or request.asset_revision != self.pose_targets[name]):
                raise PermissionError("pose target is not locally pinned")
            return
        if request.operation == "wake_to_neutral":
            if (request.parameters or request.asset_id != "soarm101/target/wake_to_neutral"
                    or self.startup_target_revision is None
                    or request.asset_revision != self.startup_target_revision):
                raise PermissionError("startup target is not locally pinned")
            return
        if request.operation != "play_choreography" or request.parameters:
            raise ValueError("only exact named choreography requests are supported")
        if not request.asset_id or not request.asset_id.startswith("soarm101/choreography/"):
            raise ValueError("exact choreography asset required")
        name = request.asset_id.removeprefix("soarm101/choreography/")
        if self.revisions.get(name) != request.asset_revision:
            raise PermissionError("choreography is not locally pinned")

    def resources_for(self, request: ActionRequest) -> tuple[str, ...]:
        if request.operation not in {"play_choreography", "wake_to_neutral", "play_joint_target"}:
            raise ValueError("unsupported trusted-host operation")
        return ("soarm101.motion",)

    def execution_timeout_for(self, request: ActionRequest) -> float:
        if request.operation in {"wake_to_neutral", "play_joint_target"}:
            return 35.0
        name = (request.asset_id or "").removeprefix("soarm101/choreography/")
        duration = self.durations_s.get(name)
        if duration is None or not 0.2 <= duration <= 30:
            raise ValueError("trusted choreography requires a bounded reviewed duration")
        return duration + 5.0

    async def execute(self, request: ActionRequest) -> Completion:
        await self.validate(request)
        startup = request.operation in {"wake_to_neutral", "play_joint_target"}
        name = request.asset_id.removeprefix("soarm101/target/" if startup else "soarm101/choreography/")
        result = await anyio.to_thread.run_sync(lambda: self._post(self.target_url if startup else self.url, {
            "request_id": uuid.uuid4().hex, "name": name,
            "revision": request.asset_revision,
        }), abandon_on_cancel=True)
        evidence = result.get("result")
        if not isinstance(evidence, dict) or evidence.get("revision") != request.asset_revision:
            raise RuntimeError("SDK broker completion did not match pinned choreography revision")
        return Completion("SDK broker completed exact pinned choreography; physical settling remains unverified")

    async def stop(self) -> StopResult:
        try:
            result = await anyio.to_thread.run_sync(lambda: self._post(self.stop_url, {
                "request_id": uuid.uuid4().hex,
            }), abandon_on_cancel=False)
        except (RuntimeError, urllib.error.URLError, TimeoutError, ValueError):
            return StopResult(False, "SDK broker STOP outcome unknown")
        body = result.get("result")
        confirmed = isinstance(body, dict) and body.get("completed") is True and body.get("holding") is True
        return StopResult(confirmed, "SDK broker STOP/HOLD confirmed" if confirmed else "SDK broker STOP/HOLD unconfirmed")
