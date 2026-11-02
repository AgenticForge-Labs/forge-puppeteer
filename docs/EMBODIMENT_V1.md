# Embodiment contract v1 — software-only reference integration

**Status (2026-11-02):** Fake-only `forge.embodiment.v1` MCP reference merged in PR #10. It is **not** a physical SDK adapter and does **not** qualify any robot for movement. The later PR #11 implements a separate, persona-independent `forge.behavior.v1` catalog and `ConversationEnsemble` in Python, not a live MCP or native robotics integration. See [Conversation behavior programs](CONVERSATION_BEHAVIOR_PROGRAMS.md).

## Architecture and authority

Puppeteer owns semantic behavior coordination and execution identity. It may
be directed by conversational voice, text-based MCP agents, deterministic
rules or scripted performances. The voice app owns speech and conversation
observations. The native SDKs / SO-ARM broker own control grants, hardware
limits, calibration, STOP and actuator outcome evidence.

This initial integration adds exactly three fake-only tools to the existing
operator-configured **stdio MCP** process. It does not add a second gateway,
general event bus or raw SDK method exposure:

- `performer_discover()`: returns contract schema, identity, controls, exact
  expressions/catalog, permissions, resource policy and presence snapshot.
- `conversation_presence(event)`: observes ordered presence transitions
  without commanding physical motion.
- `expression_request(request)`: selects an exact configured fake asset;
  execution uses the **same** `Gateway.submit`, `ActionRequest`,
  `ExecutionRuntime` and `action_lookup` path as other actions.

Existing `action_lookup(action_id)`, `action_cancel(action_id)` where
explicitly granted and `device_stop()` retain their existing semantics.
An accepted action is not proof of completion. Check `action_lookup`.
Physical STOP is never inferred from the simulated provider's result.

## Versioned wire shapes

The schema identifier is `forge.embodiment.v1`. Fields not declared by the
schema are rejected by Pydantic. Both examples target a software-only fake:

```json
{
  "schema": "forge.embodiment.v1",
  "event_id": "turn-a-speaking",
  "session_id": "room-a",
  "performer_id": "reachy-sim",
  "sequence": 2,
  "state": "speaking",
  "source": "agent_state",
  "interrupted": false
}
```

`state` is `idle`, `listening`, `speaking` or `disconnected`.
`source` is `agent_state` (approximate LiveKit state), `manual` or
`playback` (only when verified actual output timing exists).
The latter is *an assertion by the caller*, not independent proof that
Puppeteer received a measured playback signal. A state event alone grants
no robot motion permission. `interrupted=true` is valid only with `idle`.

Ordering is monotone `sequence` per session, one active session per
configured performer. An exact duplicate returns `duplicate`, an older
event returns `stale`, conflicting events or concurrent sessions are denied.
A deliberate `disconnected` observation, or inactivity expiry at 30 seconds
without a fresh event, marks presence stale. The first new session may then
take over. No periodic timer sends an SDK command upon expiry. This is an
ephemeral observation model, not a durable source, heartbeating system or
multi-process session lease. Multiple separate MCP processes must not
control one real device independently.

```json
{
  "schema": "forge.embodiment.v1",
  "action_id": "expression-042",
  "caller_id": "text-agent",
  "session_id": "room-a",
  "performer_id": "reachy-sim",
  "kind": "emotion",
  "name": "curious",
  "asset_revision": "sha256:forge-puppeteer-fake-v1"
}
```

The fake profile's revision is an **illustrative simulation marker**, not
an immutable hash of a physical recording. For an actual device, use a
cryptographic snapshot pin from its SDK-owned catalog. A semantic emotion
is not an SDK asset: explicit device mappings and resource authority are
required before using a physical backend. Missing expressions return
`unsupported`, never a random substitute. Wrong revision is denied.

For this reference fake, presence states are `placeholder` (observation
only); configured expressions are `simulated` and one-shot. The fake's
single `fake.performance` resource denies new expressions while observed
`listening` or `speaking`. No native blending is claimed; no SDK
command is constructed. Expression idempotency is fenced in memory and
the existing runtime action journal handles same-ID persistence and
ambiguous shutdown/restart outcomes. Do not retry any unknown action.

## Try all three software-only performers

Install **only** the declared Python package with its extras; no sibling
source checkout or robot hardware is needed:

```bash
python -m pip install -e '.[mcp,dev]'
forge-puppeteer-mcp --config examples/robots/embodiment-reachy.fake.json
# In another independent test, replace config with:
forge-puppeteer-mcp --config examples/robots/embodiment-stackchan.fake.json
forge-puppeteer-mcp --config examples/robots/embodiment-soarm101.fake.json
```

Each invocation is an MCP **stdio** child, not a shell REPL. A client such
as LiveKit MCPToolset or the official Python MCP ClientSession launches it,
discovers `performer_discover`, sends a `conversation_presence` JSON
object, then an `expression_request` selecting exactly one returned asset
and revision, and checks `action_lookup`. Use a fresh action ID per
one-shot. A text agent may instead explicitly request a curious expression
before asking a question; deterministic programs use the exact same tools.

The fake configuration grants only `device_info`, `action_lookup` and
the three embodiment tools. No physical authority string, USB port, broker,
network host, safety override or SDK installation is used. To verify the
**real MCP stdio boundary** run:

```bash
python -m pytest tests/test_embodiment.py -q
python -m pytest -q
ruff check src tests
```

## Reachy baseline observed (not copied into Puppeteer)

Examined pinned `forge-reachy` Conversation App:
`pollen-robotics/reachy_mini_conversation_app@5ab905d018de8d88f07444ca57677b2ffbe762ca`
and SDK `reachy-mini==1.10.0rc5` (tag
`pollen-robotics/reachy_mini@v1.10.0rc5`).

The Conversation App wraps the SDK recorded emotion library and dance
library as finite `Move` objects queued by `MovementManager`.
Dance `repeat` enqueues N moves. Returns `status=queued`, not
actuator completion. Upstream's `play_emotion` may choose a random
curated recording on unsupported input; Puppeteer must **not** inherit
that behavior. `stop_emotion` and `stop_dance` clear the *whole* primary
queue, not an independently confirmed individual completion.

`BreathingMove` is continuous (infinite duration), added after a short
idle delay; new primary moves interrupt it. Listening freezes antenna
targets and inhibits idle breathing, then blends antennas back.
Head tracking has separate speaking suspension/restore logic.
The SDK's `speech_tapper.SwayRollRT` measures 20-ms PCM windows and
updates every 50 ms, creating loudness-modulated periodic offsets;
`HeadWobbler` schedules against PCM playback PTS. These speech offsets
overlay primary moves, not substitute for them. Actual output start/drain
callbacks are **not** exposed by the pinned stock Conversation App in a
way the existing Reachy PR #4 can claim. Model text, audio delta and
`response.done` are insufficient for audible completion.

## Explicit limitations / next dependency

This initial PR has **no native Reachy, StackChan or SO-ARM expressive
mapping**. Physical speech wobble, listening gaze, emotion/dance asset
qualification, head/body resource sharing, measured completion, queue
cancellation and hardware STOP remain device-specific future work.

SO-ARM optional idle movement remains eligible for later approval but
requires the SDK broker's calibrated constraints, operator authority,
resource ownership and STOP. A simulation recording never authorizes
physical replay. Reachy PRs #3 and #4 were consolidated and merged through forge-reachy PR #5; no
supported pinned playback callback is demonstrated. Next dependency is
a separately merged Conversationalist reference adapter that emits
observations with an **honest** timing source and uses this MCP facade,
not a new robot controller.
