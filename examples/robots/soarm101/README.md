# SO-ARM101 behavior pack fixture

`behavior-packs.json` uses only the broker's parameterless `sleep` operation,
which selects the broker's saved Sleep pose. The compiler pins this manifest by
SHA-256; live preflight still requires the configured broker to report readiness,
active authority, the `sleep` tool, and compatible resource metadata.

The `inspection-recording` pack demonstrates typed `replay_recording` with a
bounded speed and both an asset ID and SHA-256 revision. Replace the explicit
fixture ID/revision with a recording returned by the configured broker's
`list_assets`; live preflight checks that exact catalog revision, and the SDK
provider currently permits replay only in simulation-only mode. The pack does
not encode joint or Cartesian coordinates. The external SO-ARM broker remains
authoritative for permission, calibration, and physical safety.

## Puppeteer schema v4 SDK simulation launch

`v4-room-simulation.json` selects `v4-sdk-simulation.json`, whose explicit
`sdk_simulation_daemon` mode asks Puppeteer's v4 stdio launcher to start the
SDK's supervised simulation daemon, wait for readiness, and stop the owned
daemon when the MCP session closes:

```sh
forge-puppeteer-ensemble --config examples/robots/soarm101/v4-room-simulation.json
```

The SDK executable must be installed and discoverable as
`soarm101-sim-daemon`, or selected with `sdk_simulation_executable`. The state
directory must be unused. This sample uses a separate simulation-only trusted
choreography catalog to animate semantic idle/speaking through the SDK broker.
Its general broker and private host tokens are independently generated and
never passed in arguments or exported into Puppeteer's environment. The example
programs contain five arm joints only and cannot command the gripper. To
connect to an operator-started broker, use `mode: "existing_broker"` and set
the broker token in the named environment variable. Physical motion remains
disabled by this sample.

To expose a one-shot conversational response, add an operator-reviewed SDK
program to the trusted catalog, then add its exact `sha256:` revision and
`duration_s` to both `choreography_revisions` and
`choreography_durations_s` in the robot file. Supported names are `yes`,
`no`, `acknowledge`, `neutral`, `confused`, and `surprised`. Each configured
name becomes a finite semantic intent on this performer; no motion is inferred
or generated from the name. The SDK private catalog validates every configured
revision and duration before the room connects. `idle` and `speaking` remain
required presence loops when choreography is enabled; `listening` is optional
and otherwise uses the idle program. The sample intentionally does not invent
any of these expression programs.

## SDK physical daemon launch (unqualified)

Schema v4 also supports an explicitly selected `sdk_physical_daemon` mode. It
starts the SDK's `soarm101-physical-daemon --config <absolute-path>` as a child
process and never arms torque or changes permission. The private daemon JSON
must use the SDK's exact schema (`physical`, robot/port/calibration, lease,
catalog, loopback port and state directory); its broker URL and trusted
catalog path must match the robot file. The SDK still requires its own valid
human lease, calibration and operator-approved physical catalog. Broker and
trusted-host tokens stay in SDK-created private files and are never exported
into Puppeteer's environment or logged.

For an explicit operator-only trusted choreography session, set
`operator_choreography_only: true` in that private SDK daemon config and set
`lease_path: null`. The daemon then advertises only health, capabilities,
state, and STOP to agent clients; the SDK executor rejects all state-changing
agent calls even if the profile is broadened. Exact-pinned programs remain
available only through the authenticated trusted-host route. This mode does
not create or require an agent lease. Its watchdog validates the active SDK
session and bound robot/calibration, and its preflight still requires exact
identity plus stationary torque HOLD. Leave the flag false to preserve the
existing lease-gated autonomous agent mode.

Puppeteer waits a bounded time for authenticated broker health/capabilities and
the trusted catalog, then stops only the child it started with SIGTERM when the
MCP session ends. A timeout during stop is reported as an uncertain STOP; there
is no forced kill, retry, rearm or fallback to an existing broker. This launch
path is software wiring only and is **not hardware-qualified**. Use attended
operator validation before any live motion; retain unknown-outcome quarantine
until STOP and sensed settled-state behavior are physically established.
