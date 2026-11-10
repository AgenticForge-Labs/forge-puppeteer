"""Real SDK broker process fixture for Puppeteer's trusted choreography E2E."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from http import HTTPStatus
from pathlib import Path

from soarm101_motion.agent_control import AgentAuthorityStore
from soarm101_motion.broker import (
    AgentMotionRates,
    BrokerResponse,
    RobotBrokerHTTPServer,
    RobotBrokerService,
)
from soarm101_motion.broker_sdk import SDKAgentExecutor
from soarm101_motion.capability_profile import CapabilityProfile
from soarm101_motion.choreography import (
    ChoreographyPolicy,
    JointChoreography,
    JointFrame,
)
from soarm101_motion.config import SOARM101Config


def main() -> None:
    port, telemetry_path = int(sys.argv[1]), Path(sys.argv[2])
    token = os.environ["SOARM101_BROKER_TOKEN"]
    host_token = os.environ["SOARM101_TRUSTED_HOST_TOKEN"]
    executor = SDKAgentExecutor(
        config=SOARM101Config(robot_id="so101"), rates=AgentMotionRates(),
        simulation=True, simulation_actions=True,
        authority_store=AgentAuthorityStore(telemetry_path.with_suffix(".lease.json")),
    )
    executor.execute("state", {})  # establish the one persistent simulated SDK session
    if os.environ.get("SOARM_TEST_OPERATOR_ONLY") == "1":
        # SDK intentionally prohibits constructing a physical-only mode on a
        # simulator. Switch only its admission flag after simulated startup to
        # exercise the real execute() denial without opening physical hardware.
        executor.operator_choreography_only = True
    gripper_writes: list[object] = []
    backend = executor._session.backend
    original_tool_write = backend.write_tool_position

    def record_tool_write(*args, **kwargs):
        gripper_writes.append((args, kwargs))
        return original_tool_write(*args, **kwargs)

    backend.write_tool_position = record_tool_write
    from soarm101_motion.constants import ARM_JOINTS
    origin = tuple(executor._session.get_joint_positions().positions[name] for name in ARM_JOINTS)
    programs = {
        name: JointChoreography.author(
            name, "so101", "simulation",
            (JointFrame(0.0, origin), JointFrame(0.4, origin)),
        ) for name in ("idle", "speaking", "yes")
    }
    policy = ChoreographyPolicy(
        "so101", "simulation", tuple(p.revision for p in programs.values()),
        max_duration_s=2.0, max_speed_deg_s=8, max_acceleration_deg_s2=25,
    )
    profile = CapabilityProfile.from_document({
        "schema_version": 1, "name": "puppeteer-trusted-simulation",
        "tools": ["robot_health", "robot_capabilities", "robot_state", "jog_joint", "stop"],
        "cameras": [], "limits": {},
    })

    class RecordingService(RobotBrokerService):
        def dispatch_trusted_host(self, method, path, payload):
            if method == "GET":
                return super().dispatch_trusted_host(method, path, payload)
            received = {
                "phase": "received", "method": method, "path": path,
                "request": dict(payload), "executor_session_id": executor.session_id,
                "sdk_connect_count": executor.connection_count,
                "gripper_write_count": len(gripper_writes),
            }
            with telemetry_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(received, sort_keys=True) + "\n")
                stream.flush()
            response = super().dispatch_trusted_host(method, path, payload)
            # Preserve a real in-flight MCP action window around this
            # non-realtime simulation so the test can exercise disconnect's
            # urgent STOP path while the response is still outstanding.
            if path.endswith("/choreography") and payload.get("name") == "speaking":
                time.sleep(float(os.environ.get("SOARM_TEST_SPEAKING_DELAY", "0.35")))
            if path.endswith("/stop") and os.environ.get("SOARM_TEST_LOSE_STOP_ACK") == "1":
                # The real simulator has processed STOP; model loss of its
                # acknowledgement at the trusted transport boundary only.
                response = BrokerResponse(HTTPStatus.SERVICE_UNAVAILABLE,
                                          {"ok": False, "error": "injected lost STOP acknowledgement"})
            record = {
                "phase": "completed",
                "method": method, "path": path, "request": dict(payload),
                "status": int(response.status), "body": response.body,
                "executor_session_id": executor.session_id,
                "sdk_connect_count": executor.connection_count,
                "gripper_write_count": len(gripper_writes),
            }
            with telemetry_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, sort_keys=True) + "\n")
                stream.flush()
            return response

    service = RecordingService(
        executor=executor, token=token, trusted_host_token=host_token,
        profile=profile, allow_simulated_sdk_posts=True, watchdog_timeout_s=8,
        choreography_policy=policy, choreography_catalog=programs,
        event_path=telemetry_path.with_suffix(".events.jsonl"),
    )
    assert service.watchdog is not None
    service.watchdog.start()
    service.watchdog.heartbeat()
    server = RobotBrokerHTTPServer(("127.0.0.1", port), service)
    heartbeat_done = threading.Event()

    def maintain_watchdog() -> None:
        while not heartbeat_done.wait(1.0):
            try:
                service.watchdog.heartbeat()
            except Exception:  # noqa: BLE001 - deliberate: an unknown device outcome must not be retried
                return

    threading.Thread(target=maintain_watchdog, daemon=True).start()
    try:
        server.serve_forever()
    finally:
        heartbeat_done.set()
        server.server_close()
        service.watchdog.close()
        executor.close()


if __name__ == "__main__":
    main()
