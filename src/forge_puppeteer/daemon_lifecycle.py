"""Generic lifecycle support for adapter-owned local device daemons.

The owner starts one configured process when its endpoint is absent, waits for
the endpoint to appear, and checks liveness without restarting a failed daemon.
Providers remain responsible for validating device identity and readiness via
their SDK before granting actions.
"""

from __future__ import annotations

import asyncio
import socket
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ManagedDaemonConfig(BaseModel):
    """Trusted operator-selected process command; never interpreted by a shell."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    command: tuple[str, ...] = Field(min_length=1, max_length=32)
    cwd: str | None = None
    log_path: str | None = None
    startup_timeout_s: float = Field(default=45, ge=1, le=300)
    persist_on_close: bool = True

    @model_validator(mode="after")
    def validate_command(self):
        if any(not item or "\x00" in item for item in self.command):
            raise ValueError("daemon command must contain nonempty argv values")
        if self.cwd is not None and not Path(self.cwd).is_absolute():
            raise ValueError("daemon working directory must be absolute")
        if self.log_path is not None and not Path(self.log_path).is_absolute():
            raise ValueError("daemon log path must be absolute")
        return self


class ManagedDaemon:
    """Start and supervise one local endpoint for an adapter's lifespan.

    Existing endpoints are reused, never killed. A daemon started here remains
    running after a normal adapter shutdown when ``persist_on_close`` is true.
    Failed processes are reported; unknown device outcomes are never retried.
    """

    def __init__(
        self,
        config: ManagedDaemonConfig,
        *,
        host: str,
        port: int,
        process_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
        probe: Callable[[str, int], bool] | None = None,
    ) -> None:
        if not host or not 1 <= port <= 65535:
            raise ValueError("daemon endpoint is invalid")
        self.config = config
        self.host = host
        self.port = port
        self._process_factory = process_factory
        self._probe = probe or self._tcp_probe
        self._process: subprocess.Popen | None = None
        self._log_stream = None
        self.started_here = False

    @staticmethod
    def _tcp_probe(host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=0.25):
                return True
        except OSError:
            return False

    async def ensure_running(self) -> None:
        """Reuse a responding endpoint or launch the configured daemon once."""
        if self._probe(self.host, self.port):
            return
        if self._process is not None:
            self.assert_alive()
        argv: Sequence[str] = self.config.command
        try:
            if self.config.log_path is not None:
                log_path = Path(self.config.log_path)
                await asyncio.to_thread(log_path.parent.mkdir, parents=True, exist_ok=True)
                self._log_stream = await asyncio.to_thread(
                    log_path.open, "ab", buffering=0
                )
            self._process = self._process_factory(
                [*argv],
                cwd=self.config.cwd,
                stdin=subprocess.DEVNULL,
                stdout=self._log_stream or subprocess.DEVNULL,
                stderr=subprocess.STDOUT if self._log_stream is not None else subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            if self._log_stream is not None:
                self._log_stream.close()
                self._log_stream = None
            raise RuntimeError("configured device daemon could not be started") from exc
        self.started_here = True
        deadline = time.monotonic() + self.config.startup_timeout_s
        try:
            while time.monotonic() < deadline:
                if self._process is None or self._process.poll() is not None:
                    raise RuntimeError("device daemon exited during startup")
                if self._probe(self.host, self.port):
                    return
                await asyncio.sleep(0.1)
            raise TimeoutError("device daemon endpoint did not become available")
        except BaseException:
            await self._stop_owned()
            raise

    def assert_alive(self) -> None:
        """Fail closed if an owned process or its endpoint has gone away."""
        if self._process is not None and self._process.poll() is not None:
            raise RuntimeError("device daemon exited; automatic restart is disabled")
        if not self._probe(self.host, self.port):
            raise RuntimeError("device daemon endpoint is unavailable")

    async def close(self) -> None:
        """Apply the configured lifetime policy to a process started here."""
        if self._process is None:
            return
        if self.config.persist_on_close:
            # The detached child is deliberately left available to the next
            # room; closing its parent adapter must not send a robot sleep.
            self._process = None
            if self._log_stream is not None:
                self._log_stream.close()
                self._log_stream = None
            return
        await self._stop_owned()

    async def _stop_owned(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            await asyncio.to_thread(process.wait, 3)
        except subprocess.TimeoutExpired:
            process.kill()
            await asyncio.to_thread(process.wait)
        finally:
            if self._log_stream is not None:
                self._log_stream.close()
                self._log_stream = None
