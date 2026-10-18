"""HTTP client for the provenance micro-service.

The service marks generated content and signs generation receipts. The
gateway reads what its engine offers (``GET /api/v1/provenance/info``) and
whether it is up (``GET /api/health``), for the services manifest
(``/v1/services``); nothing in a request's path calls it.

The gateway uses only an engine that speaks the contract it knows
(``CONTRACT``): ``info()`` refuses any other.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

from faden_gateway.services.registry import ProbeResult, ServiceStatus

logger = logging.getLogger(__name__)

#: The one contract this gateway speaks with a provenance engine.
CONTRACT = "provenance/v1"

#: Where the gateway's token for the service is set (the variable's name, not a token).
TOKEN_SETTING = "FADEN_GATEWAY_PROVENANCE_SERVICE_TOKEN"  # noqa: S105

_INFO_TIMEOUT_SEC = 5.0
_HEALTH_TIMEOUT_SEC = 3.0


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
            reason = (
                f"{TOKEN_SETTING} is not the service's token" if self._has_token
                else f"{TOKEN_SETTING} is empty"
            )
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
