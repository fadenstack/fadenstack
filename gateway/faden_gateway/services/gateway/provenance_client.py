"""HTTP client for the provenance micro-service.

The service marks generated content and signs generation receipts. The
gateway has each picture it returns marked (``mark_image``, from
``picture_marking``), and reads what the engine offers
(``GET /api/v1/provenance/info``) and whether it is up (``GET /api/health``)
for the services manifest (``/v1/services``).

The gateway uses only an engine that speaks the contract it knows
(``CONTRACT``): ``info()`` refuses any other.
"""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import httpx

from faden_gateway.services.registry import ProbeResult, ServiceStatus

logger = logging.getLogger(__name__)

#: The one contract this gateway speaks with a provenance engine.
CONTRACT = "provenance/v1"

#: Where the gateway's token for the service is set (the variable's name, not a token).
TOKEN_SETTING = "FADEN_GATEWAY_PROVENANCE_SERVICE_TOKEN"  # noqa: S105

_INFO_TIMEOUT_SEC = 5.0
_HEALTH_TIMEOUT_SEC = 3.0
#: The engine answers a mark within 30 s or says it is busy.
_MARK_TIMEOUT_SEC = 40.0

#: The pictures the engine marks.
MEDIA_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})

#: ``generated``: a picture made from a prompt; ``rewritten``: an edit of an existing one.
EditKind = Literal["generated", "rewritten"]

#: An engine's error code, and a receipt id, as the gateway passes them on (a header, an error's ``code``).
_CODE = re.compile(r"^[a-z0-9_]{1,64}$")
_RECEIPT_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


@dataclass(frozen=True, slots=True)
class ProvenanceCapabilities:
    """What the engine can do; each is off unless the engine says it is on."""

    images: bool = False
    receipts: bool = False
    records: bool = False
    detect: bool = False


@dataclass(frozen=True, slots=True)
class ProvenanceInfo:
    """The engine's ``/api/v1/provenance/info``."""

    contract: str
    engine_version: str
    capabilities: ProvenanceCapabilities = field(default_factory=ProvenanceCapabilities)
    #: The engine's plugins, as it lists them.
    plugins: tuple[Any, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract": self.contract,
            "engine_version": self.engine_version,
            "capabilities": asdict(self.capabilities),
            "plugins": list(self.plugins),
        }


class ProvenanceUnavailableError(Exception):
    """The provenance service gave no info: it could not be reached, or it refused or failed (``reached``)."""

    def __init__(self, message: str, *, reached: bool, status_code: int | None = None) -> None:
        super().__init__(message)
        self.reached = reached
        self.status_code = status_code


class ProvenanceContractError(ProvenanceUnavailableError):
    """The engine speaks a contract this gateway does not know; the gateway does not use it."""

    def __init__(self, message: str, *, contract: str, engine_version: str | None) -> None:
        super().__init__(message, reached=True)
        self.contract = contract
        self.engine_version = engine_version


@dataclass(frozen=True, slots=True)
class MarkedImage:
    """A picture as the engine marked it, and its receipt."""

    image: bytes
    media_type: str
    receipt_id: str
    #: What the engine applied, as it lists it: ``{"layer", "method", "applied"}``.
    layers: tuple[dict[str, Any], ...] = ()


