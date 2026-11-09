"""A sign-in provider's posts and the Origin check (web/origin.py ``CROSS_SITE_POSTS``).

A provider on the same site as the console (``adfs.corp.example`` beside ``ai.corp.example``) posts its SAML
answers and logout messages from its own page, and the browser sends the console's sign-in cookie along
(``SameSite=Lax`` holds back cross-site posts only). These two posts must reach their views; every other
request with the cookie from another site's page is refused as before.
"""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.containers import AuditEvent
from faden_backend.db.models.users import get_jwt_strategy
from faden_backend.web.origin import CROSS_SITE_POSTS
from tests.test_account_hardening import _person
from tests.test_sign_in import FakeModule, _identity, _signed_in_with_saml, _start, fake  # noqa: F401 - fixture used by name

#: The console at ``ai.corp.example``, the provider's page at ``adfs.corp.example``: same site, another origin.
SAME_SITE_PROVIDER = {"Host": "ai.corp.example", "Origin": "https://adfs.corp.example"}

REFUSED = "This request comes from another site and was refused."


async def _console_cookie(db: AsyncSession) -> str:
    """Someone signed in to the console in this browser, as an administrator usually is."""
    return await get_jwt_strategy().write_token(await _person(db))


@pytest.mark.anyio
async def test_a_same_site_providers_answer_reaches_the_acs_with_the_console_cookie(
    client: AsyncClient, fake: FakeModule, dbsession: AsyncSession,  # noqa: F811
) -> None:
    console = await _console_cookie(dbsession)
    started, transaction = await _start(client, "saml")
    fake.identity = _identity(provider="saml")
    done = await client.post(
        "/api/auth/sso/saml/acs", data={"SAMLResponse": "PHNhbWw+", "RelayState": transaction},
        cookies={"fapiauth": console, "faden_sso": started.cookies["faden_sso"]},
        headers=SAME_SITE_PROVIDER, follow_redirects=False,
    )
    assert done.status_code == 302, done.text
    assert done.headers["location"] == "/login", "signed in by the answer"
    assert done.cookies["fapiauth"] != console
    assert fake.finished[-1]["params"]["SAMLResponse"] == "PHNhbWw+"


@pytest.mark.anyio
async def test_the_binding_still_guards_the_acs(
    client: AsyncClient, fake: FakeModule, dbsession: AsyncSession,  # noqa: F811
) -> None:
    """Past the Origin check, an answer this browser did not start is refused by its binding, as before."""
    _, transaction = await _start(client, "saml")
    client.cookies.clear()
    fake.identity = _identity(provider="saml")
    done = await client.post(
        "/api/auth/sso/saml/acs", data={"SAMLResponse": "PHNhbWw+", "RelayState": transaction},
        cookies={"fapiauth": await _console_cookie(dbsession)},
        headers={**SAME_SITE_PROVIDER, "x-forwarded-proto": "https"}, follow_redirects=False,
    )
    assert done.headers["location"] == "/login?error=sign_in_expired"
    assert fake.finished == [], "the module was not asked"


@pytest.mark.anyio
async def test_a_same_site_providers_logout_post_ends_the_sign_in_with_the_console_cookie(
    client: AsyncClient, fake: FakeModule, dbsession: AsyncSession,  # noqa: F811
) -> None:
    cookie = await _signed_in_with_saml(client, fake, "slo-post")
    fake.logout_answer = {
        "outcome": "end_sign_ins", "subjects": ["slo-post"], "redirect_url": "https://adfs.corp.example/slo?SAMLResponse=y",
    }
    done = await client.post(
        "/api/auth/sso/saml/sls", data={"SAMLRequest": "PHNhbWxwOkxvZ291dFJlcXVlc3Q+", "RelayState": "r"},
        cookies={"fapiauth": cookie}, headers=SAME_SITE_PROVIDER, follow_redirects=False,
    )
    assert done.status_code == 302, done.text
    assert done.headers["location"] == "https://adfs.corp.example/slo?SAMLResponse=y"
    assert fake.logout_messages[-1]["binding"] == "post"
    assert (await client.get("/api/users/me", cookies={"fapiauth": cookie})).status_code == 401, "ended"
    ended = (await dbsession.execute(select(AuditEvent).where(AuditEvent.action == "auth.sign_out.provider"))).scalars()
    assert [json.loads(e.metadata_json or "{}")["provider"] for e in ended] == ["saml"]


