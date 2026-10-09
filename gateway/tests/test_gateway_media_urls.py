"""Media URLs a client sends go on to the model only as data: URLs or public addresses.

vLLM fetches an image_url (audio_url, video_url, ...) itself, with no list of
allowed domains: a URL in a chat request had the GPU machine fetch any
address, the cloud metadata service and the internal network included. The
gateway now refuses, with a 400, a media URL whose host is not public; the
pictures it adds itself (a chat's attachments, as data: URLs) and the
pictures the SDKs send (data: URLs) go on as before.
"""

from __future__ import annotations

import base64
import ipaddress
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import ChatAttachment, ExtractionStatus, ProviderType
from faden_gateway.services.gateway import media_urls
from faden_gateway.services.gateway.errors import GatewayError
from faden_gateway.settings import settings
from tests.test_agent_routes import URL as AGENT_URL
from tests.test_agent_routes import _chat as _agent_chat
from tests.test_agent_routes import _headers as _agent_headers
from tests.test_agent_routes import _model as _agent_model
from tests.test_agent_routes import backend  # noqa: F401 - the fixture the agent route needs
from tests.test_gateway_pipeline_combined import (
    ALIAS,
    _model_answers,
    _Observability,
    _seed,
    _session,
    _token,
)

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==")
DATA_URL = "data:image/png;base64," + base64.b64encode(PNG).decode()
PUBLIC = {"pictures.example.com": ["93.184.216.34"], "two-faced.example.com": ["93.184.216.34", "10.0.0.8"]}


