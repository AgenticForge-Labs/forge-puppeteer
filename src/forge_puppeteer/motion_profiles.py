"""Pure selection of independently authored idle and speaking behavior patterns.

This does not create a new motion executor, grant, or actuator scheduler.
Only prevalidated packs for the *same* performer may contribute patterns.
"""

from __future__ import annotations

from .behavior_contract import PerformerBehaviors


def select_motion_patterns(
    legacy: PerformerBehaviors,
    *,
    idle: PerformerBehaviors | None = None,
    speaking: PerformerBehaviors | None = None,
) -> PerformerBehaviors:
    """Compose a content-addressed pack, retaining all nonselected legacy cues.

    None means use the legacy pattern. Alternatives must be authored, validated
    packs for the identical performer; this function cannot make an unsafe
    target valid or authorize its physical execution.
    """
    # Deep-copy and validate revisions even if a caller mutated nested fields.
    baseline = PerformerBehaviors.model_validate_json(legacy.model_dump_json())
    replacements = {}
    for name, candidate in (("idle", idle), ("speaking", speaking)):
        if candidate is None:
            continue
        checked = PerformerBehaviors.model_validate_json(candidate.model_dump_json())
        if checked.performer_id != baseline.performer_id:
            raise ValueError(f"{name} profile belongs to another performer")
        if name not in checked.behaviors or name not in baseline.behaviors:
            raise ValueError(f"{name} profile requires an existing {name} behavior")
        replacements[name] = checked.behaviors[name]
    if not replacements:
        return baseline
    return PerformerBehaviors.author(
        baseline.performer_id,
        {**baseline.behaviors, **replacements},
        startup=baseline.startup,
    )
