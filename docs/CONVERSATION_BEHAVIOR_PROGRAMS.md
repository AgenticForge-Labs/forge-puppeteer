# Conversation behavior programs — PR 1 of 5

**Status:** PR #11 merged into `main` at `1c4ec2bc7cca14397bf55b6b806a2e01ab6d7118`. PR #12 adds Reachy/StackChan SDK-native behavior packs and an explicit pinned Reachy snapshot importer, but not yet a live ensemble launcher or Conversationalist connection. See [Native conversational behaviors](NATIVE_CONVERSATION_BEHAVIORS.md).

## Identity versus embodiment

**4G is the current Conversationalist LLM identity and tool-use persona.** Reachy, StackChan and SO-ARM101 are interchangeable physical embodiments of that same identity, including the option to perform simultaneously. Robot names and behavior packs must not contain a hard-coded persona identifier. A future profile may influence expressive style, voice, or tool grants, but persona selection and MCP tool authorization remain owned by their respective higher-level applications and security boundaries.

Puppeteer remains usable standalone through its Python and MCP interfaces: it must not import Conversationalist, LiveKit, Hermes, Platform, or an LLM provider merely to execute an operator-approved show. Conversationalist may eventually act as a voice interface to the larger Platform without changing Puppeteer's hardware authority.

## Contracts

`forge.behavior.v1` models are implemented in `src/forge_puppeteer/behavior_contract.py`:

- `BehaviorCue`: one typed provider operation at a nonnegative offset, with optional **paired** immutable asset ID and `sha256:<64-hex>` revision.
- `BehaviorPattern`: a sequence of up to 64 cues, declared `procedural`, `recorded` or `sequence`, and `once` or a bounded `loop`. A loop requires a period of at least 0.2 seconds, bounded cycles and duration, and cues strictly inside its period.
- `PerformerBehaviors`: one device's named semantic bindings with deterministic canonical JSON SHA-256 revision. Construct via `PerformerBehaviors.author(performer_id, behaviors)`. Revalidating a pack rejects changed or forged contents. This is **content integrity, not publisher authentication**. SDK providers additionally validate motion assets, calibration and physical authorization.
- `EnsemblePresence`: one conversation-wide session ID, monotonically increasing sequence, unique event ID, `idle/listening/speaking/disconnected` state, and honest `agent_state/playback/manual` timing-source label. This contract neither authenticates the speaker nor grants robot motion.

Named gestures such as `yes` and `no` are finite one-shots. `idle` and `speaking` are interruptible background loops; `listening` can loop or use a cue-free hold. Missing device mappings are not guessed or substituted.

Listening can alternatively use an explicit cue-free `hold` pattern: after
the preceding transition completes, the coordinator sends no movement
commands for that presence. This is not a motor HOLD, proof of physical
settling, or an override for an unconfirmed stop. Idle and speaking remain
bounded interruptible loops.

The initial allowed sources and the Python models describe intent *above* the SDK. A behavior may contain parameters appropriate for a selected native provider, but only the existing SDK provider may validate and execute those parameters. No motor bus, camera, audio player or separate robot controller is introduced here.

## Coordinator lifecycle

`ConversationEnsemble(runtime, packs)` coordinates one session across multiple existing `ExecutionRuntime` providers. It operates **inside** the caller's existing `runtime.run()` lifecycle:

```python
from forge_puppeteer.behavior_contract import (
    BehaviorCue, BehaviorPattern, PerformerBehaviors, EnsemblePresence,
)
from forge_puppeteer.conversation_ensemble import ConversationEnsemble

# Providers are explicitly registered and granted by the hosting application.
# This example is intentionally not a hardware connection or MCP authorization.
pattern = BehaviorPattern(
    source="procedural", mode="loop", period_s=10.0,
    cues=(BehaviorCue(at_s=0, operation="operator_approved_idle_primitive"),),
)
pack = PerformerBehaviors.author("reachy", {"idle": pattern})
ensemble = ConversationEnsemble(runtime, [pack])

async with runtime.run():
    async with ensemble.run():
        await ensemble.observe(EnsemblePresence(
            session_id="room-1", event_id="event-1", sequence=0,
            state="idle", source="manual",
        ))
        # Additional authorized conversation transitions or named expressions...
```

Before a performer can run any automatic loop, the ensemble requests **exclusive performer ownership** through the existing `ExecutionRuntime.acquire_control` contract. An arm already owned by a manipulation controller is reported unavailable and is never interrupted or stopped. On clean disconnect/session expiry, acquired ownership is released after the previous pattern is stopped; a quarantined device retains its ownership fence pending deliberate operator reconciliation. This first implementation deliberately prefers exclusive ownership over unsafe resource-sharing. Fine-grained shared/shifting autonomy can be qualified later using the same runtime without a second motion authority.

Each finite cue becomes an `ActionRequest` submitted through the **existing** `ExecutionRuntime.submit/wait`, preserving provider validation, resource locks, ownership checks, action journaling, provenance and STOP semantics. No second execution stack or device connection is created.

- A new speaking/listening state cancels and joins its previous loops before starting replacements.
- A fresh same-state event refreshes the presence lease without replaying a gesture or repeating an already issued cue.
- One-shot named expressions temporarily replace the selected performers' loops and resume their current background presence only on an eligible, confirmed path.
- Loops have finite duration/cycle limits; a 30-second default session heartbeat lease expires conservatively if the source disappears. Higher-level callers must refresh that lease. No catch-up burst is generated when an SDK command takes longer than its expected period.
- A denied **pre-dispatch** action never issues STOP against an unrelated controller's work. If an *admitted* action has an uncertain outcome on cancellation, the coordinator calls the authoritative `ExecutionRuntime.stop`. An unconfirmed STOP quarantines the performer and suppresses later automatic dispatch.
- A group request is best effort concurrent dispatch, not hard-real-time simultaneous physical motion; each action must retain its own result.

## PR 1 acceptance scope

Offline tests in `tests/test_conversation_ensemble.py` use fake native providers **through the real Puppeteer ExecutionRuntime**. They cover three performers, ordered heartbeat events, finite yes/no with background restoration, resource ownership denial without collateral STOP, unknown SDK outcome, failed STOP, per-robot quarantine, immutable pack revisions, missing gestures and session expiry.

The older `ConversationBehaviors` fake talking/idle coordinator and `forge.embodiment.v1` single-fake MCP reference remain intact for compatibility during PR 1. `ConversationEnsemble` is the common extensible orchestration foundation; PRs 2–4 bind real native SDK providers and expose the operator-granted ensemble through Puppeteer's **existing** MCP entrypoint. PR 5 then makes Conversationalist one room-owned caller.

No live motion, hardware qualification, measured audio onset, true three-robot synchronized motion or browser status is claimed by PR 1.

## Tests

```bash
python -m pip install -e '.[dev,mcp]'
pytest tests/test_conversation.py tests/test_conversation_ensemble.py -q
pytest -q
ruff check .
```

No hardware access is needed or permitted in CI. Native Reachy/StackChan behavior execution, deterministic SO-ARM choreography and final LiveKit integration remain PRs 2, 3, 4 and 5 respectively.