@pytest.fixture(autouse=True)
def _dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Names resolve from PUBLIC (IP literals as themselves); any other name does not resolve."""

    async def resolve(host: str) -> list[str]:
        if host in PUBLIC:
            return PUBLIC[host]
        try:
            return [str(ipaddress.ip_address(host))]
        except ValueError:
            return []

    monkeypatch.setattr(media_urls, "_resolve", resolve)
    monkeypatch.setattr(settings, "media_url_check", True)
    monkeypatch.setattr(settings, "media_url_allowed_hosts", "")
    media_urls._allowed.cache_clear()  # noqa: SLF001


def _picture(url: Any, kind: str = "image_url") -> list[dict[str, Any]]:
    return [{"role": "user", "content": [{"type": "text", "text": "What is in this picture?"}, {"type": kind, kind: url}]}]


# ── what counts as a media URL ──────────────────────────────────────────


def test_media_urls_are_found_in_every_shape_a_server_takes() -> None:
    messages = [
        {"role": "system", "content": "See https://intranet.lan/policy for the rules."},
        {"role": "user", "content": [
            {"type": "text", "text": "compare http://10.0.0.1/a with this"},
            {"type": "image_url", "image_url": {"url": "http://10.0.0.1/a.png", "detail": "high"}},
            {"type": "input_image", "image_url": "http://10.0.0.2/b.png"},
            {"image_url": {"url": "http://10.0.0.3/c.png"}},  # vLLM takes a part by its key
            {"type": "audio_url", "audio_url": {"url": "http://10.0.0.4/d.wav"}},
            {"type": "video_url", "video_url": {"url": "http://10.0.0.5/e.mp4"}},
            {"type": "input_audio", "input_audio": {"data": "UklGRiQAAABXQVZF", "format": "wav"}},
            {"type": "image", "source": {"type": "url", "url": "http://10.0.0.6/f.png"}},
            {"type": "file", "file": {"file_data": DATA_URL, "filename": "scan.png"}},
            {"type": "image_url", "image_url": {"url": "http:/10.0.0.7/g.png"}},
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": [
            {"type": "text", "text": "the page"}, {"type": "image_url", "image_url": {"url": DATA_URL}},
        ]},
    ]
    found = list(media_urls.media_sources(messages))
    assert [url for _, url in found] == [
        "http://10.0.0.1/a.png", "http://10.0.0.2/b.png", "http://10.0.0.3/c.png", "http://10.0.0.4/d.wav",
        "http://10.0.0.5/e.mp4", "http://10.0.0.6/f.png", DATA_URL, "http:/10.0.0.7/g.png", DATA_URL,
    ], "text, captions and base64 data are not media URLs"
    assert found[0][0] == "messages[1].content[1].image_url.url"
    assert found[-1][0] == "messages[2].content[1].image_url.url"


@pytest.mark.parametrize(
    "address",
    ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946", "8.8.8.8"],
)
def test_public_addresses_are_public(address: str) -> None:
    assert media_urls.is_public(address)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1", "::1", "10.1.2.3", "172.17.0.1", "192.168.0.10", "fd12::1",  # loopback, private
        "169.254.169.254", "fe80::1", "fd00:ec2::254", "100.100.100.200",  # link-local, metadata
        "100.64.0.1",  # CGNAT
        "224.0.0.1", "ff02::1",  # multicast
        "240.0.0.1", "255.255.255.255", "0.0.0.0", "::",  # noqa: S104 - reserved, unspecified
        "::ffff:10.0.0.1", "64:ff9b::a9fe:a9fe", "2002:a9fe:a9fe::1",  # IPv4 inside IPv6
        "not-an-address",
    ],
)
def test_internal_addresses_are_not(address: str) -> None:
    assert not media_urls.is_public(address)


@pytest.mark.anyio
async def test_a_url_passes_only_when_every_address_is_public() -> None:
    check = media_urls.check_url
    await check("x", DATA_URL, allowed_hosts="")
    await check("x", "https://pictures.example.com/cat.png", allowed_hosts="")
    for url in (
        "https://two-faced.example.com/cat.png",  # one of its addresses is private
        "http://does-not-resolve.example/cat.png",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]:8000/x.png",
        "http://localhost:8000/x.png",
        "ftp://pictures.example.com/cat.png",
        "file:///etc/passwd",
        "//pictures.example.com/cat.png",
        "http:/10.0.0.1/cat.png",  # no authority here; a lenient parser finds one
        "http://10.0.0.1\\@pictures.example.com/cat.png",  # the host checked would not be the host fetched
        "http://pictures.example.com\t/cat.png",
    ):
        with pytest.raises(GatewayError) as refused:
            await check("messages[0].content[1].image_url.url", url, allowed_hosts="")
        assert refused.value.status_code == 400
        assert refused.value.code == "media_url_not_allowed"
        assert refused.value.param == "messages[0].content[1].image_url.url"


@pytest.mark.anyio
async def test_allowed_hosts_let_internal_ones_through() -> None:
    allowed = "media.lan, *.pictures.lan, 10.20.0.0/16, 192.168.5.7"
    for url in (
        "http://media.lan/a.png", "http://cdn.pictures.lan/a.png", "http://10.20.3.4/a.png", "http://192.168.5.7/a.png",
    ):
        await media_urls.check_url("x", url, allowed_hosts=allowed)
    for url in ("http://other.lan/a.png", "http://pictures.lan.evil.example/a.png", "http://10.21.0.1/a.png"):
        with pytest.raises(GatewayError):
            await media_urls.check_url("x", url, allowed_hosts=allowed)


# ── the routes ───────────────────────────────────────────────────────────


@pytest.mark.anyio
@pytest.mark.parametrize("stream", [False, True], ids=["whole", "streamed"])
@pytest.mark.parametrize(
    ("url", "kind"),
    [
        ({"url": "http://169.254.169.254/latest/meta-data/iam/"}, "image_url"),
        ({"url": "http://10.0.0.7:8080/admin"}, "audio_url"),
        ({"url": "http://localhost:6379/"}, "video_url"),
    ],
)
async def test_an_internal_media_url_is_refused_before_the_model_is_asked(
    stream: bool, url: dict[str, str], kind: str,
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None)
    fastapi_app.state.gateway_observability = _Observability()
    sent: list[dict[str, Any]] = []
    _model_answers(monkeypatch, "ok", sent)

    r = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
        "model": ALIAS, "stream": stream, "messages": _picture(url, kind),
    })

    assert r.status_code == 400, r.text
    error = r.json()["error"]
    assert error["type"] == "invalid_request_error"
    assert error["code"] == "media_url_not_allowed"
    assert error["param"] == f"messages[0].content[1].{kind}.url"
    assert "data: URL" in error["message"]
    assert sent == [], "the model was not asked"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "url", [DATA_URL, "https://pictures.example.com/cat.png"], ids=["data", "public"],
)
async def test_data_and_public_urls_reach_the_model_unchanged(
    url: str, fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None)
    fastapi_app.state.gateway_observability = _Observability()
    sent: list[dict[str, Any]] = []
    _model_answers(monkeypatch, "a cat", sent)

    r = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
        "model": ALIAS, "messages": _picture({"url": url}),
    })

    assert r.status_code == 200, r.text
    parts = [p for m in sent[0]["messages"] if isinstance(m["content"], list) for p in m["content"]]
    assert {"type": "image_url", "image_url": {"url": url}} in parts


@pytest.mark.anyio
async def test_the_check_can_be_opened_for_listed_hosts_or_switched_off(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None)
    fastapi_app.state.gateway_observability = _Observability()
    sent: list[dict[str, Any]] = []
    _model_answers(monkeypatch, "ok", sent)

    async def ask(url: str) -> int:
        r = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
            "model": ALIAS, "messages": _picture({"url": url}),
        })
        return r.status_code

    assert await ask("http://10.20.0.9/scan.png") == 400
    monkeypatch.setattr(settings, "media_url_allowed_hosts", "10.20.0.0/16")
    assert await ask("http://10.20.0.9/scan.png") == 200
    assert await ask("http://10.30.0.9/scan.png") == 400
    monkeypatch.setattr(settings, "media_url_check", False)
    assert await ask("http://10.30.0.9/scan.png") == 200, "as it was before"


@pytest.mark.anyio
async def test_a_chats_own_pictures_still_reach_the_model(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The console sends text; the gateway adds the chat's pictures as data: URLs, after the check."""
    await _seed(db_session, provider=ProviderType.VLLM, pii=None)
    fastapi_app.state.gateway_observability = _Observability()

    class _Store:
        async def get_bytes(self, key: str) -> bytes:
            assert key == "k/cat.png"
            return PNG

    fastapi_app.state.chat_file_store = _Store()
    sent: list[dict[str, Any]] = []
    _model_answers(monkeypatch, "a cat", sent)
    sid = await _session(db_session)
    db_session.add(ChatAttachment(
        tenant_id="tenant-a", user_id="user-1", session_id=sid, filename="cat.png", content_type="image/png",
        size_bytes=len(PNG), storage_key="k/cat.png", extraction_status=ExtractionStatus.SKIPPED,
    ))
    await db_session.commit()

    r = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
        "model": ALIAS, "session_id": str(sid), "messages": [{"role": "user", "content": "What is in the picture?"}],
    })

    assert r.status_code == 200, r.text
    urls = [
        p["image_url"]["url"]
        for m in sent[0]["messages"] if isinstance(m["content"], list)
        for p in m["content"] if p.get("type") == "image_url"
    ]
    assert urls == [DATA_URL]