class ProvenanceMarkError(Exception):
    """A picture was not marked; ``code`` says why, in the engine's words or the gateway's.

    The engine's: ``busy``, ``no_signing_key``, ``unavailable``. The gateway's:
    ``unreachable``, ``timeout``, ``token_refused`` (401, 403), ``too_large``
    (413), ``not_a_picture`` (422), ``unreadable_answer``, and ``not_marked``
    (the answer does not say a watermark and metadata were applied).
    """

    def __init__(self, message: str, *, code: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _engine_error(resp: httpx.Response) -> tuple[str, str]:
    """An engine error's code and message (``{"detail": {"code", "message"}}``); ``unavailable`` without."""
    try:
        detail = resp.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    if not isinstance(detail, dict):
        return "unavailable", ""
    code = detail.get("code")
    message = str(detail.get("message") or "")[:200]
    return (code if isinstance(code, str) and _CODE.match(code) else "unavailable"), message


def _applied(layers: list[Any], layer: str) -> bool:
    return any(
        isinstance(entry, dict) and entry.get("layer") == layer and entry.get("applied") is True
        for entry in layers
    )


def _read_marked(resp: httpx.Response) -> MarkedImage:
    """The engine's 200 to a mark: the marked picture, with a watermark and metadata applied."""
    try:
        data = resp.json()
        image = base64.b64decode(str(data["image"]), validate=True)
        media_type = str(data["media_type"])
        receipt_id = str(data["receipt_id"])
        layers = data.get("layers") or []
        if not image or media_type not in MEDIA_TYPES or not _RECEIPT_ID.match(receipt_id):
            raise ValueError("no picture, an unknown media type or a receipt id that cannot be read")
        if not isinstance(layers, list):
            raise TypeError("layers is not a list")
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ProvenanceMarkError(
            "The provenance service's answer could not be read.",
            code="unreadable_answer", status_code=resp.status_code,
        ) from exc
    if not (_applied(layers, "watermark") and _applied(layers, "metadata")):
        raise ProvenanceMarkError(
            "The provenance service's answer does not say a watermark and metadata were applied.",
            code="not_marked", status_code=resp.status_code,
        )
    return MarkedImage(image=image, media_type=media_type, receipt_id=receipt_id, layers=tuple(layers))


def _capabilities(raw: Any) -> ProvenanceCapabilities:
    """The capabilities the engine sends; a flag that is missing or not ``true`` is off."""
    if not isinstance(raw, dict):
        return ProvenanceCapabilities()
    return ProvenanceCapabilities(
        images=raw.get("images") is True,
        receipts=raw.get("receipts") is True,
        records=raw.get("records") is True,
        detect=raw.get("detect") is True,
    )


class ProvenanceClient:
    """Async client wrapping the provenance service's HTTP endpoints."""

    def __init__(self, *, base_url: str, http_client: httpx.AsyncClient, service_token: str) -> None:
        self._base = base_url.rstrip("/")
        self._http = http_client
        self._has_token = bool(service_token)
        # The service refuses ``info`` without its token; none set, the call
        # goes out bare and is refused there (and the manifest says why).
        self._headers = {"Authorization": f"Bearer {service_token}"} if service_token else {}
        # Each problem is logged once per process, not on every manifest read.
        self._logged: set[str] = set()

    def _log_once(self, key: str, level: int, message: str, *args: Any) -> None:
        if key in self._logged:
            logger.debug(message, *args)
            return
        self._logged.add(key)
        logger.log(level, message, *args)

    def _refusal_reason(self) -> str:
        """Why the service refuses the gateway's token (401, 403)."""
        if self._has_token:
            return f"{TOKEN_SETTING} is not the service's token"
        return f"{TOKEN_SETTING} is empty"

    def _mark_refused(self, resp: httpx.Response) -> ProvenanceMarkError:
        """Why the engine answered a mark with an error (*resp* is 4xx or 5xx)."""
        status = resp.status_code
        if status in (401, 403):
            message = f"The provenance service refused the gateway ({status}): {self._refusal_reason()}."
            return ProvenanceMarkError(message, code="token_refused", status_code=status)
        if status == 413:
            message = "The picture is over the provenance service's size limit."
            return ProvenanceMarkError(message, code="too_large", status_code=status)
        if status == 422:
            message = "The provenance service did not take the bytes for a picture."
            return ProvenanceMarkError(message, code="not_a_picture", status_code=status)
        code, said = _engine_error(resp)
        message = f"The provenance service answered {status} ({code})" + (f": {said}" if said else ".")
        return ProvenanceMarkError(message, code=code, status_code=status)

    async def healthy(self) -> bool:
        """Whether the service answers its health check (``GET /api/health``) with ``ok``.

        Says nothing about the token or the contract: ``info()`` does.
        """
        try:
            resp = await self._http.get(f"{self._base}/api/health", timeout=_HEALTH_TIMEOUT_SEC)
        except httpx.HTTPError:
            logger.debug("Provenance health check failed", exc_info=True)
            return False
        if resp.status_code >= 400:
            return False
        try:
            return resp.json().get("status") == "ok"
        except (ValueError, AttributeError):
            return False

    async def info(self) -> ProvenanceInfo:
        """What the engine offers: ``GET /api/v1/provenance/info``, with the gateway's token.

        Raises ``ProvenanceContractError`` when the engine speaks a contract
        other than ``CONTRACT``, and ``ProvenanceUnavailableError`` when there
        is no info: not reached, the token refused (401, 403), or an answer
        that cannot be read.
        """
        try:
            resp = await self._http.get(
                f"{self._base}/api/v1/provenance/info", headers=self._headers, timeout=_INFO_TIMEOUT_SEC,
            )
        except httpx.HTTPError as exc:
            raise ProvenanceUnavailableError(
                "The provenance service could not be reached.", reached=False,
            ) from exc
        if resp.status_code in (401, 403):
            reason = self._refusal_reason()
            self._log_once(
                f"refused-{resp.status_code}", logging.WARNING,
                "The provenance service at %s refused the gateway (%d): %s.",
                self._base, resp.status_code, reason,
            )
            raise ProvenanceUnavailableError(
                f"The provenance service refused the gateway ({resp.status_code}): {reason}.",
                reached=True, status_code=resp.status_code,
            )
        if resp.status_code >= 400:
            raise ProvenanceUnavailableError(
                f"The provenance service answered {resp.status_code}.",
                reached=True, status_code=resp.status_code,
            )
        try:
            data = resp.json()
            contract = str(data["contract"])
            engine_version = str(data["engine_version"])
            plugins = data.get("plugins") or []
            if not isinstance(plugins, list):
                raise TypeError("plugins is not a list")
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ProvenanceUnavailableError(
                "The provenance service's info could not be read.",
                reached=True, status_code=resp.status_code,
            ) from exc
        if contract != CONTRACT:
            self._log_once(
                f"contract-{contract}", logging.ERROR,
                "The provenance engine at %s (%s) speaks contract %r; this gateway knows %r only "
                "and does not use it. Run an engine that speaks %r.",
                self._base, engine_version, contract, CONTRACT, CONTRACT,
            )
            raise ProvenanceContractError(
                f"The engine speaks {contract}; this gateway knows {CONTRACT} only.",
                contract=contract, engine_version=engine_version,
            )
        return ProvenanceInfo(
            contract=contract,
            engine_version=engine_version,
            capabilities=_capabilities(data.get("capabilities")),
            plugins=tuple(plugins),
        )

    async def mark_image(
        self,
        image_bytes: bytes,
        *,
        media_type: str,
        model: str,
        agent: str | None = None,
        edit_kind: EditKind = "generated",
    ) -> MarkedImage:
        """The picture marked by the engine: ``POST /api/v1/provenance/images/mark``, with the token.

        The engine embeds a watermark and the metadata, signs them and keeps a
        receipt. It answers within 30 s or says it is busy; the call waits up
        to 40 s. Raises ``ProvenanceMarkError`` when the picture comes back
        without a watermark and metadata applied, or does not come back.
        """
        body = {
            "image": base64.b64encode(image_bytes).decode("ascii"),
            "media_type": media_type,
            "model": model,
            "agent": agent,
            "edit_kind": edit_kind,
        }
        try:
            resp = await self._http.post(
                f"{self._base}/api/v1/provenance/images/mark",
                json=body, headers=self._headers, timeout=_MARK_TIMEOUT_SEC,
            )
        except httpx.TimeoutException as exc:
            message = f"The provenance service did not answer within {_MARK_TIMEOUT_SEC:g} s."
            raise ProvenanceMarkError(message, code="timeout") from exc
        except httpx.HTTPError as exc:
            message = "The provenance service could not be reached."
            raise ProvenanceMarkError(message, code="unreachable") from exc
        if resp.status_code >= 400:
            raise self._mark_refused(resp)
        return _read_marked(resp)

    async def probe(self) -> ProbeResult:
        """The module's line in the services manifest: healthy with the engine's info, or why not."""
        try:
            info = await self.info()
        except ProvenanceContractError as exc:
            return ProbeResult(
                status=ServiceStatus.UNHEALTHY,
                version=exc.engine_version,
                metadata={"contract": exc.contract, "error": str(exc)},
            )
        except ProvenanceUnavailableError as exc:
            return ProbeResult(status=ServiceStatus.UNHEALTHY, metadata={"error": str(exc)})
        details = info.to_dict()
        details.pop("engine_version")
        return ProbeResult(status=ServiceStatus.HEALTHY, version=info.engine_version, metadata=details)
