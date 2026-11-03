import json
import sqlite3

import pytest

from forge_puppeteer.operator_reconcile import CONFIRMATION, reconcile_unknown_action


def seed_databases(tmp_path, *, other_activity=False):
    journal = tmp_path / "actions.sqlite3"
    owners = tmp_path / "owners.sqlite3"
    db = sqlite3.connect(journal)
    db.execute(
        "CREATE TABLE actions (id TEXT PRIMARY KEY, request TEXT, fingerprint TEXT, record TEXT, reserved INTEGER)"
    )
    request = {
        "id": "action-1", "performer_id": "reachy-local", "caller_id": "room",
        "session_id": "session", "operation": "head_pose", "parameters": {},
    }
    record = {
        "id": "action-1", "state": "unknown", "operation": "head_pose",
        "error": "stop_outcome_unknown", "cancellation_outcome": "stop_unconfirmed",
    }
    db.execute("INSERT INTO actions VALUES (?, ?, ?, ?, 1)", (
        "action-1", json.dumps(request), "fingerprint", json.dumps(record),
    ))
    db.commit()
    db.close()
    db = sqlite3.connect(owners)
    db.execute("CREATE TABLE performer_owners (performer_id TEXT, caller_id TEXT, session_id TEXT, process_id TEXT)")
    db.execute("CREATE TABLE performer_activity (performer_id TEXT, process_id TEXT, action_id TEXT, caller_id TEXT, session_id TEXT)")
    db.execute("INSERT INTO performer_owners VALUES ('reachy-local', 'room', 'session', 'process')")
    db.execute("INSERT INTO performer_activity VALUES ('reachy-local', 'process', 'action-1', 'room', 'session')")
    if other_activity:
        db.execute("INSERT INTO performer_activity VALUES ('reachy-local', 'other', 'action-2', 'room', 'session')")
    db.commit()
    db.close()
    return journal, owners


def reconcile(paths, **kwargs):
    journal, owners = paths
    values = {
        "action_id": "action-1", "operator": "operator",
        "observation": "Observed settled", "confirmation": CONFIRMATION,
    }
    values.update(kwargs)
    return reconcile_unknown_action(
        journal_path=str(journal), ownership_path=str(owners),
        **values,
    )


def test_reconciliation_preserves_unknown_outcome_and_audits_operator(tmp_path):
    journal, owners = seed_databases(tmp_path)
    result = reconcile((journal, owners))
    assert result["action_outcome"] == "unknown"
    db = sqlite3.connect(journal)
    record, reserved = db.execute("SELECT record, reserved FROM actions WHERE id='action-1'").fetchone()
    assert json.loads(record)["cancellation_outcome"] == "stop_unconfirmed"
    assert json.loads(record)["state"] == "unknown"
    assert reserved == 0
    audit = db.execute("SELECT operator, observation, action_outcome FROM operator_reconciliations").fetchone()
    assert audit == ("operator", "Observed settled", "unknown")
    assert sqlite3.connect(owners).execute("SELECT count(*) FROM performer_owners").fetchone()[0] == 0


@pytest.mark.parametrize("change", ["confirmation", "other_activity"])
def test_reconciliation_refuses_unconfirmed_or_ambiguous_state(tmp_path, change):
    journal, owners = seed_databases(tmp_path, other_activity=change == "other_activity")
    kwargs = {"confirmation": ""} if change == "confirmation" else {}
    with pytest.raises(ValueError):
        reconcile((journal, owners), **kwargs)
    assert sqlite3.connect(journal).execute("SELECT reserved FROM actions").fetchone()[0] == 1


def test_reconciliation_does_not_accept_terminal_or_unreserved_actions(tmp_path):
    journal, owners = seed_databases(tmp_path)
    db = sqlite3.connect(journal)
    db.execute("UPDATE actions SET reserved=0")
    db.commit()
    db.close()
    with pytest.raises(ValueError, match="unresolved reserved unknown"):
        reconcile((journal, owners))
