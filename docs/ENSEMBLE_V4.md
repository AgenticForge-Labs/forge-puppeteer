# File-composed ensemble configuration (schema v4)

Schema v4 is an opt-in room format for selecting any nonempty combination of
Reachy, StackChan, and SO-ARM101. Schemas 1–3 keep their existing formats and
startup paths.

The room file owns common journal/ownership paths and lists the selected robot
files. Relative `file` values resolve from the room file's directory. Each
robot file repeats its kind and performer ID so both the launcher and callers
can reject mismatches before constructing a provider.

```json
{
  "schema_version": 4,
  "physical": true,
  "robots": [
    {"kind": "stackchan", "id": "stage-stackchan", "file": "robots/stackchan.json"}
  ],
  "action_journal": "/var/lib/puppeteer/actions.db",
  "ownership_db": "/var/lib/puppeteer/owners.db"
}
```

Robot file shape is `{ "kind": "stackchan", "id": "stage-stackchan",
"provider": { ...existing provider configuration... } }`. The `provider`
object uses the existing `ServerConfig` fields for Reachy and StackChan, and
an HTTP SDK broker binding for SO-ARM101. The SO-ARM file must explicitly set
`mode` to `existing_broker`, `sdk_simulation_daemon`, or `sdk_physical_daemon`; the former connects to
the selected loopback broker and never manages its lifecycle. The latter starts
the SDK's supervised simulation daemon from Puppeteer's v4 serve lifecycle,
waits for SDK health, and stops it on MCP shutdown only when this launcher
created it. It requires `simulation_state_dir` and matching `simulation_port`;
existing or ambiguous state fails closed. The daemon owns its SDK session,
independent watchdog heartbeat, and STOP path. Its generated token stays in
the private SDK state directory and process environment, never command
arguments or logs. `sdk_simulation_executable` selects the installed SDK
`soarm101-sim-daemon` executable (default: PATH lookup). A selected Reachy
also requires the room's absolute `reachy_neutral_head` path.

Run `forge-puppeteer-ensemble --config room.json --check-config` for a local
configuration check. It does not connect to devices or qualify hardware.
Conversationalist reads the same room and robot files and fences their bytes
after validation. SO-ARM discovery is read from the configured broker. Its
idle/listening/speaking patterns are position holds with no cues when no choreography is configured. `go_pose`
and mutable saved poses are not used for presence.

Optional SO-ARM presence movement is configured by `choreography_revisions`,
`choreography_durations_s`, and `trusted_choreography_catalog` in the robot
file. In `sdk_simulation_daemon` mode, Puppeteer passes that explicit file to
the existing SDK daemon's `start` command. The daemon loads it through the
SDK broker catalog parser, requires exact SHA-256 content revisions, and
rejects catalogs that permit physical trials. The SDK generates distinct
private broker and trusted-host tokens in its mode-0700 state directory;
Puppeteer reads them from those private files without changing process
environment variables. Neither token is placed in argv or logs.

Puppeteer preflights the live SDK catalog against the configured revisions
and durations before serving actions. Only exact finite, joint-only programs
can run through the existing room `ExecutionRuntime`; gripper motion is not a
choreography capability. `examples/robots/soarm101/v4-sdk-simulation.json` and
`v4-trusted-choreography.json` provide a sanitized simulation-only example.
The simulator and its host watchdog remain SDK-owned. This path does not
qualify any physical operation.

`physical: true` on the room is the single grant for driving real robots (Reachy,
StackChan, an SDK physical daemon); `false` is for simulated SO-ARM rooms. There are
no per-device `I_AUTHORIZE_*` markers. Sessions start at each robot's sleep pose (the
room checks it and asks; see `AGENTS.md`), so owned physical choreography needs a
pinned `startup_target_revision` that wakes the arm to neutral. Exact pinned programs may have an
SDK-policy-bounded duration up to 30 seconds. Normal presence changes are
admitted promptly and coalesced while the current trusted finite segment
finishes. Interruptions, disconnect, heartbeat expiry and shutdown use the
existing STOP path. Runtime execution has a duration plus five second deadline;
a timeout has an unknown outcome and requires STOP and reconciliation.
Authored segments are never retimed automatically. SDK completion does not
establish physical settling.

## Reachy and StackChan behavior options

A Reachy or StackChan robot file may carry an `options` object next to `provider`.
Options choose how the standard behaviors are authored and which opt-in motion
variants run; they never grant device access (that is `physical: true` plus the
provider's tool grants). Only the options in current use are supported.

Reachy (`options` in `reachy.json`), defaults first:

| Option | Values |
|---|---|
| `expression_style` | `classic`, `expressive` |
| `startup_behavior` | `none`, `wake_up_if_sleeping` (needs the `wake_up` grant) |
| `listening_mode`, `listening_tilt_rad` | `tilt` or `hold`; tilt 0-0.07 rad |
| `speaking_axis`, `speaking_amplitude_rad` | `yaw` or `tilt`; 0-0.07 rad |
| `expressive_amplitude_rad`, `antenna_sway_rad`, `breath_amplitude_m` | 0-0.12 rad, 0-0.26 rad, 0-0.005 m |
| `neutral_antennas_rad` | `[left, right]`; required for expressive motion |
| `idle` | `default`, `original_v1` (needs the `original_idle` grant and an identity neutral head) |
| `speaking` | `default`, `smooth_v2` (needs `speech_driven: true` and the `pose_and_antennas` grant) |
| `speech_driven` | motion follows the audio-envelope features |
| `legacy_speech_fallback` | rhythmic head motion when features go stale (needs `smooth_v2`, classic style) |

StackChan (`options` in `stackchan.json`): `speaking_mode` is `legacy_size` or
`native_weight`; `speech_driven` is a boolean. SO-ARM101 takes no options.

To convert an old v2 (Reachy) or v3 (Reachy + StackChan) config, run
`python scripts/migrate-room-config.py OLD.json OUT_DIR`. It writes new files, never
overwrites, and stops with a message on any option v4 no longer supports.
