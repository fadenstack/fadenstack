"""The gateway sends no prompt to a provider at a link-local or cloud metadata address.

The backend refuses such an endpoint when a provider is saved; a name that
resolves there later, or a row saved before that check, still got every
prompt and the provider's key. The gateway now leaves such a provider out of
a request's routes (a 502 when none is left), looking a host up once a
minute; private, loopback and host.docker.internal addresses -- and the
models Fadenstack runs on its machines -- go on as before.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import LLMProviderInstance, ProviderType
from faden_gateway.services.gateway import provider_address
from faden_gateway.services.gateway.errors import GatewayError
from tests.test_gateway_pipeline_combined import ALIAS, _model_answers, _Observability, _seed, _token

NAMES = {
    "api.example.com": ["104.18.6.192"],
    "metadata.example.com": ["169.254.169.254"],
    "sneaky.example.com": ["104.18.6.192", "fd00:ec2::254"],
    "vllm.lan": ["192.0.2.71"],
    "host.docker.internal": ["192.168.65.254"],
    "localhost": ["127.0.0.1", "::1"],
}


@pytest.fixture
def lookups(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Names resolve from NAMES; every lookup is kept."""
    asked: list[str] = []

    async def resolve(host: str) -> list[str]:
        asked.append(host)
        return NAMES.get(host, [])

    monkeypatch.setattr(provider_address, "resolve", resolve)
    provider_address.forget()
    return asked


def _route(base_url: str, **fields: Any) -> SimpleNamespace:
    return SimpleNamespace(**{"source_kind": None, "node_id": None, "ssl_verify_mode": None, **fields}, base_url=base_url)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "base_url",
    [
        "http://169.254.169.254/v1",
        "http://169.254.10.20:8000",  # link-local
        "http://[fe80::1%25eth0]:8000",
        "http://[fd00:ec2::254]/",
        "http://100.100.100.200/",
        "http://0.0.0.0:8000",  # noqa: S104
        "http://[::]:8000",
        "http://[::ffff:169.254.169.254]/",
        "http://2852039166/",  # 169.254.169.254, spelled as a number
        "http://0xa9fea9fe/",
        "http://metadata.example.com/v1",
        "https://sneaky.example.com/v1",  # one of its addresses is
    ],
)
async def test_a_link_local_or_metadata_address_is_refused(base_url: str, lookups: list[str]) -> None:
    reason = await provider_address.refusal(base_url)
    assert reason is not None
    assert any(word in reason for word in ("metadata", "link-local", "unspecified"))


@pytest.mark.anyio
@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.example.com/v1",
        "http://192.0.2.71:8000/faden-app",  # a machine on the LAN
        "http://vllm.lan:8000",
        "http://192.168.1.20:11434",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://host.docker.internal:8000",
        "http://[fd12::1]:8000",
        "http://does-not-resolve.example:8000",  # let through: the call fails by itself
        "litellm://openai",
        "",
        None,
    ],
)
async def test_private_loopback_and_public_addresses_pass(base_url: str | None, lookups: list[str]) -> None:
    assert await provider_address.refusal(base_url) is None


@pytest.mark.anyio
async def test_a_hosts_answer_is_kept_a_minute(lookups: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr(provider_address.time, "monotonic", lambda: clock[0])

    for _ in range(3):
        assert await provider_address.refusal("https://api.example.com/v1") is None
        assert await provider_address.refusal("https://metadata.example.com/v1") is not None
    assert lookups == ["api.example.com", "metadata.example.com"], "one lookup per host, then the kept answer"

    await provider_address.refusal("http://10.0.0.1:8000")
    assert lookups == ["api.example.com", "metadata.example.com"], "an address is not looked up"

    NAMES["api.example.com"] = ["169.254.169.254"]  # the name moves to the metadata service
    try:
        clock[0] += provider_address.CACHE_TTL_SEC - 1
        assert await provider_address.refusal("https://api.example.com/v1") is None, "still the kept answer"
        clock[0] += 2
        assert await provider_address.refusal("https://api.example.com/v1") is not None, "looked up again"
    finally:
        NAMES["api.example.com"] = ["104.18.6.192"]
    assert lookups.count("api.example.com") == 2


@pytest.mark.anyio
async def test_a_refused_provider_is_left_out_of_the_routes(lookups: list[str]) -> None:
    bad = _route("http://metadata.example.com/v1")
    good = _route("http://192.0.2.49:8000")
    assert await provider_address.usable([bad, good]) == [good]

    # Models on Fadenstack's machines are internal by design: not checked.
    on_machines = [
        _route("http://169.254.3.4:8000", source_kind="inference_deployment"),
        _route("http://169.254.3.5:8000", source_kind="found_container"),
        _route("http://169.254.3.6:8000", node_id=uuid.uuid4()),
        _route("https://169.254.3.7:8443", ssl_verify_mode="machine"),
    ]
    assert await provider_address.usable(on_machines) == on_machines

    with pytest.raises(GatewayError) as refused:
        await provider_address.usable([bad])
    assert refused.value.status_code == 502
    assert refused.value.code == "upstream_address_refused"
    assert refused.value.error_type == "server_error"
    assert "metadata.example.com (169.254.169.254) is a cloud metadata address" in refused.value.message


@pytest.mark.anyio
@pytest.mark.parametrize("stream", [False, True], ids=["whole", "streamed"])
@pytest.mark.parametrize(
    ("base_url", "status"),
    [("http://169.254.169.254/latest", 502), ("http://metadata.example.com", 502), ("http://10.0.0.5:8000", 200)],
)
async def test_the_route_refuses_a_provider_at_a_metadata_address_and_takes_a_private_one(
    stream: bool, base_url: str, status: int, lookups: list[str],
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider row saved before the backend's check: the gateway does not send it the prompt."""
    instance = await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=None)
    await db_session.execute(
        update(LLMProviderInstance).where(LLMProviderInstance.id == instance).values(base_url=base_url),
    )
    await db_session.commit()
    fastapi_app.state.gateway_observability = _Observability()
    sent: list[dict[str, Any]] = []
    _model_answers(monkeypatch, "ok", sent)

    r = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
        "model": ALIAS, "stream": stream, "messages": [{"role": "user", "content": "Hello"}],
    })

    assert r.status_code == status, r.text
    if status == 502:
        assert r.json()["error"]["code"] == "upstream_address_refused"
        assert sent == [], "the provider was not sent the prompt"
    else:
        assert len(sent) == 1
