"""The sign-in module, called server to server (``contract`` has the routes and shapes)."""

from __future__ import annotations

import functools
import logging
from typing import Any

import httpx
from pydantic import ValidationError

from faden_backend.services.sign_in.contract import (
    DirectoryAnswer,
    DirectoryRequest,
    FinishRequest,
    PasswordRequest,
    ProviderPublic,
    ProviderUpkeep,
    Refusal,
    SignInAnswer,
    SignInStart,
    StartRequest,
)
from faden_backend.services.tls import default_httpx_verify
from faden_backend.settings import settings

log = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class ModuleUnavailableError(Exception):
    """The module is switched off, has no token, cannot be reached, or answered nonsense."""


class ModuleRefusedError(Exception):
    """The module refused a request with an HTTP error; ``status`` and ``detail`` say how."""

    def __init__(self, status: int, detail: Any) -> None:
        super().__init__(f"{status}: {detail}")
        self.status = status
        self.detail = detail


def enabled() -> bool:
    """Whether a sign-in module is switched on."""
    return settings.auth_enabled


@functools.cache
def _http() -> httpx.AsyncClient:
    return httpx.AsyncClient(verify=default_httpx_verify(), timeout=_TIMEOUT)


async def request(method: str, path: str, *, json: Any = None, params: Any = None) -> httpx.Response:
    """*method* *path* on the module, with the service token; an error status raises ModuleRefusedError."""
    if not settings.auth_enabled:
        raise ModuleUnavailableError("the sign-in module is switched off")
    if not settings.auth_service_token:
        log.error("The sign-in module is on but FADEN_BACKEND_AUTH_SERVICE_TOKEN is empty: not calling it")
        raise ModuleUnavailableError("no service token")
    url = f"{settings.auth_service_url.rstrip('/')}{path}"
    try:
        response = await _http().request(
            method, url, json=json, params=params,
            headers={"Authorization": f"Bearer {settings.auth_service_token}"},
        )
    except httpx.HTTPError as exc:
        log.warning("The sign-in module could not be reached: %s", exc)
        raise ModuleUnavailableError(str(exc)) from exc
    if response.status_code >= httpx.codes.BAD_REQUEST:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise ModuleRefusedError(response.status_code, detail)
    return response


async def public_providers() -> list[ProviderPublic]:
    """The providers people may sign in with; none when the module is off or cannot be reached."""
    if not enabled():
        return []
    try:
        response = await request("GET", "/api/v1/providers/public")
        return [ProviderPublic.model_validate(item) for item in response.json()]
    except (ModuleUnavailableError, ModuleRefusedError, ValueError, ValidationError) as exc:
        log.warning("No sign-in providers to show: %s", exc)
        return []


async def provider(key: str) -> ProviderPublic | None:
    """The provider with *key*, if people may sign in with it."""
    return next((p for p in await public_providers() if p.key == key), None)


async def start(key: str, return_url: str) -> SignInStart:
    """Where to send the browser to sign in with *key*."""
    response = await request("POST", "/api/v1/sign-in/start", json=StartRequest(
        provider=key, return_url=return_url,
    ).model_dump())
    try:
        return SignInStart.model_validate(response.json())
    except (ValueError, ValidationError) as exc:
        raise ModuleUnavailableError(f"unreadable start answer: {exc}") from exc


def _answer(response: httpx.Response) -> SignInAnswer:
    try:
        answer = SignInAnswer.model_validate(response.json())
    except (ValueError, ValidationError) as exc:
        raise ModuleUnavailableError(f"unreadable sign-in answer: {exc}") from exc
    if answer.outcome == "identity" and answer.identity is None:
        raise ModuleUnavailableError("an identity answer without an identity")
    if answer.outcome == "refused" and answer.refusal is None:
        return SignInAnswer(outcome="refused", refusal=Refusal.PROVIDER_ERROR, detail=answer.detail)
    return answer


async def finish(key: str, transaction: str, return_url: str, params: dict[str, str]) -> SignInAnswer:
    """The provider's answer for *transaction*, as an identity or a refusal."""
    response = await request("POST", "/api/v1/sign-in/finish", json=FinishRequest(
        provider=key, transaction=transaction, return_url=return_url, params=params,
    ).model_dump())
    return _answer(response)


async def password(key: str, username: str, secret: str) -> SignInAnswer:
    """A directory account's name and password, checked by the module."""
    response = await request("POST", "/api/v1/sign-in/password", json=PasswordRequest(
        provider=key, username=username, password=secret,
    ).model_dump())
    return _answer(response)


async def metadata(key: str) -> httpx.Response:
    """The service-provider metadata of *key* (SAML), as the module made it."""
    return await request("GET", f"/api/v1/providers/{key}/metadata")


async def upkeep() -> list[ProviderUpkeep]:
    """What the hourly upkeep does for each provider's people."""
    response = await request("GET", "/api/v1/upkeep")
    try:
        return [ProviderUpkeep.model_validate(item) for item in response.json()]
    except (ValueError, ValidationError) as exc:
        raise ModuleUnavailableError(f"unreadable upkeep answer: {exc}") from exc


async def directory(key: str, subjects: list[str]) -> DirectoryAnswer:
    """Which of *subjects* the directory provider *key* still has, and how."""
    response = await request("POST", "/api/v1/directory", json=DirectoryRequest(
        provider=key, subjects=subjects,
    ).model_dump())
    try:
        return DirectoryAnswer.model_validate(response.json())
    except (ValueError, ValidationError) as exc:
        raise ModuleUnavailableError(f"unreadable directory answer: {exc}") from exc
