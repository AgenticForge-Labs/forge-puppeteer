# SO-ARM101 behavior pack fixture

`behavior-packs.json` uses only the broker's parameterless `sleep` operation,
which selects the broker's saved Sleep pose. The compiler pins this manifest by
SHA-256; live preflight still requires the configured broker to report readiness,
active authority, the `sleep` tool, and compatible resource metadata.

The `inspection-recording` pack demonstrates typed `replay_recording` with a
bounded speed and both an asset ID and SHA-256 revision. Replace the explicit
fixture ID/revision with a recording returned by the configured broker's
`list_assets`; live preflight checks that exact catalog revision, and the SDK
provider currently permits replay only in simulation-only mode. The pack does
not encode joint or Cartesian coordinates. The external SO-ARM broker remains
authoritative for permission, calibration, and physical safety.
