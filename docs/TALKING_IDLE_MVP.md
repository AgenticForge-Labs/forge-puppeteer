# Conversational talking/idle MVP

**Status:** Implementation in a stacked, unmerged draft PR based on
`agent/stackchan-native-mcp-integration` (Puppeteer #7). This is NOT
`main` behavior. There are no real-device drivers or playback-source hooks
activated by this PR.

## Small shared contract

`forge_puppeteer.conversation.ConversationBehaviors` accepts authoritative
playback events with an opaque, stable `turn_id`:

- `speech_started(turn_id)` — start the native talking animation once per
  available performer; no PCM processing or text-based inference.
- `speech_finished(turn_id)` — stop the native talking animation, then return
  that performer to idle only when stopping was confirmed.
- `speech_interrupted(turn_id)` — same conservative stop/idle sequence; duplicate
  or stale endings cannot terminate a newer turn.
- `idle(turn_id)` — request native idle on already-idle performers, without
  overriding active speech.

Each registered `TalkingDriver` exposes `start_talking()`,
`stop_talking()` and `set_idle()`, returning `BehaviorAck` evidence.
Device adapters must use their **existing, authorized native animation and
execution resources**. The coordinator supplies no motor packets, arbitrary
trajectories, camera ownership, audio pipeline, timers or speech recognition.
A driver reporting unavailable/denied behavior is skipped; a failed or
unconfirmed operation is blocked from future automatic dispatch. The
coordinator does not assume a generic software return implies measured robot
motion or safety-rated STOP.

The tests register three **fake** performers named StackChan, Reachy and
SO-ARM101. They verify a shared turn, concurrent broadcast calls, per-device
unavailability, idempotency, stale turn fencing, interruption and blocked
unknown outcomes. These are simulated behavior contracts only; they do not
validate physical robots or device-specific talking assets.

## Connection to the SDKs

- **StackChan:** eventually map start/stop/idle to approved native
  mouth/face/head sequences using the SDK and its public STOP generation;
  do not implement a second serial controller. Puppet #7 and StackChan
  SDK #6 must be accepted/merged before binding a physical adapter.
- **Reachy Mini:** stock Conversation App and Pollen SDK retain their
  native wobbling. Puppet should not send an independent overlapping
  head animation while the stock app is speaking.
- **SO-ARM101:** default talking gesture is unavailable/no-op. Any
  future movement requires an approved recorded gesture and an
  explicit resource grant from the manipulation controller.

## Playback event requirement

The caller must emit events at actual audible playback start, end and
interruption, **not** at model response creation, text completion, receipt
of a PCM chunk, or server-side response completion. Callers must convey the
same turn identifier on start/finish/interruption. A Reachy-side adapter
will be separately validated against upstream playback support.

## Deferred

Listening, audio analysis/phoneme timing, emotion mixing, recording editors,
new firmware operations and general fleet autonomy are outside this MVP.

## Software validation

```bash
uv run --extra dev --extra mcp pytest tests/test_conversation.py -q
uv run --extra dev --extra mcp pytest -q
uv run --extra dev --extra mcp ruff check .
```

Real SDK integration, installed-wheel tests and attended hardware review
remain separate gates. The group behavior is best-effort concurrent scheduling,
not synchronized physical motion.
