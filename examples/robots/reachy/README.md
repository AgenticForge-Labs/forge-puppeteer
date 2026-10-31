# Reachy behavior pack fixture

For a fully runnable offline MCP example, see [`behavior-packs.mock.json`](behavior-packs.mock.json),
[`mock-mcp-server.json`](mock-mcp-server.json), and the
[Studios/forge-reachy handoff guide](../../../docs/DOWNSTREAM_INTEGRATIONS.md).
The fake asset ID/revision in that profile are explicitly local test values.

`behavior-packs.json` demonstrates the supported fixed `play_move` contract.
The `asset_id` is an exact staged-catalog ID; replace the revision marker with
that catalog's returned revision when preparing an operator-authored pack. The
compiler pins the pack file itself by SHA-256 and execution preflight checks the
asset ID and revision against live MCP discovery before ownership or dispatch.
Reachy cancellation is not confirmed by the current SDK contract.

`behavior-packs.upstream-custom.json` shows separate upstream community and
operator-custom recordings. Stage and lock each catalog independently; each
catalog has its own exact revision, and the matching cue pin must be updated
before compiling either pack. The upstream lock example is
`emotions.manifest.json`; it records the source dataset revision, file hashes,
SDK version, license and notices. Local audio sidecars are included in the
snapshot hashes and are associated with their move, but playback sound remains
disabled in the example cues.

This is a fixture, not an executable deployment profile. No head pose or other
coordinate target is authored here.
