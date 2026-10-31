# Robot package deployment

## Package extras and boundaries

Forge Puppeteer has two optional dependency groups:

- `.[dev]` installs the repository's pytest/Ruff development tools.
- `.[mcp]` installs MCP v2 (`mcp>=2,<3`) for the packaged MCP server modules.

The base wheel needs only Pydantic, AnyIO and PyYAML. Importing the package,
using the mock CLI, or inspecting contracts does not import a robot SDK or
connect to hardware. The MCP extra provides the packaged
`forge-puppeteer-mcp` stdio launcher. Its `fake` backend is intended for wire
acceptance and deterministic offline examples. The `reachy_catalog` backend is
discovery-only and requires the pinned Reachy extra plus an explicitly staged,
verified snapshot. An operator config selects one device's fixed tool schema
and allowlist:

```bash
python -m pip install 'forge-puppeteer[mcp]==0.1.0'
forge-puppeteer-mcp --config /etc/forge-puppeteer/server.json
```

See [`examples/robots/mock-mcp-server.json`](../examples/robots/mock-mcp-server.json)
for a fake-backed StackChan selection. The config must set `backend` to `fake`,
choose `stackchan`, `reachy`, or `soarm101`, and provide the exact `allowed_tools`.
The fake backend never imports SDKs, scans ports, or makes hardware connections.
The packaged `reachy_catalog` backend provides offline asset discovery only.
It loads the exact pinned SDK and a staged manifest, and deliberately creates
no daemon client. Reachy also has an opt-in `reachy_sdk` backend for operator
controlled motion. The pinned SDK constructor connects immediately, so this
backend requires an explicit host, port, and `localhost_only` or `network`
connection mode; `auto` discovery and daemon spawning are never used. It creates
the SDK client only inside server lifespan and invokes the SDK context cleanup
after runtime tasks are cancelled. Its authority declaration is a local
configuration gate, not an independently verified safety grant. The SDK's
`ready` value means only that SDK construction succeeded; it is not a separate
daemon-health, calibration, or hardware-safety check. The launcher selects the
SDK's `no_media` backend, so recorded moves run without audio sidecars.
`async_play_move` return is not measured physical completion. SDK `cancel_move`
and native `goto_target` have no confirmed stop acknowledgment; a stop request
therefore reports unconfirmed and quarantines the process against further motion.
Operator verification and process restart are required to clear that quarantine.
`scripts/reachy-sdk-api-smoke.py` inspects the installed pinned class and method
signatures without constructing a client. The offline package smoke also runs
the installed wheel's Reachy MCP stdio lifecycle with an injected fake SDK,
checks the fixed constructor options and allowed-tool filtering, exercises a
recorded move, and verifies SDK context cleanup. Neither check opens a daemon
connection or requires hardware.
StackChan's
compatible SDK facade is unpublished, and SO-ARM must connect through its
separately installed broker. SO-ARM may now be launched as `soarm101_broker`,
which delegates only through an operator-configured broker subprocess and never
creates a direct controller. The trusted config must set
`physical_authority` to `I_AUTHORIZE_BROKER_CONTROL` when broker-backed motion is
intended. This declaration is a deployment gate, not a grant: the broker must
independently report live tools and authority on each request. Configure
`broker_command`, optional `broker_args` and `broker_env`, and broker tool names
in `allowed_tools`. The subprocess is launched without a shell and is closed
with the Puppeteer server lifecycle.

```json
{
  "backend": "soarm101_broker",
  "device": "soarm101",
  "instance_id": "arm",
  "physical_authority": "I_AUTHORIZE_BROKER_CONTROL",
  "broker_command": "/usr/local/bin/soarm101-mcp",
  "broker_args": [],
  "allowed_tools": ["robot_capabilities", "robot_state", "list_assets", "go_pose", "stop"]
}
```

No standalone SO-ARM SDK dependency is pinned here: this integration speaks the
existing MCP broker contract and leaves broker version/profile pinning to the
robot deployment. The broker remains responsible for actual authority. Packaged
stdio tests use a fake broker process only.

For the simulation-only pinned sequence/recording replay bridge, run
`bash scripts/soarm101-wheel-smoke.sh`. It copies each current working tree's
`pyproject.toml`, `README.md`, and `src/` into temporary source snapshots,
builds fresh Forge and SO-ARM wheels, stages the runtime dependency closure,
and installs those wheels into a new Python 3.12 environment with `--no-index`.
The installed Forge MCP launcher delegates over stdio to the installed SDK MCP
server, which talks to an authenticated loopback SDK simulation broker. The
smoke checks simulation mode, exact catalog revision, stale-revision rejection,
pinned sequence completion (gripper and wait steps), a generated pinned
recording behavior pack with stale-pin preflight denial, a bounded simulated
joint action, and holding-confirmed SDK STOP. It never opens a serial device or
starts physical mode. Without an argument, only dependency staging can access
the package index; a supplied wheelhouse must contain the complete runtime
dependency closure for Forge's `mcp` extra and the SDK on CPython 3.12 for the
current OS and architecture. The final install is always offline.
The smoke invokes one sequence replay and one recording replay, each in its own
simulation broker session; both are driven through the behavior-pack client.
The SDK reserves each replay identity and requires confirmed
STOP before another replay; in the SDK simulation backend that STOP ends the
session, so recording replay coverage requires a second fresh broker session.