@pytest.mark.anyio
@pytest.mark.parametrize("key", ["c", "a" * 64, "corp-saml-2"])
async def test_every_key_the_contract_allows_is_let_through(
    client: AsyncClient, fake: FakeModule, dbsession: AsyncSession, key: str,  # noqa: F811
) -> None:
    """Keys the module does not offer reach the views too, which say so themselves."""
    cookie = {"fapiauth": await _console_cookie(dbsession)}
    for step in ("acs", "sls"):
        r = await client.post(
            f"/api/auth/sso/{key}/{step}", data={"SAMLResponse": "x"}, cookies=cookie,
            headers=SAME_SITE_PROVIDER, follow_redirects=False,
        )
        assert r.status_code == 302, (step, r.status_code, r.text)
        assert r.headers["location"].startswith("/login?"), r.headers


@pytest.mark.anyio
@pytest.mark.parametrize(("method", "path"), [
    ("POST", "/api/auth/sso/saml/link-start"),
    ("POST", "/api/auth/sso/saml/acs/"),
    ("POST", "/api/auth/sso/saml/acs/extra"),
    ("POST", "/api/auth/sso/saml/acsx"),
    ("POST", "/api/auth/sso/saml/acs%0A"),
    ("POST", "/api/auth/sso/x/y/acs"),
    ("POST", "/x/y/api/auth/sso/saml/acs"),
    ("POST", "/api/auth/sso/saml/sls/"),
    ("POST", "/api/auth/sso/saml/slsx"),
    ("POST", "/x/api/auth/sso/saml/sls"),
    ("POST", "/api/auth/sso/Saml/acs"),
    ("POST", "/api/auth/sso/-saml/acs"),
    ("POST", "/api/auth/sso/saml-/sls"),
    ("POST", "/api/auth/sso/saml_x/acs"),
    ("POST", "/api/auth/sso/saml.x/sls"),
    ("POST", f"/api/auth/sso/{'a' * 65}/acs"),
    ("PUT", "/api/auth/sso/saml/acs"),
    ("PATCH", "/api/auth/sso/saml/acs"),
    ("DELETE", "/api/auth/sso/saml/acs"),
    ("PUT", "/api/auth/sso/saml/sls"),
    ("DELETE", "/api/auth/sso/saml/sls"),
])
async def test_everything_else_with_the_cookie_from_another_site_is_still_refused(
    client: AsyncClient, fake: FakeModule, dbsession: AsyncSession, method: str, path: str,  # noqa: F811
) -> None:
    r = await client.request(
        method, path, data={"SAMLResponse": "x"}, cookies={"fapiauth": await _console_cookie(dbsession)},
        headers=SAME_SITE_PROVIDER, follow_redirects=False,
    )
    assert (r.status_code, r.json()) == (403, {"detail": REFUSED}), (method, path, r.status_code, r.text)


@pytest.mark.anyio
async def test_reads_are_unchanged(
    client: AsyncClient, fake: FakeModule, dbsession: AsyncSession,  # noqa: F811
) -> None:
    cookie = {"fapiauth": await _console_cookie(dbsession)}
    me = await client.get("/api/users/me", cookies=cookie, headers=SAME_SITE_PROVIDER)
    assert me.status_code == 200
    fake.logout_answer = {"outcome": "signed_out"}
    redirect = await client.get(
        "/api/auth/sso/saml/sls?SAMLResponse=x", cookies=cookie, headers=SAME_SITE_PROVIDER, follow_redirects=False,
    )
    assert redirect.headers["location"] == "/login?signed_out=provider", "HTTP-Redirect binding, as before"


def test_the_list_is_these_two_posts_each_with_its_reason() -> None:
    assert [(r.method, r.path.pattern.rsplit("/", 1)[-1]) for r in CROSS_SITE_POSTS] == [
        ("POST", "acs\\Z"), ("POST", "sls\\Z"),
    ]
    for route in CROSS_SITE_POSTS:
        assert route.path.pattern.startswith("\\A/api/auth/sso/"), route.path.pattern
        assert "ASVS 5.0" in route.reason, route.reason