@pytest.mark.anyio
async def test_the_agent_route_checks_them_too_and_takes_the_sdks_pictures(
    client: AsyncClient, db_session: AsyncSession, backend: Any, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    """The .NET SDK sends a tool's pictures (the PowerPoint agent's slide) as data: URLs in the tool message."""
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=None)
    sent: list[dict[str, Any]] = []
    _agent_model(monkeypatch, sent)

    refused = await client.post(AGENT_URL, headers=_agent_headers(), json=_agent_chat(
        messages=_picture({"url": "http://169.254.169.254/latest/"}),
    ))
    assert refused.status_code == 400, refused.text
    assert refused.json()["error"]["code"] == "media_url_not_allowed"
    assert sent == []

    call = {"id": "c1", "type": "function", "function": {"name": "read_page", "arguments": "{}"}}
    taken = await client.post(AGENT_URL, headers=_agent_headers(), json=_agent_chat(messages=[
        {"role": "user", "content": [{"type": "text", "text": "Check this slide"}, {"type": "image_url",
                                                                                   "image_url": {"url": DATA_URL}}]},
        {"role": "assistant", "content": None, "tool_calls": [call]},
        {"role": "tool", "tool_call_id": "c1", "content": [
            {"type": "text", "text": "Slide 3 rendered"}, {"type": "image_url", "image_url": {"url": DATA_URL}},
        ]},
    ]))
    assert taken.status_code == 200, taken.text
    assert DATA_URL in str(sent[0]["messages"])