This is repeatable installed-wheel evidence for the *current local source
snapshots*, not a release pin or deployment guarantee. SO-ARM SDK version
`0.1.0.dev8` and the compatible Forge bridge are unpublished here; a fresh
build from identical source snapshots and the same dependency wheelhouse is
required to reproduce it. The script copies current `src/` trees (including
local edits and added files), but the wheel metadata does not identify their
Git revisions. Default dependency staging uses version ranges and can resolve
different versions on a later run; exact dependency reproduction requires
retaining the complete wheelhouse and its hashes. The generated replay assets and profile are
temporary smoke fixtures, not production operator policy. The ordinary
deployment backend still relies on the separately installed broker and
operator-selected profile. Simulation STOP evidence does not establish
physical stopping, replay safety on a real arm, or production qualification.

There is no published SDK extra for StackChan. Reachy's `reachy` extra pins
`reachy-mini==1.10.0rc5`; install it from an online package index or prebuilt
wheelhouse. The
StackChan provider uses an evolving SDK API that is not yet represented by a
released compatible dependency. Do not substitute a mutable local checkout or
broad SDK version range for the Reachy deployment pin. The external SO-ARM
broker remains separately installed and is not a Puppeteer physical-control
bypass.

An opt-in `stackchan_sdk` backend is available when the operator has installed
the compatible `stackchan-control` wheel with its `serial` extra into the same
Python environment as Puppeteer. Puppeteer declares no StackChan extra or
guessed version pin. The trusted server config must name the installed
distribution version exactly, select a USB port, declare physical authority,
and enumerate the tools to enable:

```json
{
  "backend": "stackchan_sdk",
  "device": "stackchan",
  "instance_id": "stackchan",
  "physical_authority": "I_AUTHORIZE_STACKCHAN_CONTROL",
  "port": "/dev/ttyACM0",
  "sdk_version": "<exact installed stackchan-control version>",
  "allowed_tools": ["device_info", "device_state", "head_move", "face_update", "device_stop"]
}
```

The launcher verifies the installed distribution version before startup and
opens only the configured port when its MCP lifecycle begins. It closes the SDK
client during server shutdown after the runtime cancels active work. A deployment
must obtain and install a compatible wheel separately; source checkouts and
unpublished package versions are not deployment inputs. The authority setting
is a local operator gate, not a substitute for attended hardware qualification.

`scripts/stackchan-pty-stdio-smoke.py` verifies the installed launcher against
the installed SDK using a local pseudo-terminal and a small protocol emulator.
The emulator responds to capabilities/state discovery, face update, and stop;
the smoke checks MCP discovery, action completion, and stop confirmation. It
does not test M5Stack hardware, modified firmware, USB enumeration, physical
motion, settling, reconnect behavior, cancellation under device load, or asset
playback. The fake firmware reports command completion itself, so this is
packaging and software-protocol integration evidence only.

Example operator config for offline Reachy catalog discovery:

```json
{
  "backend": "reachy_catalog",
  "device": "reachy",
  "instance_id": "reachy",
  "name": "Reachy offline catalog",
  "allowed_tools": ["device_info", "device_state", "list_assets", "action_lookup"],
  "asset_root": "/var/lib/forge-puppeteer/reachy/assets",
  "manifest": "/var/lib/forge-puppeteer/reachy/manifest.json"
}
```

Install with `pip install 'forge-puppeteer[mcp,reachy]==0.1.0'`. The launcher
checks the exact installed SDK version and verifies the staged inventory at
startup and before catalog reads. It never constructs a `ReachyMini` daemon
client. The catalog can be exercised with a fake SDK move factory in tests;
real motion is not available through this backend.

## Offline wheel smoke

With `uv`, Python 3.12, and pip available, run:

```bash
bash scripts/offline-package-smoke.sh
```

The script builds fresh Forge Puppeteer and StackChan SDK wheels, stages those
wheels with their dependencies, then creates a fresh temporary virtual
environment and installs entirely offline with `--no-index`. With no argument,
the staging phase uses pip and may access the package index. For a fully
air-gapped run, pass a wheelhouse as the first argument; it must contain the
complete runtime dependency closure for `forge-puppeteer[mcp]` and
`stackchan-control[serial]`, compatible with CPython 3.12, OS, and architecture.
The two project wheels are built locally and added to that wheelhouse before the
offline install. For example, prepare a temporary wheelhouse while online with:

