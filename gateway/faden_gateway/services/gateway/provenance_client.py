"""HTTP client for the provenance micro-service.

The service marks generated content and signs generation receipts. The
gateway has each picture it returns marked (``mark_image``, from
``picture_marking``), asks for a receipt for each answer with text
(``receipt``, from ``text_receipts``), passes on what an agent's SDK asks of
the engine (``sign_record``, ``report_placement``, ``policy``, ``anchors``:
the public routes in ``web/api/v1/provenance.py``), and reads what the engine
offers (``GET /api/v1/provenance/info``) and whether it is up
(``GET /api/health``) for the services manifest (``/v1/services``).

The gateway uses only an engine that speaks the contract it knows
(``CONTRACT``): ``info()`` refuses any other.
"""

from __future__ import annotations

import base64
import logging
import re
import time
from collections.abc import Callable
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
#: Receipts, records, placements, the policy and the anchors are the engine's fast lane: it answers in
#: milliseconds, so a turn waits for a receipt 5 s at most.
_RECEIPT_TIMEOUT_SEC = 5.0
_FAST_TIMEOUT_SEC = 5.0
#: How long the anchors are kept between reads: they change only when a signing key is rotated, and the
#: route that serves them needs no sign-in.
_ANCHORS_TTL_SEC = 60.0

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


