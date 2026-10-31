# Robot metadata and existing performance contracts

`robot_contracts` version 1 is metadata, not authority or an execution API.
`examples/robots/metadata.json` is illustrative offline data: it does not describe
actual device limits, connected hardware, installed assets, or broker grants.
The device-specific parameter schemas are intentionally distinct. Providers must
supply live, validated values before the metadata can support execution.

The existing `Performance` and `Cue` models express semantic actions in a timed
performance. `PerformanceStage` maps those actions to an embodiment, arbitrates
its existing coarse resource domains, and emits `PerformanceEvent` records. Those
records currently describe stage execution; they do not provide the durable action
identity, exact asset revision, cancellation evidence, or recovery semantics of
`ActionRecord`. The new metadata must not be interpreted as adding those guarantees
or as replacing the existing stage, event emitter, or resource locks. Later runtime
integration must bridge the two explicitly and retain existing semantic behavior.

`examples/robots/mock-performance.json` is a semantic fixture for the existing
performance model, not an executable device configuration. The names are authored
examples and do not imply that a device has the named assets.
