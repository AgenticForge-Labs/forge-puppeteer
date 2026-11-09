# Reachy daemon motion ownership: requirements for original profiles

The original presence profiles are implemented and exercised with fake devices.
The physical launcher does not require a daemon-wide lease. The pinned
Reachy Mini SDK is `1.10.0rc5` at upstream commit
`221b3c3ce1aadae3c0eed97a8712d83bd205e13e`. Its SDK WebSocket drops command
responses, and the daemon accepts motion from other paths independently of the
SDK client. A snapshot claiming that no other motion is active is not a lease.

## Observed admission paths

The SDK WebSocket calls `Backend.process_command` without a connection identity
and discards the command response (`io/ws_server.py`). WebRTC also dispatches
commands through the backend, with a peer identity. REST and legacy WebSocket
routes call backend methods directly (`daemon/app/routers/move.py`), including
target, trajectory and raw-write paths. The media routes can independently
enable wobbling and tracking (`daemon/app/routers/media.py`). Existing daemon
speech offsets persist and are composed with targets. The app lifecycle lock
does not itself fence all of these entry points.

## Required properties

A future SDK and daemon lease would need the properties below. These are
engineering requirements for such a lease, not claims about current support
or a prerequisite imposed by the physical launcher.

1. Acquire a uniquely scoped lease atomically with source-state checks, and
   bind it to the actual transport connection and an unguessable generation.
   Refuse acquisition when an active trajectory, tracking, wobbling, residual
   speech offset, unknown command outcome or other unverified source exists.
2. Fence every motion-affecting ingress, including WebSocket SDK commands and
   tasks, REST, legacy streaming and raw writes, WebRTC, daemon-side sources,
   app lifecycle and internal motion paths. Admission must happen at a shared
   authority boundary; testing only the SDK route is insufficient. Safety
   STOP must remain available irrespective of lease ownership.
3. Make the lease a live authority, not a boolean status: renew it using a
   bounded watchdog, fence old generations and all subsequent commands on
   loss or expiry, and handle races between renewal, disconnect, dispatch,
   callbacks and expiry atomically. Do not silently transfer or reacquire it.
4. Provide correlated acknowledgments that distinguish rejection, admission,
   execution and unconfirmed outcomes. A queued or sent frame does not prove
   execution or settling. Check lease validity for each command, including
   long-running work, and define how in-flight work is fenced on expiry.
5. Expose source and lease evidence without disclosing credentials. An
   uncertain STOP or loss of communication leaves motion quarantined until
   deliberate operator reconciliation. Expiry or disconnect must not issue
   an automatic neutral, sleep, wake, reconnect or retry.
6. Define and test cancellation and shutdown independently of physical
   settling. Hardware neutral, motor inactivity and sleep require separate
   measurement or operator evidence.

## Qualification

Use fake backend and transport integration tests to race acquisition and target
submission across all ingress paths; inject disconnects, delayed commands,
lease expiry, stale generations, source activation and ignored commands.
Assert emergency STOP is never blocked. Verify no late target is admitted after
fencing, and preserve unknown outcomes when command or STOP evidence is lost.
Run upstream and dependent suites. The physical daemon must not be replaced or
restarted as part of these tests. Attended physical validation remains a
separate, explicitly approved step after the complete integration is reviewed.
