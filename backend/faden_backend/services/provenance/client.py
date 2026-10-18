"""What the backend asks the provenance engine, with the service token.

``engine_info`` (``GET /api/v1/provenance/info``): the console's Modules page shows it -- the contract the
engine speaks, its version, what it can mark and check, and the plugins it loaded. Asking never fails: an
engine that does not answer, refuses the service token or answers something else is a state of its own,
never an error for the person asking.

``verify`` (``POST /api/v1/provenance/verify``) and ``anchors`` (``GET /api/v1/provenance/anchors``): the
content check. They return the engine's answer or raise :class:`EngineError`, which carries the HTTP status
and the code the backend answers with. What is sent to be checked is never logged: not here, not in an
error.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import httpx

from faden_backend.services.tls import default_httpx_verify
from faden_backend.settings import settings

logger = logging.getLogger(__name__)

#: The contract this backend speaks with the engine.
CONTRACT = "provenance/v1"
INFO_PATH = "/api/v1/provenance/info"

#: ``running``: it answered with its info. ``not_running``: no answer (stopped, unreachable, timed out,
#: a 5xx). ``refused``: it refused the service token. ``unexpected``: it answered, but not with its info.
EngineState = Literal["running", "not_running", "refused", "unexpected"]


@dataclass(slots=True)
class EngineInfo:
    """The engine's answer, or why there is none."""

    state: EngineState
    contract: str | None = None
    engine_version: str | None = None
    capabilities: dict[str, bool] = field(default_factory=dict)
    plugins: list[str] = field(default_factory=list)

    @property
    def contract_supported(self) -> bool:
        """Whether the engine speaks the contract this backend speaks."""
        return self.contract == CONTRACT

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready, with the contract this backend expects beside the engine's."""
        return {**asdict(self), "contract_supported": self.contract_supported, "expected_contract": CONTRACT}


def _plugin_name(plugin: Any) -> str:
    if isinstance(plugin, dict) and plugin.get("name"):
        return str(plugin["name"])
    return str(plugin)


def _parse(body: Any) -> EngineInfo:
    """The info route's body as :class:`EngineInfo`; anything without a contract is ``unexpected``."""
    if not isinstance(body, dict) or not isinstance(body.get("contract"), str):
        return EngineInfo(state="unexpected")
    capabilities = body.get("capabilities")
    plugins = body.get("plugins")
    version = body.get("engine_version")
    return EngineInfo(
        state="running",
        contract=body["contract"],
        engine_version=str(version) if version is not None else None,
        capabilities=(
            {str(name): bool(on) for name, on in capabilities.items()}
            if isinstance(capabilities, dict)
            else {}
        ),
        plugins=[_plugin_name(p) for p in plugins] if isinstance(plugins, list) else [],
    )


def _read(resp: httpx.Response) -> EngineInfo:
    """What an answer of the info route says."""
    if resp.status_code in (401, 403):
        return EngineInfo(state="refused")
    if resp.status_code >= 500:
        return EngineInfo(state="not_running")
    if resp.status_code != 200:
        return EngineInfo(state="unexpected")
    try:
        return _parse(resp.json())
    except ValueError:
        return EngineInfo(state="unexpected")


