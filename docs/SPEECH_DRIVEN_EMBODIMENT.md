# Provider-neutral, audio-reactive embodiment plan

Status: **software primitives only**, not a connected audio-to-motor pipeline.
The normal classic and expressive behavior packs remain unchanged.
Physical qualification is separate from source merge and CI.

## Authority and reuse

- Conversationalist owns LiveKit, speech generation, the authorized room and
  outgoing PCM analysis. It emits content-free 50 ms
  \`forge.speech_envelope.v1\` features, not vendor-specific motion.
- Puppeteer owns semantic expression mapping, room/session ordering, action IDs,
  performer catalog, resource arbitration, runtime journal and STOP evidence.
- Native robot SDKs own physical commands, calibration, path limits, timing
  capabilities, safety and device-specific stop behavior.
- Platform owns cross-system observability, privacy, OTel export and replay
  policy; it is not a second motion scheduler.
- Reachy Mini, StackChan and SO-ARM101 are **performer types**, not personas.
  A shared voice agent can drive three different styles, each with independent
  authoring, hardware grants and completion records.

## What is implemented

1. Conversationalist merged PR #18 provides a generated-PCM analyzer
   based on the pinned Reachy Mini speech-tapper RMS / hysteretic VAD /
   attack-and-release envelope, emitting bounded intensity and activity.
2. This Puppeteer branch implements a deterministic *dimensionless*
   6-axis/antenna/mouth expression texture using the pinned Reachy frequencies.
   Separate seeded phases prevent identical lockstep while preserving
   a common speech-driven envelope. Its optional room-owned MCP ingestion tool
   validates at most eight hops per batch and exposes read-only preview state.
   There is **no actuator adapter** here.
3. Puppeteer PR #19 merged a human-observed, offline reconciliation procedure
   that preserves historical UNKNOWN action outcomes and quarantine evidence.
4. Puppeteer PR #20 remains an unmerged and physically unqualified measured
   neutral-return proposal. Its own acceptance gate must be met before merge.

## Exact contract

Each outgoing synthesized PCM hop is:

- schema = forge.speech_envelope.v1
- stream_id: nonempty opaque identifier; one stream per generated segment
- sequence: monotonically increasing integer, starting at 0
- offset_ms = sequence * 50; duration_ms = 50
- active: hysteretic synthesized-speech activity
- intensity: finite [0, 1] smoothed loudness
- rms_dbfs: finite [-120, 0]
- timing_source = generated_pcm
- acoustic_playout_measured = false

No transcript, raw PCM, microphone stream, business record, API token,
absolute pose, joint target, provider secret or customer data enters this
contract. No timestamps from one host should be presented as acoustically
synchronized with another host without calibration.

The reusable Puppeteer expression shaper emits unitless, bounded channels:
pitch, yaw, roll, x, y, z, antenna, mouth, with stream sequence and offset.
These are **not radians, meters, servo angles, GPIO commands, motor gains or
guaranteed movement**. A robot adapter must interpret only approved channels,
apply its own operator-reviewed amplitudes and entire-trajectory checks, and
execute through the existing ExecutionRuntime and SDK.

## Sequenced follow-up implementation

### A. Review neutral/shutdown before attended motion

- Keep PR #20 quarantined pending head/antenna clearance review,
  actual feedback convergence and interruption evidence; reconcile the exact
  unknown pose using the already-merged offline PR #19 procedure.
- Add a generic, operator-configured close policy in Puppeteer: do not accept
  new cues; finish or stop only actions owned by the performer; reject sleep
  if any outcome is UNKNOWN or a foreign owner controls the device; optionally
  request an authored neutral and then an SDK sleep through the same execution
  path only after physical authority and reviewed clearance.
- Distinguish pose convergence, no active task, motor mode/torque state, daemon
  liveness and physical settling in evidence. No SDK disconnect or daemon
  health flag alone establishes sleep. A sleep movement must itself be
  independently approved and its outcome verified. Keep standalone operation.
- A browser tab closing is not necessarily the end of a LiveKit room; only
  explicit room end, expiry or owning-worker termination can invoke this path.
- On an unconfirmed STOP, quarantine and require human reconciliation.
  No automatic drive to neutral, retry, wake-up or rearm. Never stop a
  manipulation controller that owns the device.

### B. Connect real outgoing audio once, no duplicate media path

- Use LiveKit's documented Agent.tts_node hook (and, for realtime speech
  providers, realtime_audio_output_node) to observe generated outgoing PCM
  without replacing its output sink or changing frame delivery.
- Produce short bounded batches with monotonic per-stream sequence. In
  Conversationalist, preserve immediate interruption priority, room identity
  and current playback lifecycle evidence; never allow a feature queue to
  obstruct a semantic STOP event. Drop stale audio features, don't catch up.
- The existing room-owned Puppeteer MCP child now exposes an **optional**
  versioned envelope-ingest tool. Conversationalist must negotiate that
  capability on startup and send only bounded frames. The current ingestion
  tool is preview-only. Do not automatically reinterpret generated PCM as
  playback or issue motor commands.
  Do not offer the envelope tool directly to the conversational LLM.
- Establish mapping to LiveKit output playback accounting and the selected
  local EMEET speaker. Generated-PCM time by itself can lead physical sound
  by an unbounded amount; server track callbacks cannot prove acoustic timing.
  Calibration is required before authorizing synchronized motion.

### C. Bind approved robot-specific renderers

- Reachy: use its one existing SDK goto_target head+antennas path behind
  ExecutionRuntime, with reviewed neutral, head geometry, antennas,
  acceleration and explicit amplitude/rate grants. Never enable the SDK's
  parallel HeadWobbler, whose callback bypasses Puppeteer's ownership.
- StackChan: map relevant channels to its existing supported face and head
  SDK semantics and live firmware limits; rate-limit mouth/display changes.
- SO-ARM101: do **not** stream inferred joint targets from PCM. Select among
  exact operator-approved finite choreography assets, with measured start,
  native SDK whole-path validation, watchdog and HOLD/STOP. Default to
  no audio-reactive arm movement until physically qualified.
- Make absent capabilities explicit. Deterministic fallback may be a
  face/display/audio status with zero motor commands.

### D. Validation and provenance

- Unit: malformed schema, gaps, duplicates, out-of-order batches, stale
  playback, interruption, malformed PCM, silence, clipping, no audio sink,
  drop/backpressure and multiple performers.
- MCP: one signed LiveKit room, one child, exact capability negotiation,
  no private content, failure of an optional audio tool without mutating
  action state. Simulated three-robot timelines and denial under ownership.
- Attended: record EMEET synthesized/output/speaker and filmed movement
  timings; head/antenna clearance; real neutral + sleep state; no residual
  servo noise; yes/no regression; physical STOP/recovery evidence.
- Platform OTel: non-content event IDs and timing-source labels, hashes,
  per-device admission/finish/unknown and operator reconciliation, correlated
  to the existing journal. Audio and transcript must never leak into traces.

## Acceptance gates

Software merge is permitted only for isolated, tested, disabled-by-default
algorithms and contracts. No hardware motion is qualified by those merges.
Audio-driven hardware enablement requires documented operator-reviewed
envelope, capabilities, calibration, signed authority, safe STOP/hold, and
attended speaker/movement measurements. UNKNOWN outcomes block retry/rearm.


## StackChan preview renderer (software-only candidate)

Puppeteer now includes a pure `StackChanSpeechFacePreview` that reduces
50 ms normalized speech channels to at most 5 native-format mouth requests per
second by default (150 ms spacing), returning an explicit closing-mouth
candidate on activity release. Values are bounded by a reviewed per-renderer
profile (default peak size 42); the native StackChan SDK parser validates the
resulting `face` operation in offline tests. This module does not run an MCP
client, open USB, dispatch through ExecutionRuntime, or claim speaker timing.

Binding those candidates to actual firmware requires a separate operator
grant, resource ownership, explicit START/STOP behavior, EMEET timing review,
and attended StackChan visual acceptance. The new 100%/170% display setting
belongs to the SDK/firmware independently and never alters envelope intensity.


Puppeteer's StackChan speech-face preview now models the native stock
SpeakingModifier's **mouth weight** channel (default bounded maximum 75),
not a servo angle, mouth size, or actual phoneme. It remains disconnected
from motion and from a production-calibrated speaker clock. Releasing speech
proposes mouth weight 0, but verified stop/neutral remains the responsibility
of the existing runtime and SDK when physical dispatch is eventually enabled.
