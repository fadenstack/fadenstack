"""A forgotten password can be reset at any address an account is made with.

Accounts are made with any address their organisation uses, including special-use domains such as
``firma.local`` (common for internal mail) or ``.test``; the reset route used to check the address as an
internet address and refused those with a 422, so those people could never reset their password. The answer
stays the same whether or not an account has the address (ASVS 5.0 6.3.8).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.notifications import NotificationOutbox
from tests.conftest import RecordingMailSender
from tests.test_account_hardening import _person

pytestmark = pytest.mark.anyio


async def _resets(db: AsyncSession, to: str) -> list[NotificationOutbox]:
    return list((await db.execute(select(NotificationOutbox).where(
        NotificationOutbox.kind == "password_reset", NotificationOutbox.payload_json["to_email"].astext == to,
    ))).scalars())


@pytest.mark.parametrize("domain", ["firma.local", "corp.test", "intranet.internal", "localhost"])
async def test_a_special_use_domain_gets_its_reset_mail(
    client: AsyncClient, dbsession: AsyncSession, mail_on: RecordingMailSender, domain: str,
) -> None:
    user = await _person(dbsession)
    user.email = f"{user.email.split('@')[0]}@{domain}"
    await dbsession.flush()
    sent = await client.post("/api/auth/forgot-password", json={"email": user.email})
    assert sent.status_code == 202, sent.text
    assert len(await _resets(dbsession, user.email)) == 1


async def test_the_address_is_found_whatever_its_case(
    client: AsyncClient, dbsession: AsyncSession, mail_on: RecordingMailSender,
) -> None:
    user = await _person(dbsession)
    user.email = "Ada.Lovelace@Firma.Local"
    await dbsession.flush()
    assert (await client.post("/api/auth/forgot-password", json={"email": "ada.lovelace@firma.local"})).status_code == 202
    assert len(await _resets(dbsession, user.email)) == 1


async def test_an_unknown_address_gets_the_same_answer_and_no_mail(
    client: AsyncClient, dbsession: AsyncSession, mail_on: RecordingMailSender,
) -> None:
    sent = await client.post("/api/auth/forgot-password", json={"email": "nobody@firma.local"})
    assert sent.status_code == 202 and sent.content in (b"", b"null")
    assert await _resets(dbsession, "nobody@firma.local") == []


@pytest.mark.parametrize("email", ["not-an-address", "ada@", "@firma.local", "ada lovelace@firma.local",
                                   "a@b@firma.local", "x" * 330 + "@firma.local"])
async def test_what_is_no_address_is_refused(client: AsyncClient, email: str) -> None:
    assert (await client.post("/api/auth/forgot-password", json={"email": email})).status_code == 422
