"""Core's client of the mail service: the addresses it posts to are the ones the mailer serves.

faden_mailer mounts its internal messages under ``/api`` (``faden_mailer.web.application``), as it does
its health check, which the module registry already asked at ``/api/health``.
"""

from __future__ import annotations

import httpx
import pytest

from faden_backend.services.notifications.mailer_client import MailerClient

pytestmark = pytest.mark.anyio


async def test_the_mailer_is_called_where_it_listens() -> None:
    seen: list[str] = []
    transport = httpx.MockTransport(lambda request: seen.append(request.url.path) or httpx.Response(200))
    async with httpx.AsyncClient(transport=transport) as http:
        mailer = MailerClient(http)
        await mailer.send_password_reset({})
        await mailer.send_admin_alert({})
    assert seen == [
        "/api/internal/v1/messages/password-reset",
        "/api/internal/v1/messages/admin-alert",
    ]
