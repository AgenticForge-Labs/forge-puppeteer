#!/usr/bin/env python3
"""Convert a legacy v2 (Reachy only) or v3 (Reachy + StackChan) config to schema v4.

Writes NEW files into an output directory (ensemble.json plus one file per robot)
and refuses to overwrite anything. Only the options still supported by v4 are
carried over; anything else is reported and stops the conversion, so nothing is
dropped silently. The old file is never modified.

    python scripts/migrate-room-config.py OLD.json OUT_DIR
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SUPPORTED_PROFILES = {"idle": {"legacy": "default", "original_v1": "original_v1"},
                      "speaking": {"legacy": "default", "smooth_v2": "smooth_v2"}}
REACHY_OPTION_MAP = {
    "reachy_expression_style": "expression_style", "reachy_startup_behavior": "startup_behavior",
    "listening_mode": "listening_mode", "listening_tilt_rad": "listening_tilt_rad",
    "speaking_axis": "speaking_axis", "speaking_amplitude_rad": "speaking_amplitude_rad",
    "reachy_expressive_amplitude_rad": "expressive_amplitude_rad",
    "reachy_antenna_sway_rad": "antenna_sway_rad", "reachy_breath_amplitude_m": "breath_amplitude_m",
    "reachy_neutral_antennas_rad": "neutral_antennas_rad",
    "reachy_legacy_speech_fallback": "legacy_speech_fallback",
}
IGNORED_MARKERS = {"motion_authority", "reachy_original_idle_authority", "schema_version"}
HANDLED = set(REACHY_OPTION_MAP) | IGNORED_MARKERS | {
    "reachy", "stackchan", "stackchan_speaking_mode", "reachy_neutral_head", "action_journal",
    "ownership_db", "presence_ttl_s", "stop_timeout_s", "motion_profiles", "speech_driven_performers",
}
WARN_IF_SET = {"startup_settle_s": 0.0, "speech_seed": 7, "stackchan_startup_behavior": "none",
               "stackchan_display_presets_enabled": False}


class MigrationError(ValueError):
    pass


def migrate(old: dict) -> dict[str, dict]:
    """Return ``{filename: json-able}`` for a v4 ensemble. Raises MigrationError."""
    version = old.get("schema_version")
    if version not in (2, 3):
        raise MigrationError(f"only v2 (Reachy) and v3 (Reachy + StackChan) configs migrate; got {version!r}")
    if "soarm101" in old:
        raise MigrationError("SO-ARM entries are not migrated; write a v4 SO-ARM robot file instead")
    unsupported = sorted(k for k in old if k not in HANDLED and k not in WARN_IF_SET)
    if unsupported:
        raise MigrationError(f"options not supported by v4: {unsupported}")
    warnings = [k for k, default in WARN_IF_SET.items() if old.get(k, default) != default]

    reachy = dict(old["reachy"])
    reachy_id = reachy["instance_id"]
    reachy.pop("physical_authority", None)
    options = {new: old[key] for key, new in REACHY_OPTION_MAP.items() if key in old}
    profile = (old.get("motion_profiles") or {}).get(reachy_id, {})
    for state, mapping in SUPPORTED_PROFILES.items():
        value = profile.get(state, "legacy")
        if value not in mapping:
            raise MigrationError(f"Reachy {state} profile {value!r} is no longer supported in v4")
        if mapping[value] != "default":
            options[state] = mapping[value]
    speech_driven = set(old.get("speech_driven_performers") or [])
    if reachy_id in speech_driven:
        options["speech_driven"] = True
    robots = [("reachy", reachy_id, {"kind": "reachy", "id": reachy_id, "provider": reachy,
                                      "options": options})]
    other_profiles = set(old.get("motion_profiles") or {}) - {reachy_id}
    if version == 3:
        stackchan = dict(old["stackchan"])
        stack_id = stackchan["instance_id"]
        stackchan.pop("physical_authority", None)
        stack_profile = (old.get("motion_profiles") or {}).get(stack_id, {})
        if any(v != "legacy" for v in stack_profile.values()):
            raise MigrationError("StackChan motion profiles are no longer supported in v4")
        other_profiles -= {stack_id}
        stack_options = {"speaking_mode": old.get("stackchan_speaking_mode", "legacy_size")}
        if stack_id in speech_driven:
            stack_options["speech_driven"] = True
        robots.append(("stackchan", stack_id, {"kind": "stackchan", "id": stack_id,
                                                "provider": stackchan, "options": stack_options}))
        speech_driven -= {stack_id}
    if other_profiles or speech_driven - {reachy_id}:
        raise MigrationError("motion profiles or speech-driven entries reference unknown performers")
    room = {"schema_version": 4, "physical": True,
            "robots": [{"kind": k, "id": i, "file": f"{k}.json"} for k, i, _ in robots],
            "action_journal": old["action_journal"], "ownership_db": old["ownership_db"],
            "reachy_neutral_head": old["reachy_neutral_head"]}
    for key in ("presence_ttl_s", "stop_timeout_s"):
        if key in old:
            room[key] = old[key]
    files = {"ensemble.json": room, **{f"{k}.json": body for k, _, body in robots}}
    files["_warnings"] = warnings  # type: ignore[assignment]
    return files


def write(files: dict[str, dict], out_dir: Path) -> list[Path]:
    out_dir = Path(out_dir)
    targets = [out_dir / name for name in files if not name.startswith("_")]
    clashes = [t for t in targets if t.exists()]
    if clashes:
        raise MigrationError(f"refusing to overwrite: {[str(c) for c in clashes]}")
    out_dir.mkdir(parents=True, exist_ok=True)
    for target in targets:
        target.write_text(json.dumps(files[target.name], indent=2) + "\n", encoding="utf-8")
    return targets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("old_config")
    parser.add_argument("out_dir")
    args = parser.parse_args(argv)
    try:
        files = migrate(json.loads(Path(args.old_config).read_text(encoding="utf-8")))
        warnings = files.pop("_warnings")
        written = write(files, Path(args.out_dir))
    except (MigrationError, OSError, KeyError, ValueError) as exc:
        print(f"not migrated: {exc}", file=sys.stderr)
        return 1
    for path in written:
        print(f"wrote {path}")
    for key in warnings:
        print(f"note: {key} is not carried over to v4")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
