import json

import pytest

from forge_puppeteer.cli import main
from forge_puppeteer.execution import ActionRequest, ExecutionError
from forge_puppeteer.operator import execute_configured_action


def _files(tmp_path, grants=None):
    config = {
        "backend": "fake", "device": "stackchan", "instance_id": "fake-1",
        "allowed_tools": grants or ["head"],
    }
    request = {
        "id": "take.1", "performer_id": "fake-1", "caller_id": "test",
        "session_id": "session", "operation": "head",
        "parameters": {"yaw": 12.0},
    }
    cp, rp = tmp_path / "operator.json", tmp_path / "action.json"
    cp.write_text(json.dumps(config)); rp.write_text(json.dumps(request))
    return cp, rp


def test_python_and_cli_use_configured_fake_runtime(tmp_path, capsys):
    config, action = _files(tmp_path)
    result = execute_configured_action(config, ActionRequest.model_validate_json(action.read_text()))
    assert result.state == "completed"
    assert main(["provider-action", "--provider-config", str(config), "--action", str(action)]) == 0
    cli_result = json.loads(capsys.readouterr().out)
    assert cli_result["state"] == result.state
    assert cli_result["operation"] == "head"


def test_python_and_cli_fail_closed_on_missing_operator_grant(tmp_path):
    config, action = _files(tmp_path, grants=["device_info"])
    with pytest.raises(ExecutionError, match="permission_denied"):
        execute_configured_action(config, ActionRequest.model_validate_json(action.read_text()))
    with pytest.raises(ExecutionError, match="permission_denied"):
        main(["provider-action", "--provider-config", str(config), "--action", str(action)])
