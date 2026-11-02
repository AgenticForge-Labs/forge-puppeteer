# Native conversational behaviors — PR 2 of 5

**Status:** Software integration on `agent/native-reachy-stackchan-behaviors`; validate the PR merge state before treating this as `main`. No physical robot is configured or qualified by this code.

## Architecture and identity

**4G remains the Conversationalist personality and tool-facing identity.**
The same content-free conversational events can animate Reachy Mini,
StackChan, SO-ARM101 or any future performer. Puppeteer owns expression,
motion recordings, named behaviors, procedural loops and resource policy.
Native SDKs are the sole owners of motor access, firmware protocol and
device-specific safety. Neither Conversationalist nor Platform is installed
by this package to build or run a performer pack.

PR 1 merged `forge.behavior.v1`, `ConversationEnsemble`, shared
ExecutionRuntime ownership and quarantining. PR 2 adds two **pack authors**
and an explicit Reachy asset installer. It does **not** activate native
ensemble MCP or a LiveKit bridge. Those belong in PRs 4 and 5.

## Reachy Mini: official recordings at one immutable revision

The repository already tracks the upstream recording manifest at
`examples/robots/reachy/emotions.manifest.json`:

- Dataset: `pollen-robotics/reachy-mini-emotions-library`
- Exact revision: `873ae49f0b89114b7e535eff0c1f7560d21d9357`
- SDK: `reachy-mini==1.10.0rc5`
- License/attribution: Apache-2.0 / Pollen Robotics; retain any required upstream notices

Before using native recorded emotion or yes/no gestures, an operator explicitly
installs that manifest (from a **trusted Git checkout**) into an otherwise
unused local directory:

```bash
mkdir -p /opt/agenticforge/puppeteer
python -m forge_puppeteer.reachy_sync \
  --manifest examples/robots/reachy/emotions.manifest.json \
  --target /opt/agenticforge/puppeteer/reachy-recordings
```

The downloader constructs only official HTTPS dataset URLs with the exact
pinned revision; prevalidates file names and SHA-256 digests; bounds file
counts, per-file/total download size; verifies every byte and the exact
snapshot inventory; and atomically installs the complete snapshot. Existing
destinations are verified and **never silently updated or repaired**. An
incomplete or mismatched download leaves no installed catalog. A reviewer may
later deliberately update the checked-in manifest and the operator's
explicitly selected new destination, subject to licensing and validation.

The sync command uses Python's standard library and does not open a robot,
start an SDK daemon, install an LLM or touch any conversational app. A separate
`ReachyCatalog` verifies the installed snapshot before the existing
`ReachyPlaybackProvider` can play an exact recorded move.

## Reachy program authoring

`reachy_conversation_pack(performer_id, neutral_head=...,
recording_revision=..., available_recordings=...)` creates:

- `idle`: gentle bounded vertical breathing around an explicit,
  operator-reviewed 4x4 neutral head transform. Default vertical amplitude
  is **zero** until physical frame/clearance qualification.
- `listening`: gentle pitched head attention pose, finite native SDK target
  steps in a repeating behavior.
- `speaking`: periodic small left/right head roll-like movement around
  the reviewed transform. This is a deterministic fallback, **not** true
  speech-derived audio/phoneme timing or emotional prosody.
- `yes`, `no` and `acknowledge`: exact pinned SDK recordings
  `yes1` and `no1`, with associated audio disabled.
- `neutral`: explicit return to the reviewed head transform.
- Optional `happy`, `curious`, `confused` and `excited` recordings,
  only when those exact recording names are present in the verified catalog.

The editor must **not** replace a missing recorded gesture with a random
upstream file. The separate `ReachyPlaybackProvider` already checks the
operator's motion authorization, exact asset ID/revision and current movement
resources for every execution. A 4x4 neutral transform is an operator
calibration input, not a universal physical "home" command. Native SDK
completion does not prove actual physical settling. Its STOP remains
unconfirmed for some operations, with enforced quarantine.

