"""Attended, auditable reconciliation of one unknown action.

This command does not issue a device STOP and never changes an action's outcome.
It records an operator's physical observation and releases only the durable
admission fence associated with that exact unknown action.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from .robot_contracts import ActionRecord, ActionState

CONFIRMATION = "I OBSERVED THE ROBOT SETTLED AND ALL MOTION CONTROLLERS STOPPED"


def reconcile_unknown_action(
    *,
    journal_path: str,
    ownership_path: str,
    action_id: str,
    operator: str,
    observation: str,
    confirmation: str,
) -> dict[str, str]:
    """Record attended physical evidence and release the exact stale fence.

    This is deliberately offline. The operator must first ensure every motion
    controller is stopped and observe the robot settled. The action remains
    ``unknown`` with its original stop evidence; this is not SDK confirmation.
    """
    journal = Path(journal_path)
    owners = Path(ownership_path)
    if (
        not journal.is_absolute()
        or not owners.is_absolute()
        or journal.resolve() == owners.resolve()
    ):
        raise ValueError("journal and ownership paths must be distinct absolute paths")
    if confirmation != CONFIRMATION:
        raise ValueError("physical observation confirmation did not match")
    operator = operator.strip()
    observation = observation.strip()
    if not operator or not observation:
        raise ValueError("operator and physical observation are required")

    db = sqlite3.connect(journal, timeout=5, isolation_level=None)
    try:
        db.execute("ATTACH DATABASE ? AS owners", (str(owners),))
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT request, record, reserved FROM main.actions WHERE id=?",
            (action_id,),
        ).fetchone()
        if row is None:
            raise ValueError("unknown action ID")
        request_json, record_json, reserved = row
        request = json.loads(request_json)
        record = ActionRecord.model_validate_json(record_json)
        if record.state != ActionState.UNKNOWN or not reserved:
            raise ValueError("action is not an unresolved reserved unknown")
        performer_id = request.get("performer_id")
        if not isinstance(performer_id, str) or not performer_id:
            raise ValueError("action has no valid performer ID")
        active = db.execute(
            "SELECT process_id, caller_id, session_id FROM owners.performer_activity "
            "WHERE performer_id=? AND action_id=?",
            (performer_id, action_id),
        ).fetchall()
        if len(active) != 1:
            raise ValueError("matching durable activity is missing or ambiguous")
        process_id, caller_id, session_id = active[0]
        owner = db.execute(
            "SELECT caller_id, session_id, process_id FROM owners.performer_owners "
            "WHERE performer_id=?",
            (performer_id,),
        ).fetchone()
        if owner != (caller_id, session_id, process_id):
            raise ValueError("durable owner does not match the unknown action")
        other = db.execute(
            "SELECT 1 FROM owners.performer_activity WHERE performer_id=? AND action_id<>? LIMIT 1",
            (performer_id, action_id),
        ).fetchone()
        if other is not None:
            raise ValueError("another action is active for this performer")

        reconciled_at = datetime.now(UTC).isoformat()
        db.execute(
            "CREATE TABLE IF NOT EXISTS main.operator_reconciliations ("
            "action_id TEXT PRIMARY KEY, performer_id TEXT NOT NULL, operator TEXT NOT NULL, "
            "observation TEXT NOT NULL, reconciled_at TEXT NOT NULL, "
            "action_outcome TEXT NOT NULL CHECK(action_outcome='unknown'))"
        )
        db.execute(
            "INSERT INTO main.operator_reconciliations VALUES (?, ?, ?, ?, ?, 'unknown')",
            (action_id, performer_id, operator, observation, reconciled_at),
        )
        updated = db.execute(
            "UPDATE main.actions SET reserved=0 WHERE id=? AND reserved=1", (action_id,)
        )
        if updated.rowcount != 1:
            raise ValueError("action reservation changed during reconciliation")
        removed_activity = db.execute(
            "DELETE FROM owners.performer_activity WHERE performer_id=? AND process_id=? AND action_id=?",
            (performer_id, process_id, action_id),
        )
        removed_owner = db.execute(
            "DELETE FROM owners.performer_owners WHERE performer_id=? AND caller_id=? AND session_id=? AND process_id=?",
            (performer_id, caller_id, session_id, process_id),
        )
        if removed_activity.rowcount != 1 or removed_owner.rowcount != 1:
            raise ValueError("durable ownership changed during reconciliation")
        db.execute("COMMIT")
        return {
            "action_id": action_id,
            "performer_id": performer_id,
            "action_outcome": "unknown",
            "reconciled_at": reconciled_at,
            "operator": operator,
        }
    except Exception:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Record an attended observation for one unknown Puppeteer action."
    )
    parser.add_argument("--journal", required=True)
    parser.add_argument("--ownership-db", required=True)
    parser.add_argument("--action-id", required=True)
    parser.add_argument("--operator", required=True)
    parser.add_argument("--observation", required=True)
    parser.add_argument("--confirm", required=True, help=f'type exactly: {CONFIRMATION}')
    args = parser.parse_args()
    print(json.dumps(reconcile_unknown_action(
        journal_path=args.journal,
        ownership_path=args.ownership_db,
        action_id=args.action_id,
        operator=args.operator,
        observation=args.observation,
        confirmation=args.confirm,
    ), indent=2))


if __name__ == "__main__":
    main()
