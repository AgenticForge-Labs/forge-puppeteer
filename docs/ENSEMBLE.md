# Ensembles

An **ensemble** is one running `forge-puppeteer-ensemble` process plus the robots it
controls: one to three of Reachy Mini, StackChan and SO-ARM101, one action journal, and
one conversation at a time. A caller such as Conversationalist (or Studios, later)
connects over stdio MCP and sends presence (`idle`, `listening`, `speaking`) and named
gestures; the ensemble runs each robot's standard behaviors through the shared
`ExecutionRuntime`. It works the same with no LiveKit, Conversationalist or Platform.

(Older docs and code say "room". That is LiveKit's word; the Puppeteer term is
*ensemble*.)

## Files

An ensemble is an *ensemble file* that lists one *robot file* per robot. Relative
`file` paths resolve from the ensemble file's directory. Each robot file repeats its
`kind` and `id`, so mismatches are rejected before any provider is built.

```json
{
  "schema_version": 4,
  "physical": true,
  "robots": [
    {"kind": "reachy", "id": "reachy", "file": "reachy.json"},
    {"kind": "stackchan", "id": "stackchan", "file": "stackchan.json"}
  ],
  "action_journal": "/absolute/path/actions.db",
  "ownership_db": "/absolute/path/owners.db",
  "reachy_neutral_head": "/absolute/path/neutral.json"
}
```

A robot file is `{"kind": ..., "id": ..., "provider": {...}, "options": {...}}`.
`provider` is the connection and tool grants (Reachy/StackChan use the SDK provider
fields; SO-ARM101 uses an HTTP broker binding). `options` is optional (below).

`physical: true` is the single grant for driving real robots (Reachy, StackChan, an
SDK physical daemon). `false` is for simulated SO-ARM ensembles. There are no
per-device authorization markers. A selected Reachy also needs the absolute
`reachy_neutral_head` path.

Only schema version 4 is supported. Convert an older config with
`python scripts/migrate-room-config.py OLD.json OUT_DIR` (writes new files, never
overwrites, and stops on any option that no longer exists).

## Running

```bash
forge-puppeteer-ensemble --config /absolute/path/ensemble.json --check-config   # no devices touched
forge-puppeteer-ensemble --config /absolute/path/ensemble.json                   # serve over stdio
```

At startup the ensemble reads each real robot's pose and compares it with its standard
sleep pose (`src/forge_puppeteer/poses/`, ±5° by default). A robot that is not at sleep
and has a `sleep_target` is laid down at sleep first (one move, never retried), so every
conversation starts at sleep and then wakes the same way. A robot that cannot get there
stays still; the caller relays the per-joint deltas and asks. A held robot can be answered
while the conversation carries on with the other robots (it joins at the next presence
change). `ensemble_state` reports `startup_complete` (per robot and overall) once the
wake-up motion has finished and settled; a caller should not speak before then. MCP tools:
`ensemble_startup_check` (re-read, no motion), `ensemble_prepare` (`move_to_sleep` /
`confirm_at_sleep`), `ensemble_park` (end of session: sleep move, re-check, report; refused
during a conversation unless `settle_session` is true, which lets the running behaviors finish
without a STOP and releases the robots first, so the caller should disconnect afterwards),
`ensemble_recover`
(lift a quarantine once the robot is seen settled). See `AGENTS.md` for the working
rules, including that faults stop the run and are never retried.

## SO-ARM101

The robot file's `provider.mode` is one of:

- `existing_broker`: connect to a loopback SDK broker; never manage its lifecycle.
- `sdk_simulation_daemon`: start the SDK's supervised simulation daemon for the life
  of the ensemble (needs `simulation_state_dir` and a matching `simulation_port`;
  existing or ambiguous state fails closed). Skips the sleep check.
- `sdk_physical_daemon`: start the SDK's physical daemon (needs `physical: true`,
  `physical_daemon_config`, and for choreography a pinned `startup_target_revision`
  that wakes the arm to neutral). Stale state from a dead daemon is cleaned up
  automatically; a running daemon is never taken over.

The daemon owns its SDK session, watchdog and STOP path. Its tokens stay in the private
SDK state directory and process environment, never in arguments or logs. Presence
movement is optional: `choreography_revisions`, `choreography_durations_s` and
`trusted_choreography_catalog` pin exact finite, joint-only programs by SHA-256
(gripper motion is not a choreography capability; durations are SDK-policy-bounded, up
to 30 seconds). Named taught poses are exposed with `pose_targets`;
`sleep_target` names the `pose_targets` entry used for `move_to_sleep` and
`ensemble_park`. `sleep_check: false` turns the startup check off, for a simulated
broker only. Examples: `examples/robots/soarm101/v4-*.json`.

### Live presence (breathing, listening, audio-synced talking)

Pinned programs each start and end at rest, so presence built from them pauses between
motions and cannot follow the voice. With a `live_presence` block in the provider, the
idle, listening and speaking states instead run as one SDK live stream
(`/v1/trusted/stream/*`): Puppeteer sends small joint *offsets* about 20 times a second and
the SDK clamps them to an operator-pinned `JointEnvelope` (centre pose plus at most 15° per
joint, listed under `envelopes` in the trusted catalog), slews them under the pinned speed and
acceleration limits and writes every sample through its guarded stream. If offsets stop the arm
returns to the centre; a STOP or watchdog trip ends the stream. Talking nods follow the
synthesized speech level (`speech_driven: true`; the same generated-audio features Reachy
uses) and fall back to a gentle rhythm when none arrive. Gestures (`yes`, `no`, `curious`),
the wake move and the sleep move stay pinned programs: the ensemble closes the stream
(back to the centre pose), runs the program, and reopens it.

```json
"live_presence": {
  "envelope": "presence", "envelope_revision": "sha256:…",
  "breath_delta_deg": [0, -0.8, 4, -2.1, 0], "max_offset_deg": [0, 6, 6, 6, 6],
  "breath_period_s": 4.0, "talk_amplitude_deg": 5.0, "speech_driven": true
}
```

`scripts/author-soarm-live.py CONFIG_DIR` pins the envelope and adds the block (it needs the
SDK's Python environment). The old `idle`/`speaking` programs may stay pinned; they are unused.

With `operator_choreography_only: true` in the private daemon config, no agent lease is
created, agent state changes are denied by the SDK, and only the exact pinned
choreography runs, through the authenticated trusted-host route.

## Reachy and StackChan options

`options` chooses how the standard behaviors are authored and which opt-in variants
run. It never grants access (that is `physical` plus the provider's tool grants).

Reachy, defaults first:

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

StackChan: `speaking_mode` is `legacy_size` or `native_weight`; `speech_driven` is a
boolean. SO-ARM101 takes no `options`; its live presence is configured in the provider.