async def engine_info(
    base_url: str | None = None,
    token: str | None = None,
    *,
    timeout_sec: float = 5.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> EngineInfo:
    """Ask the engine's info route with the service token (the settings' unless given); never raises."""
    url = f"{(settings.provenance_service_url if base_url is None else base_url).rstrip('/')}{INFO_PATH}"
    token = settings.provenance_service_token if token is None else token
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with httpx.AsyncClient(
            verify=default_httpx_verify(), timeout=timeout_sec, transport=transport,
        ) as client:
            resp = await client.get(url, headers=headers)
    except httpx.HTTPError:
        logger.debug("The provenance engine did not answer at %s", url, exc_info=True)
        return EngineInfo(state="not_running")
    except Exception:
        logger.warning("The provenance engine could not be asked at %s", url, exc_info=True)
        return EngineInfo(state="not_running")
    return _read(resp)


# ── The content check ─────────────────────────────────────────────────────────────

VERIFY_PATH = "/api/v1/provenance/verify"
ANCHORS_PATH = "/api/v1/provenance/anchors"

#: The largest file the engine checks (its ``max_image_bytes``); a text is held to the same, in UTF-8.
MAX_CONTENT_BYTES = 20 * 1024 * 1024

#: The codes the engine answers 503 with: no hash key set, its lane full, pictures not checkable now.
_ENGINE_503_CODES = frozenset({"no_signing_key", "busy", "unavailable"})


class EngineError(Exception):
    """The engine gave no result: the status and code the backend answers with, and a sentence for people."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message

    def detail(self) -> dict[str, str]:
        """The ``detail`` of the error answer, in the engine's shape."""
        return {"code": self.code, "message": self.message}


def _error_detail(resp: httpx.Response) -> tuple[str | None, str | None]:
    """The engine's ``{"detail": {"code", "message"}}``, or ``(None, None)`` when it answered otherwise."""
    try:
        body = resp.json()
    except ValueError:
        return None, None
    detail = body.get("detail") if isinstance(body, dict) else None
    if not isinstance(detail, dict):
        return None, None
    code, message = detail.get("code"), detail.get("message")
    return (
        str(code) if isinstance(code, str) and code else None,
        str(message) if isinstance(message, str) and message else None,
    )


def _engine_error(resp: httpx.Response) -> EngineError:
    """What a refusal of the engine becomes."""
    code, message = _error_detail(resp)
    if resp.status_code in (401, 403):
        return EngineError(502, "refused", "The provenance engine refused this server's service token.")
    if resp.status_code == 413:
        return EngineError(413, code or "too_large", message or "Content over the size limit is not checked.")
    if resp.status_code == 422:
        return EngineError(
            422, code or "invalid", message or "The provenance engine could not read what was sent.",
        )
    if resp.status_code == 503:
        return EngineError(
            503,
            code if code in _ENGINE_503_CODES else "unavailable",
            message or "The provenance engine cannot check content right now.",
        )
    return EngineError(502, "unexpected", "The provenance engine gave an unexpected answer.")


async def _ask(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None,
    required: tuple[str, ...],
    base_url: str | None,
    token: str | None,
    timeout_sec: float,
    transport: httpx.AsyncBaseTransport | None,
) -> dict[str, Any]:
    """One call to the engine; its JSON answer when it has the *required* keys, else :class:`EngineError`."""
    url = f"{(settings.provenance_service_url if base_url is None else base_url).rstrip('/')}{path}"
    token = settings.provenance_service_token if token is None else token
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with httpx.AsyncClient(
            verify=default_httpx_verify(), timeout=timeout_sec, transport=transport,
        ) as client:
            resp = await client.request(method, url, json=body, headers=headers)
    except httpx.TimeoutException:
        logger.warning("The provenance engine did not answer %s %s in time", method, path)
        raise EngineError(504, "timeout", "The provenance engine took too long to answer.") from None
    except httpx.HTTPError as exc:
        logger.warning("The provenance engine did not answer %s %s: %s", method, path, type(exc).__name__)
        raise EngineError(503, "unavailable", "The provenance engine is not answering.") from None
    except Exception as exc:
        # Whatever it is, the route answers; only its type is logged, never what was sent.
        logger.warning("The provenance engine could not be asked %s %s: %s", method, path, type(exc).__name__)
        raise EngineError(503, "unavailable", "The provenance engine is not answering.") from None
    if resp.status_code != 200:
        error = _engine_error(resp)
        logger.info(
            "The provenance engine answered %s %s with %s (%s)", method, path, resp.status_code, error.code,
        )
        raise error
    try:
        answer = resp.json()
    except ValueError:
        answer = None
    if not isinstance(answer, dict) or any(key not in answer for key in required):
        logger.warning("The provenance engine answered %s %s with something else", method, path)
        raise EngineError(502, "unexpected", "The provenance engine gave an unexpected answer.")
    return answer


async def verify(
    body: dict[str, Any],
    *,
    base_url: str | None = None,
    token: str | None = None,
    timeout_sec: float = 120.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """The engine's ``VerifyResult`` for *body* (a ``VerifyRequest``), or :class:`EngineError`."""
    return await _ask(
        "POST", VERIFY_PATH, body=body,
        required=("verdict", "findings", "content_hash", "detector", "checked_at"),
        base_url=base_url, token=token, timeout_sec=timeout_sec, transport=transport,
    )


async def anchors(
    *,
    base_url: str | None = None,
    token: str | None = None,
    timeout_sec: float = 10.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """The engine's ``Anchors`` (its signing chains, the CAs and the detector), or :class:`EngineError`."""
    return await _ask(
        "GET", ANCHORS_PATH, body=None, required=("signing_chains", "trust_anchors", "detector"),
        base_url=base_url, token=token, timeout_sec=timeout_sec, transport=transport,
    )
