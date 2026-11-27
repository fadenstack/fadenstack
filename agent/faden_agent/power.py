"""Keep a Mac node awake while the agent serves (macOS only).

A Mac that idle-sleeps stops sending heartbeats, and the backend marks the
node offline (seen at 22:10 on a laptop on battery). While the agent runs
it holds a power assertion that prevents *idle system* sleep -- the display
may still sleep -- through ``/usr/bin/caffeinate -i -w <agent pid>``.

- ``caffeinate`` is a system binary and needs no root. ``-w`` makes it exit
  on its own when the agent exits, however the agent ends, so it is never
  left behind; it stays in the agent's process group, so launchd's job
  cleanup takes it too.
- It is a tracked child: when it dies it is started again, at most once
  every :data:`RESTART_MIN_S`.
- It does not keep a laptop awake with its lid closed, nor on a critically
  low battery: Mac nodes belong on AC power (desktop Macs are better still).
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import time
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

CAFFEINATE = "/usr/bin/caffeinate"
RESTART_MIN_S = 10.0
CHECK_EVERY_S = 5.0


class IdleSleepGuard:
    def __init__(
        self,
        *,
        enabled: bool = True,
        config_error: str | None = None,
        binary: str = CAFFEINATE,
        pid: int | None = None,
        check_every_s: float = CHECK_EVERY_S,
        restart_min_s: float = RESTART_MIN_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._enabled = enabled and not config_error
        self._config_error = config_error
        self._binary = binary
        self._pid = pid or os.getpid()
        self._check_every = check_every_s
        self._restart_min = restart_min_s
        self._clock = clock
        self._proc: subprocess.Popen[bytes] | None = None
        self._task: asyncio.Task[None] | None = None
        self._last_start = float("-inf")
        self._restarts = 0
        self._error: str | None = None

    # ── the assertion holder ──────────────────────────────────────

    def _spawn(self) -> None:
        self._last_start = self._clock()
        try:
            self._proc = subprocess.Popen(  # noqa: S603 - fixed argv
                [self._binary, "-i", "-w", str(self._pid)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True,
            )
            self._error = None
        except OSError as exc:
            self._proc = None
            self._error = f"could not start {self._binary}: {exc}"
            log.warning("Idle sleep is NOT prevented: %s", self._error)

    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def check(self) -> None:
        """Start it again when it died (rate-limited); reaps the old one."""
        if not self._enabled or self.running():
            return
        if self._proc is not None:
            code = self._proc.poll()
            self._error = f"{self._binary} exited ({code})"
        if self._clock() - self._last_start < self._restart_min:
            return
        if self._proc is not None:
            self._restarts += 1
            log.warning("%s; starting it again", self._error)
        self._spawn()

    async def start(self) -> None:
        if self._config_error:
            log.error("Idle sleep is NOT prevented: %s", self._config_error)
            return
        if not self._enabled:
            log.warning("Idle sleep is not prevented (FADEN_NODE_AGENT_PREVENT_IDLE_SLEEP=off): "
                        "this Mac may sleep and drop offline")
            return
        self._spawn()
        if self.running():
            log.info("Preventing idle system sleep while the agent runs (%s -i -w %s, pid %s); "
                     "the display may sleep. Keep this Mac on AC power: a closed lid or a "
                     "critical battery still sleeps it.", self._binary, self._pid, self._proc.pid)
        self._task = asyncio.create_task(self._watch(), name="prevent-idle-sleep")

    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(self._check_every)
            self.check()

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

    @staticmethod
    def holder_of(agent_pid: int) -> int | None:
        """The ``caffeinate -i -w <agent_pid>`` keeping that agent's Mac awake, if any."""
        import psutil  # noqa: PLC0415

        # A packaged (onefile) agent is two processes: the pid launchd knows is the
        # bootloader, and the Python agent that starts caffeinate is its child.
        procs = list(psutil.process_iter(["pid", "ppid", "cmdline"]))
        watched = {agent_pid} | {p.info["pid"] for p in procs if p.info.get("ppid") == agent_pid}
        for proc in procs:
            cmdline = proc.info.get("cmdline") or []
            if cmdline[:3] == [CAFFEINATE, "-i", "-w"] and len(cmdline) == 4 and cmdline[3].isdigit() \
                    and int(cmdline[3]) in watched:
                return proc.info["pid"]
        return None

    def status(self) -> dict[str, Any]:
        """For the inventory and the logs: not a capability."""
        return {
            "prevent_idle_sleep": self._enabled,
            "active": self.running(),
            "method": "caffeinate -i -w",
            "pid": self._proc.pid if self.running() and self._proc is not None else None,
            "restarts": self._restarts,
            "error": self._config_error or (None if self.running() else self._error),
        }
