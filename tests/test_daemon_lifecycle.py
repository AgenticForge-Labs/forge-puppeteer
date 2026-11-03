"""Local process lifecycle tests use fake probes and never open hardware."""

import subprocess

import pytest

from forge_puppeteer.daemon_lifecycle import ManagedDaemon, ManagedDaemonConfig


class FakeProcess:
    def __init__(self, *_args, **_kwargs):
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def wait(self, *_args):
        return self.returncode

    def kill(self):
        self.returncode = -9


@pytest.mark.anyio
async def test_managed_daemon_starts_once_waits_for_endpoint_and_persists_on_close():
    process = FakeProcess()
    probes = iter([False, False, True])
    calls = []

    def factory(*args, **kwargs):
        calls.append((args, kwargs))
        return process

    daemon = ManagedDaemon(
        ManagedDaemonConfig(command=("reachy-mini-daemon", "--no-wake-up-on-start")),
        host="127.0.0.1", port=8000, process_factory=factory,
        probe=lambda *_: next(probes),
    )
    await daemon.ensure_running()
    assert daemon.started_here is True
    assert calls[0][0] == (["reachy-mini-daemon", "--no-wake-up-on-start"],)
    assert calls[0][1]["start_new_session"] is True
    await daemon.close()
    assert process.terminated is False


@pytest.mark.anyio
async def test_managed_daemon_reuses_existing_endpoint_without_spawning():
    daemon = ManagedDaemon(
        ManagedDaemonConfig(command=("daemon",)),
        host="localhost", port=8000,
        process_factory=lambda *_args, **_kwargs: pytest.fail("must not spawn"),
        probe=lambda *_: True,
    )
    await daemon.ensure_running()
    assert daemon.started_here is False
    await daemon.close()


@pytest.mark.anyio
async def test_managed_daemon_does_not_restart_a_failed_owned_process():
    process = FakeProcess()
    daemon = ManagedDaemon(
        ManagedDaemonConfig(command=("daemon",)),
        host="127.0.0.1", port=8000,
        process_factory=lambda *_args, **_kwargs: process,
        probe=lambda *_: False,
    )
    daemon._process = process
    daemon.started_here = True
    process.returncode = 1
    with pytest.raises(RuntimeError, match="automatic restart is disabled"):
        await daemon.ensure_running()


def test_managed_daemon_config_rejects_shell_empty_command_and_relative_cwd():
    with pytest.raises(ValueError):
        ManagedDaemonConfig(command=())
    with pytest.raises(ValueError, match="working directory must be absolute"):
        ManagedDaemonConfig(command=("daemon",), cwd="relative")


@pytest.mark.anyio
async def test_managed_daemon_routes_child_output_to_configured_log(tmp_path):
    process = FakeProcess()
    calls = []

    def factory(*args, **kwargs):
        calls.append((args, kwargs))
        return process

    probes = iter([False, True])
    log_path = tmp_path / "daemon" / "daemon.log"
    daemon = ManagedDaemon(
        ManagedDaemonConfig(command=("daemon",), log_path=str(log_path)),
        host="127.0.0.1", port=8000, process_factory=factory,
        probe=lambda *_: next(probes),
    )
    await daemon.ensure_running()
    assert calls[0][1]["stdout"].name == str(log_path)
    assert calls[0][1]["stderr"] == subprocess.STDOUT
    await daemon.close()
    assert not process.terminated

