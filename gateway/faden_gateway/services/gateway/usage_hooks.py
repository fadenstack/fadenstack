"""Running the extensions' admission checks and usage listeners (``services/extensions.py``).

Both lists are empty in core: then a request pays nothing for them, not even a lookup of its team.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Sequence
from typing import Any

from faden_gateway.services.extensions import (
    AdmissionCheck,
    AdmissionContext,
    Refusal,
    UsageListener,
    UsageRecord,
)
from faden_gateway.services.gateway.errors import GatewayError

log = logging.getLogger(__name__)

#: Listeners started for after the response, kept referenced until they are done (the event loop holds
#: tasks only weakly).
_RUNNING: set[asyncio.Task[Any]] = set()


async def _checked(check: AdmissionCheck, context: AdmissionContext) -> Refusal | None:
    """*check*'s answer; one that raises is logged and lets the request in.

    An extension that wants to fail closed returns a ``Refusal`` itself.
    """
    try:
        return await check(context)
    except Exception:
        log.exception("An admission check failed; it does not refuse request %s", context.request_id)
        return None


async def first_refusal(checks: Sequence[AdmissionCheck], context: AdmissionContext) -> Refusal | None:
    """Run *checks* side by side; the first refusal in their order, or ``None``."""
    if not checks:
        return None
    answers = await asyncio.gather(*(_checked(check, context) for check in checks))
    return next((answer for answer in answers if answer is not None), None)


def refused(refusal: Refusal, headers: dict[str, str] | None = None) -> GatewayError:
    """*refusal* as the 429 (or the status it names) OpenAI clients read (C4).

    ``Retry-After`` from ``retry_after_sec``, and ``x-should-retry: false`` when that is more than a
    minute; the refusal's own headers win over these and over *headers* (the caps' ``x-ratelimit-*``).
    """
    sent: dict[str, str] = dict(headers or {})
    if refusal.retry_after_sec is not None:
        sent["Retry-After"] = str(max(int(refusal.retry_after_sec), 0))
        if refusal.retry_after_sec > 60:
            sent["x-should-retry"] = "false"
    sent.update(refusal.headers)
    return GatewayError(
        status_code=refusal.status,
        message=refusal.message or "A usage limit refuses this request. Ask an administrator.",
        error_type=refusal.error_type,
        code=refusal.code,
        param=refusal.param,
        headers=sent,
    )


def _spawn(step: Awaitable[Any], what: str) -> None:
    async def run() -> None:
        try:
            await step
        except Exception:
            log.exception("A usage listener failed (%s)", what)

    task = asyncio.ensure_future(run())
    _RUNNING.add(task)
    task.add_done_callback(_RUNNING.discard)


def notify(listeners: Sequence[UsageListener], record: UsageRecord) -> None:
    """Tell each of *listeners* about *record*, in the background; nothing waits for them."""
    for listener in listeners:
        _spawn(listener(record), record.request_id)


async def drain() -> None:
    """Wait for the listeners still running (tests, shutdown)."""
    while _RUNNING:
        await asyncio.gather(*list(_RUNNING), return_exceptions=True)
