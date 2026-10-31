# SO-ARM101 direct broker → Puppeteer MCP migration

**State:** implemented on a Puppeteer development branch, unqualified for
hardware or production; SDK-owned MCP remains usable pending parity
acceptance. First dependency in the
[robot consolidation sequence](ROBOT_SDK_MCP_CONSOLIDATION.md).

## Runtime ownership

```text
Any MCP client
      |
forge-puppeteer-mcp --config <operator-private-config>
      |
Puppeteer MCPServer (stdio, tool whitelist, ownership, action history)
      |
SOARM101Provider + SOARM101BrokerHTTP
      |
SDK BROKER_CAPABILITY_ROUTES / authenticated HTTP /v1/*
      |
SDK broker permissions, leases, replay policy, watchdog, STOP
      |
SOARM101 native motion SDK, calibrated backend or simulation
```

There is no SDK MCP subprocess in the **new direct transport**. The
`SOARM101BrokerHTTP` interface reads the SDK's
`BROKER_CAPABILITY_ROUTES` rather than defining a second authoritative
route map. The SO-ARM101 Motion SDK remains independently usable
without Puppeteer. Puppeteer MCP-only operation does not need Platform,
Studio, Docker, OpenShell or model-provider credentials.

The existing `soarm101_broker` launcher backend now accepts
`broker_url` instead of `broker_command`. The former accesses the
locally running SDK broker over HTTP with an environment-only token;
the latter is a temporary compatibility path kept for old wheel
and OpenShell qualification. Exactly one is allowed. An agent may
not supply a broker path, token, arbitrary remote URL or executable
through MCP tools. Only explicit localhost URLs are accepted.

## Example configuration

`examples/robots/soarm101/direct-broker-simulation.json` is
**simulation-only** and safe to commit. It contains no credentials.
Configure the trusted environment in the **Puppeteer process**:

```bash
# Local, separately started SDK simulated broker required.
# Supply SOARM101_BROKER_TOKEN to the trusted launcher environment
# without logging or committing its value. Do not expose a supervisor
# STOP credential in an agent's MCP request/environment.
forge-puppeteer-mcp --config examples/robots/soarm101/direct-broker-simulation.json
```

Install both the standalone SO-ARM101 SDK wheel and
`forge-puppeteer[mcp]` in the same Python environment. The native SDK
works without Puppeteer; Puppeteer works without Platform or Studio.
The optional script
`scripts/soarm101-wheel-smoke.sh` builds **both** wheels into an
isolated environment, installs the SDK without its `[mcp]` extra,
and exercises replay and MCP stdio against a separately spawned
SDK **simulation broker**. The SDK-owned `soarm101-mcp` executable
must not appear anywhere in the new wheel test's process chain.

The example does not start a broker, connect a robot, arm torque, create
an operator lease, or assert camera availability. Its `simulation_only`
mode requires fresh broker state proving `simulation_actions` before
the adapter reports readiness. The broker independently enforces
profile and watchdog rules on each request. This is **not** permission
to operate physical hardware, and the sample contains no real secrets.

## MCP functionality ported / shared

- Common Puppeteer MCP discovery, action lookup, ownership,
  cancel, device stop and asset listing.
- SO-ARM-specific broker health, capabilities and state tools.
- `go_pose`, signed small `jog_joint`, `jog_cartesian`,
  `move_gripper`, `sleep`, `sleep_up`.
- Simulation-only `replay_recording` and `replay_sequence`
  through SDK broker's original pinned asset/session/revision
  checks. Puppet cannot weaken the replay policy.
- `capture_camera` checks the broker's actual JPEG SHA-256 before
  returning image content and permits only safe metadata; this
  does not grant access to unadvertised cameras.
- Migrated `soarm101://guidance/{core,coordinates,joints,vision,recovery}`
  resources, `operate_robot_task` prompt and MCP server
  instructions. All are non-authoritative *reasoning* guidance.
- Broker grants are filtered in the Puppeteer server at registration
  and checked again against live `GET /v1/profile` before any
  broker operation; SDK also enforces its own profile/lease limits.
- Simulation mode is checked **on each action validation**, rather
  than cached from initial connection. STOP/HOLD requires explicit
  broker-confirmed stop, not merely a status label.
- The direct transport cannot follow HTTP redirects or environment
  proxies, both of which could leak the broker bearer token.
- This is a **new** MCP action lifecycle interface: compared with
  SDK `soarm101-mcp`, motion calls take Puppeteer's
  `action_id`, `caller_id`, `session_id` and typed parameters.
  Tool *capability* parity does not mean byte-for-byte argument-schema
  compatibility. External agent configurations require a planned
  migration to these tool signatures.

The direct HTTP client intentionally returns a small compatibility
object matching the previous internal broker-session methods. It is
not a second MCP server and does not send MCP over HTTP to the SDK.

## Local Codex acceptance before marking migration complete

Repository: `AgenticForge-Labs/forge-puppeteer`, branch
`agent/soarm101-broker-native-mcp`. Use the final exact PR SHA.
Sibling: `AgenticForge-Labs/soarm101-motion-sdk` `main` at
`8eec5770e9670799b91ccd103966c4627d6907c4`
or newer confirmed merge. The SDK's broker and test fixtures
are required for the installed-wheel simulation step; **no
connected robot, camera or live hardware device** is required.

First, on the Puppeteer source tree with dev/MCP optional deps:

```bash
uv run pytest tests/robots/test_soarm101_broker_http.py -q
uv run pytest tests/robots/test_soarm101_provider.py \
  tests/robots/test_soarm101_mcp.py \
  tests/robots/test_packaged_launcher_soarm.py -q
uv run pytest -q
uv run ruff check .
git diff --check
```

Then separately run SDK non-hardware tests and the existing
installed wheel smoke in a clean environment; update the wheel
smoke to target `broker_url`, **not** SDK's
`soarm101_motion.mcp_server`, and verify:
- Broker tools/list filtered by active source profile and read-only
  default, with no hidden tool reachable by direct call;
- Both simulation and refused physical-config modes; no automatic
  arming or motion beyond explicitly approved SDK simulation;
- Camera bytes/digest, provenance, no host path disclosure;
- Expired/superseded broker token, stale asset revision,
  profile removal, missing SDK, invalid URL, unknown result;
- Cancellation with STOP/HOLD and no late action success or
  duplicate replay after ambiguous outcomes;
- Original Hermes/Codex OpenShell client-only bundle behavior,
  separate trusted host supervisor token and no raw USB mounts;
- Exactly one SDK broker owns an arm; simultaneous MCP clients
  cannot create two independent motor owners.

No local tests have been executed through the GitHub connector;
no physical test is implied by this branch. If mismatched SDK
versions or MCP v2 tool registration fail the installed-wheel
checks, **repair this branch before superseding the old MCP server**.
The SDK MCP files, entrypoint and `[mcp]` extra must remain until
the dependent SDK cleanup PR is accepted.

## Deferred StackChan phase

See [StackChan plan](ROBOT_SDK_MCP_CONSOLIDATION.md).
Do not implement StackChan adapter/firmware changes on this
branch. Its native capabilities, USB serial ownership,
unknown-outcome handling and firmware-side safety remain
in `stackchan-control-sdk`; only after the SO-ARM installed
wheel and parity gates are accepted should we apply the
common Puppeteer MCP construction pattern to StackChan.
