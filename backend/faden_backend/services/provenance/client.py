"""What the provenance engine says about itself (``GET /api/v1/provenance/info``).

The console's Modules page shows it: the contract the engine speaks, its version, what it can mark and check,
and the plugins it loaded. Asking never fails: an engine that does not answer, refuses the service token or
answers something else is a state of its own, never an error for the person asking.
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