For the future PR 4 launcher, use the provided **`reachy_pack_from_catalog`** helper. It accepts only a `ReachyCatalog`, resolves available names from its verified immutable snapshot, and checks each asset's catalog revision before constructing the versioned pack. Do not trust an agent-supplied inventory or dynamically enable a new SDK owner.

## StackChan: SDK-native expressions

`stackchan_conversation_pack(performer_id, yaw_center=0,
pitch_center=450, yaw_amplitude=80, pitch_amplitude=45, head_speed=180)`
returns a signed content-revisioned `PerformerBehaviors` value with:

| Behavior | Native implementation |
| --- | --- |
| `idle` | Centered head, gentle yaw shifts, neutral mouth and blinking eyes |
| `listening` | Attentive eyes, mouth closed and modest pitch incline |
| `speaking` | Cycled mouth sizes 12/42/22/0, without separate speech generation |
| `yes` | Two bounded head pitch nods, then centered |
| `no` | Two bounded yaw shakes, then centered |
| `acknowledge` | Short affirmative pitch dip and settle |
| `neutral` | Mouth/eyes reset and centered head |
| `happy`, `curious`, `confused` | Finite face/head gestures using existing SDK controls |

The SDK uses integer **tenths of a degree**. The authoring bounds are a
conservative subset of fixed firmware protocol limits, but the current
connected firmware's `native_angle_limits_tenths` and permission grants
are still checked by `StackChanProvider.validate`. In particular,
**pitch 0 is invalid**; this pack uses a valid configurable center.

Puppeteer supplies `head` and `face` operation cues to the existing
`StackChanProvider`, which already delegates to the installed
`stackchan-control` SDK, uses its STOP generation, serial serialization and
native firmware command acknowledgements, and does not open a second USB
controller. No robot-specific personality, new firmware animation engine,
wire-protocol clone or new SDK motion semantics are introduced.

## Bounded cancellation and transition policy

These packs use only finite, explicitly time-bounded SDK commands. PR 1's
`ConversationEnsemble` owns the group scheduler and exclusive
`ExecutionRuntime` performer leases; new presence interrupts the previous
loop. If a command was admitted and then its outcome becomes ambiguous, the
existing provider-wide SDK STOP contract applies. An unconfirmed result
quarantines the individual performer; it is **not** retried or force-reset.

The current PR does not provide an independent safety-rated stop, physical
space measurement, RT audio playback start/finish callback, or physical
timing synchronization. Those require explicit operator validation and, for
SO-ARM, its SDK's distinct deterministic choreography PR 3.

## Test / release gate

CI installs `stackchan-control` from verified
`main@391ccb9b5ff777fbd88df6a97e2477161a34c7ec` into the
test worker without opening USB. The new tests cover protocol-native
Step parsing, bounded authoring, immutable revision pins, atomic asset import,
a corrupted downloaded file, missing/stale assets and the software
Reachy/StackChan providers driven by one `ConversationEnsemble`.

```bash
python -m pip install -e '.[dev,mcp]'
python -m pip install 'stackchan-control[serial] @ git+https://github.com/AgenticForge-Labs/stackchan-control-sdk.git@391ccb9b5ff777fbd88df6a97e2477161a34c7ec'
pytest tests/robots/test_conversation_native.py tests/robots/test_conversation_native_integration.py -q
pytest -q
ruff check src/forge_puppeteer/conversation_native.py src/forge_puppeteer/stackchan_validation.py src/forge_puppeteer/reachy_sync.py tests/robots/test_conversation_native*.py
```

**Local Codex after PR 5:** synchronize actual Reachy dataset bytes and verify
its full manifest, install exact Reachy SDK, confirm operator-approved neutral,
check actual StackChan USB firmware capability/pose and motion/STOP with
supervised hardware, and observe real start/settle times. No physical motion
is authorized merely because the software CI passes.
