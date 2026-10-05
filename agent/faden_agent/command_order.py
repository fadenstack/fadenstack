"""Which commands may run at the same time on this machine, and in what order.

Until 0.2.3 the agent ran one command at a time, and it ran it inside the
loop that reads the connection: nothing else was read until it finished. A
model copy of half an hour on gpu-01 left the server's status requests
queued unread; once the websocket's buffer was full it stopped reading the
socket altogether -- the replies to its own keepalive pings with it -- and
the connection was dropped as dead, taking the copy with it.

Commands now run beside the reading, and beside each other where that is
safe. Four kinds:

``CHANGE``
    Changes what runs on the machine: starts, stops, joins, deploys, pulls
    the runtime image, updates the system. One at a time, in the order they
    came -- as every command used to be. Any command not listed below is one.
``OBSERVE``
    Reads what the changes act on (Ray's and Serve's status, logs, the
    inventory). Several at once, never while a change runs: a status taken
    halfway through starting a cluster reads "down", and the server re-forms
    clusters it finds down.
``CHECK``
    Probes that look at nothing a change touches: the internet, the fabric.
    Any time.
``TRANSFER``
    Long copies into a store of their own: a model, the runtime image served
    to a peer. Beside everything else, a few at a time (each holds a
    download's buffers), and one per model (``model_puller`` and
    ``hf_download`` lock the model's folder).

A command waiting for its turn says so every minute, so the server does not
take a queued command for a silent -- dead -- one.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from faden_agent.models import NodeCommandType as T

log = logging.getLogger(__name__)

CHANGE, OBSERVE, CHECK, TRANSFER = "change", "observe", "check", "transfer"

KIND: dict[str, str] = {
    T.GET_RAY_STATUS.value: OBSERVE,
    T.GET_RAY_SERVE_STATUS.value: OBSERVE,
    T.DESCRIBE_RAY_CLUSTER.value: OBSERVE,
    T.DESCRIBE_RAY_PLACEMENT.value: OBSERVE,
    T.FETCH_CONTAINER_LOGS.value: OBSERVE,
    T.COLLECT_DIAGNOSTICS.value: OBSERVE,
    T.REFRESH_INVENTORY.value: OBSERVE,
    T.CHECK_INTERNET.value: CHECK,
    T.VALIDATE_FABRIC_LISTEN.value: CHECK,
    T.VALIDATE_FABRIC_CONNECT.value: CHECK,
    T.SYNC_MODEL.value: TRANSFER,
    T.SERVE_RUNTIME_IMAGE.value: TRANSFER,
}

#: Transfers running at once; more wait their turn.
MAX_TRANSFERS = 2
#: How often a command waiting for its turn says so.
WAITING_NOTICE_SEC = 60.0


def kind_of(command_type: str) -> str:
    """The kind of *command_type*; a change unless it is known to be something safer."""
    return KIND.get(command_type, CHANGE)


class ReadWriteLock:
    """Many readers or one writer, served in the order they asked.

    A writer waits for the readers already in, and holds everyone who came
    after it -- so a stream of status probes cannot keep a change waiting
    for ever, nor a change slip in ahead of probes that were first.
    """

    def __init__(self) -> None:
        self._turnstile = asyncio.Lock()  # FIFO: whoever asked first goes first
        self._readers = 0
        self._no_readers = asyncio.Event()
        self._no_readers.set()

    @contextlib.asynccontextmanager
    async def read(self) -> AsyncIterator[None]:
        async with self._turnstile:
            self._readers += 1
            self._no_readers.clear()
        try:
            yield
        finally:
            self._readers -= 1
            if self._readers == 0:
                self._no_readers.set()

    @contextlib.asynccontextmanager
    async def write(self) -> AsyncIterator[None]:
        async with self._turnstile:
            await self._no_readers.wait()
            yield


class CommandOrder:
    """The gate every command passes on this machine (see the module's docstring)."""

    def __init__(self, *, max_transfers: int = MAX_TRANSFERS, waiting_notice_sec: float = WAITING_NOTICE_SEC) -> None:
        self._state = ReadWriteLock()
        self._transfers = asyncio.Semaphore(max_transfers)
        self._notice_sec = waiting_notice_sec

    def _slot(self, kind: str) -> contextlib.AbstractAsyncContextManager[Any]:
        if kind == OBSERVE:
            return self._state.read()
        if kind == TRANSFER:
            return self._transfers
        if kind == CHECK:
            return contextlib.nullcontext()
        return self._state.write()

    @contextlib.asynccontextmanager
    async def turn(
        self, command_type: str, *, notify: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> AsyncIterator[None]:
        """Wait for *command_type*'s turn, saying every so often that it waits; hold it while inside."""
        kind = kind_of(command_type)
        slot = self._slot(kind)
        entered = asyncio.Event()

        async def _tell() -> None:
            while not entered.is_set():
                try:
                    await asyncio.wait_for(entered.wait(), timeout=self._notice_sec)
                except TimeoutError:
                    if notify is not None:
                        try:
                            await notify({
                                "phase": "queued",
                                "message": f"Waiting for the machine's other {'transfers' if kind == TRANSFER else 'commands'} to finish",
                            })
                        except Exception:  # noqa: BLE001 - a notice is never worth failing the command over
                            log.debug("Could not send a waiting notice", exc_info=True)

        teller = asyncio.create_task(_tell())
        try:
            async with slot:
                entered.set()
                yield
        finally:
            entered.set()
            teller.cancel()