```bash
mkdir -p /tmp/forge-puppeteer-wheelhouse
# First build both project wheels locally; pip then stages their dependency closure.
uv build --wheel --out-dir /tmp/forge-puppeteer-wheelhouse .
uv build --wheel --out-dir /tmp/forge-puppeteer-wheelhouse \
  /home/larry/code/stackchan-control-sdk
python3.12 -m pip download --dest /tmp/forge-puppeteer-wheelhouse \
  "$(find /tmp/forge-puppeteer-wheelhouse -maxdepth 1 -name 'forge_puppeteer-*.whl' -print -quit)[mcp]" \
  "$(find /tmp/forge-puppeteer-wheelhouse -maxdepth 1 -name 'stackchan_control-*.whl' -print -quit)[serial]"
bash scripts/offline-package-smoke.sh /tmp/forge-puppeteer-wheelhouse
```

Without an explicit wheelhouse, only dependency staging accesses the network;
the subsequent fresh-environment installation is offline and index-free. The
smoke runs
`forge-puppeteer validate` and mock `execute`, then starts the installed
`forge-puppeteer-mcp` entry point as a subprocess and uses MCP ClientSession over
stdio to list catalogs and complete a deterministic fake action for each of the
three device schemas. It also starts the installed StackChan launcher against
the PTY firmware emulator described above. This is software wire/package
evidence only; it does not exercise hardware.

For air-gapped deployment, populate a wheelhouse from the same reviewed lock
and platform/Python target before disconnecting the machine, then install the
wheel and dependencies with pip's `--no-index --find-links <wheelhouse>`. Keep
the wheelhouse manifest and hashes with the deployment. `uv.lock` records the
repository's development resolution; it is not yet a lock for optional SDK
extras or every target platform.

To check the Reachy extra and catalog backend in a clean installed wheel, stage
the exact snapshot as described below and run
`scripts/reachy-catalog-stdio-smoke.py <asset-root> <manifest>` with the
installed environment active. The smoke paginates the catalog, checks the exact
SDK version and disconnected state, and confirms no playback tool is exposed.

## Reachy asset staging

Reachy assets are large external data and are not embedded in the wheel. Follow
[`examples/robots/reachy/README.md`](../examples/robots/reachy/README.md) to
fetch the locked upstream dataset revision while online, preserve its license
and attribution files, and build a trusted operator manifest over the staged
files. Transfer the staged directory and manifest together. At startup,
`ReachyCatalog` verifies the exact inventory and file hashes before loading
moves. The catalog's content revision is useful for detecting drift, but does
not authenticate a manifest or grant authority to play a move.

Offline catalog loading requires the pinned Reachy SDK, the staged asset bytes,
and the manifest. Clean wheel installation evidence must use a wheelhouse that
contains the full `reachy-mini==1.10.0rc5` dependency closure for the target
Python and platform; the ordinary MCP smoke does not install this optional extra.
Example config for the operator-authorized SDK backend:

```json
{
  "backend": "reachy_sdk",
  "device": "reachy",
  "instance_id": "reachy",
  "physical_authority": "I_AUTHORIZE_REACHY_CONTROL",
  "sdk_version": "1.10.0rc5",
  "host": "127.0.0.1",
  "daemon_port": 8000,
  "connection_mode": "localhost_only",
  "asset_root": "/var/lib/forge-puppeteer/reachy/assets",
  "manifest": "/var/lib/forge-puppeteer/reachy/manifest.json",
  "allowed_tools": ["device_info", "device_state", "list_assets", "play_move", "device_stop"]
}
```

Reachy playback, cancellation guarantees, and physical acceptance are not
established by catalog loading.

## Local server configuration and authority

MCP servers use stdio and should be launched only from trusted local
configuration. Stdio is not authentication; do not expose these servers on a
network without an authenticated deployment boundary. Server startup or catalog
discovery does not arm a device. SO-ARM physical authority continues to be
owned by its broker and current operator grants. Mock package smoke results
must not be reported as hardware readiness, motion completion, or physical
acceptance.

Set `FORGE_PUPPETEER_ACTION_JOURNAL` in the trusted server launch configuration
to an operator-selected SQLite file, or pass that path explicitly as
`ExecutionRuntime(journal_path=...)`. The common server lifespan cancels and
awaits in-flight software tasks before closing the journal;
an interrupted action is recorded as unknown and reserves its performer until
an independent stop is confirmed after restart. Keep the journal on durable,
private local storage and do not point multiple server processes at the same
SQLite file.