class ProvenanceRequestError(Exception):
    """A call to the engine did not give what was asked; ``code`` says why, in the engine's words or ours.

    The engine's: ``busy``, ``no_signing_key``, ``unavailable``. The gateway's:
    ``unreachable``, ``timeout``, ``token_refused`` (401, 403), ``too_large``
    (413), ``invalid_request`` (422: the engine did not take the body), and
    ``unreadable_answer``.
    """

    def __init__(self, message: str, *, code: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class ProvenanceMarkError(ProvenanceRequestError):
    """A picture was not marked; ``code`` says why, in the engine's words or the gateway's.

    The engine's: ``busy``, ``no_signing_key``, ``unavailable``. The gateway's:
    ``unreachable``, ``timeout``, ``token_refused`` (401, 403), ``too_large``
    (413), ``not_a_picture`` (422), ``unreadable_answer``, and ``not_marked``
    (the answer does not say a watermark and metadata were applied).
    """


@dataclass(frozen=True, slots=True)
class SignedReceipt:
    """A receipt the engine signed (``SignedReceipt``): the ``GenerationReceipt``, its JWS, its time-stamp."""

    #: The ``GenerationReceipt`` as the engine sent it; its JWS signs exactly these fields.
    receipt: dict[str, Any]
    signature: str
    #: The RFC 3161 time-stamp token (base64 DER), where a time-stamp authority answered.
    timestamp: str | None = None

    @property
    def receipt_id(self) -> str:
        return str(self.receipt["id"])

    def to_dict(self) -> dict[str, Any]:
        """As the client gets it: the receipt and its signature, and the time-stamp when there is one."""
        out: dict[str, Any] = {"receipt": self.receipt, "signature": self.signature}
        if self.timestamp:
            out["timestamp"] = self.timestamp
        return out


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


def _unreadable(resp: httpx.Response) -> ProvenanceRequestError:
    return ProvenanceRequestError(
        "The provenance service's answer could not be read.",
        code="unreadable_answer", status_code=resp.status_code,
    )


def _object(resp: httpx.Response) -> dict[str, Any]:
    """The engine's 2xx answer, a JSON object."""
    try:
        data = resp.json()
    except ValueError:
        raise _unreadable(resp) from None
    if not isinstance(data, dict):
        raise _unreadable(resp)
    return data


def _signed(resp: httpx.Response, key: str) -> dict[str, Any]:
    """A signed answer (``SignedReceipt``, ``SignedRecord``): *key*'s object, with an id, and a JWS."""
    data = _object(resp)
    signed = data.get(key)
    signature = data.get("signature")
    timestamp = data.get("timestamp")
    if (
        not isinstance(signed, dict)
        or not _RECEIPT_ID.match(str(signed.get("id") or ""))
        or not isinstance(signature, str)
        or not signature
        or not (timestamp is None or isinstance(timestamp, str))
    ):
        raise _unreadable(resp)
    return data


def _anchors(data: dict[str, Any]) -> bool:
    return (
        isinstance(data.get("signing_chains"), list)
        and isinstance(data.get("trust_anchors"), list)
        and isinstance(data.get("detector"), dict)
    )


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

    def __init__(
        self,
        *,
        base_url: str,
        http_client: httpx.AsyncClient,
        service_token: str,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._http = http_client
        self._has_token = bool(service_token)
        # The service refuses ``info`` without its token; none set, the call
        # goes out bare and is refused there (and the manifest says why).
        self._headers = {"Authorization": f"Bearer {service_token}"} if service_token else {}
        # Each problem is logged once per process, not on every manifest read.
        self._logged: set[str] = set()
        self._clock = clock
        #: The anchors last read, and until when they are served without asking again.
        self._anchors: tuple[dict[str, Any], float] | None = None

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

    def _call_refused(self, resp: httpx.Response) -> ProvenanceRequestError:
        """Why the engine answered a receipt, record, placement, policy or anchors call with an error."""
        status = resp.status_code
        if status in (401, 403):
            message = f"The provenance service refused the gateway ({status}): {self._refusal_reason()}."
            return ProvenanceRequestError(message, code="token_refused", status_code=status)
        if status == 413:
            message = "The request is over the provenance service's size limit."
            return ProvenanceRequestError(message, code="too_large", status_code=status)
        if status == 422:
            message = "The provenance service did not take the request."
            return ProvenanceRequestError(message, code="invalid_request", status_code=status)
        code, said = _engine_error(resp)
        message = f"The provenance service answered {status} ({code})" + (f": {said}" if said else ".")
        return ProvenanceRequestError(message, code=code, status_code=status)

    async def _call(
        self, method: str, path: str, *, timeout: float, body: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """One call to the engine with the token; its 2xx answer, or ``ProvenanceRequestError``."""
        try:
            resp = await self._http.request(
                method, f"{self._base}{path}", json=body, headers=self._headers, timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            message = f"The provenance service did not answer within {timeout:g} s."
            raise ProvenanceRequestError(message, code="timeout") from exc
        except httpx.HTTPError as exc:
            message = "The provenance service could not be reached."
            raise ProvenanceRequestError(message, code="unreachable") from exc
        if resp.status_code >= 400:
            raise self._call_refused(resp)
        return resp

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

    async def receipt(
        self,
        *,
        content_sha256: str,
        model: str,
        watermark: dict[str, Any],
        agent: str | None = None,
        model_version: str | None = None,
    ) -> SignedReceipt:
        """A signed receipt for a text a model wrote: ``POST /api/v1/provenance/receipts``, with the token.

        The engine gets the text's hash only (``content_hash.text_sha256``),
        never the text. It answers in milliseconds; the call waits 5 s at
        most. Raises ``ProvenanceRequestError`` when no signed receipt comes back.
        """
        body = {
            "kind": "text",
            "model": model,
            "model_version": model_version,
            "agent": agent,
            "content_sha256": content_sha256,
            "watermark": watermark,
        }
        resp = await self._call(
            "POST", "/api/v1/provenance/receipts", body=body, timeout=_RECEIPT_TIMEOUT_SEC,
        )
        data = _signed(resp, "receipt")
        return SignedReceipt(
            receipt=data["receipt"], signature=data["signature"], timestamp=data.get("timestamp"),
        )

    async def sign_record(self, record: dict[str, Any], *, agent: str | None) -> dict[str, Any]:
        """A signed record of a placement (``SignedRecord``): ``POST /api/v1/provenance/records/sign``.

        *record* is a ``RecordRequest`` without its agent, which is *agent*.
        Raises ``ProvenanceRequestError`` when no signed record comes back.
        """
        resp = await self._call(
            "POST", "/api/v1/provenance/records/sign",
            body={**record, "agent": agent}, timeout=_FAST_TIMEOUT_SEC,
        )
        return _signed(resp, "record")

    async def report_placement(self, report: dict[str, Any], *, agent: str | None) -> None:
        """Keep a placement report (``PlacementReport``) for *agent*: ``POST /api/v1/provenance/placements``.

        Raises ``ProvenanceRequestError`` when the engine does not take it.
        """
        await self._call(
            "POST", "/api/v1/provenance/placements",
            body={**report, "agent": agent}, timeout=_FAST_TIMEOUT_SEC,
        )

    async def policy(self) -> dict[str, Any]:
        """The engine's default ``ProvenancePolicy``: ``GET /api/v1/provenance/policy``."""
        return _object(await self._call("GET", "/api/v1/provenance/policy", timeout=_FAST_TIMEOUT_SEC))

    async def anchors(self) -> dict[str, Any]:
        """What anyone needs to check this installation's signatures offline (``Anchors``).

        ``GET /api/v1/provenance/anchors``, kept for a minute: they change
        only when a signing key is rotated, and they are served to anyone.
        """
        kept = self._anchors
        if kept is not None and kept[1] > self._clock():
            return kept[0]
        resp = await self._call("GET", "/api/v1/provenance/anchors", timeout=_FAST_TIMEOUT_SEC)
        data = _object(resp)
        if not _anchors(data):
            raise _unreadable(resp)
        self._anchors = (data, self._clock() + _ANCHORS_TTL_SEC)
        return data

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
