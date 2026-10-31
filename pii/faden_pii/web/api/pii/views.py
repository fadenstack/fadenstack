"""PII scan, redact, sanitize API endpoints.

The PII service is **stateless** — event telemetry is forwarded to the
backend service which owns the ``pii_scan_events`` table.  Stats and
event-log endpoints are served by the backend directly.
"""

from __future__ import annotations

import logging
import time

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from faden_pii.services.pii.service import DEFAULT_ENTITIES
from faden_pii.services.pii.service import PIIService
from faden_pii.settings import settings
from faden_pii.web.api.pii.schema import (
    DetectedEntityDTO,
    PIIDetokenizeRequest,
    PIIDetokenizeResponse,
    PIIPolicyOptionsResponse,
    PIIRedactRequest,
    PIIRedactResponse,
    PIISanitizeRequest,
    PIISanitizeResponse,
    PIIScanRequest,
    PIIScanResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Shared httpx client for fire-and-forget event logging ─────────
_event_client: httpx.AsyncClient | None = None


def _get_event_client() -> httpx.AsyncClient:
    global _event_client
    if _event_client is None:
        _event_client = httpx.AsyncClient(timeout=2.0)
    return _event_client


_DEV_BACKEND_TOKEN = "dev-internal-token"  # noqa: S105


def _backend_event_token() -> str:
    """The token the backend takes event reports on; empty when there is none to present."""
    if settings.backend_token:
        return settings.backend_token
    return _DEV_BACKEND_TOKEN if settings.environment == "dev" else ""


def _backend_event_url() -> str:
    """Build the backend event-log ingestion URL."""
    url = settings.backend_url.strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    return f"{url}/api/admin/pii/events/log"


async def _log_event_async(
    *,
    operation: str,
    mode: str | None = None,
    language: str = "en",
    score_threshold: float = 0.35,
    pii_detected: bool = False,
    entities_found: int = 0,
    entity_type_counts: dict[str, int] | None = None,
    source: str = "api",
    request_id: str | None = None,
    duration_ms: float | None = None,
) -> bool | None:
    """Fire-and-forget POST to the backend event-log endpoint."""
    url = _backend_event_url()
    token = _backend_event_token()
    if not url:
        return
    if not token:
        logger.debug("No backend token: PII event not reported")
        return
    payload = {
        "operation": operation,
        "mode": mode,
        "language": language,
        "score_threshold": score_threshold,
        "pii_detected": pii_detected,
        "entities_found": entities_found,
        "entity_type_counts": entity_type_counts,
        "source": source,
        "request_id": request_id,
        "duration_ms": duration_ms,
    }
    try:
        response = await _get_event_client().post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code in (401, 403, 503):
            logger.warning(
                "The backend refused a PII event (%s): is the internal token set on both sides?",
                response.status_code,
            )
        return response.status_code < 400
    except Exception:
        logger.debug("Failed to forward PII event to backend")
        return False


async def _report_event(request: Request, **event) -> None:
    reporter = getattr(request.app.state, "pii_events", None)
    if reporter is not None:
        reporter.submit(**event)
    elif settings.environment == "pytest":
        # ASGI unit tests do not enter app lifespan.
        await _log_event_async(**event)


def _get_pii_service(request: Request) -> PIIService:
    """Retrieve the PIIService singleton from app state."""
    return request.app.state.pii_service  # type: ignore[no-any-return]


@router.get("/options", response_model=PIIPolicyOptionsResponse)
async def get_policy_options(request: Request) -> PIIPolicyOptionsResponse:
    """Return supported option values for policy configuration UIs.

    The languages are the ones the service has models for; a policy in any
    other language would fail every check.
    """
    service = getattr(request.app.state, "pii_service", None)
    languages = (
        service.supported_languages
        if service is not None
        else [settings.pii_default_language]
    )
    return PIIPolicyOptionsResponse(
        supported_entities=service.supported_entities
        if service is not None and hasattr(service, "supported_entities")
        else list(DEFAULT_ENTITIES),
        supported_languages=languages,
        detector=getattr(
            getattr(getattr(service, "runtime", None), "detector", None), "metadata", {}
        ),
        supported_sanitize_modes=["redact", "tokenize"],
        default_language=settings.pii_default_language,
        default_score_threshold=service.profile.threshold
        if service is not None and hasattr(service, "profile")
        else settings.pii_score_threshold,
        profile_id=service.profile.id
        if service is not None and hasattr(service, "profile")
        else "presidio-legacy",
        profile_fingerprint=service.profile.fingerprint
        if service is not None and hasattr(service, "profile")
        else None,
        calibrated=service.profile.calibrated
        if service is not None and hasattr(service, "profile")
        else False,
        ready=bool(
            service is not None
            and hasattr(service, "runtime")
            and service.runtime.healthy
        ),
        entity_languages=getattr(
            getattr(getattr(service, "runtime", None), "detector", None),
            "entity_languages",
            {},
        ),
        default_entities={
            language: service.default_entities(language) for language in languages
        }
        if service is not None and hasattr(service, "default_entities")
        else {},
    )


@router.post("/scan", response_model=PIIScanResponse)
async def scan_text(
    body: PIIScanRequest,
    request: Request,
) -> PIIScanResponse:
    """Detect PII entities in the provided text."""
    svc = _get_pii_service(request)
    started = time.perf_counter()
    result = await svc.scan(
        body.text,
        language=body.language,
        entities=body.entities,
        score_threshold=svc.validate_profile(
            body.profile_id, body.threshold_mode, body.score_threshold
        ),
    )
    duration_ms = round((time.perf_counter() - started) * 1000.0, 2)
    # Build entity-type breakdown
    type_counts: dict[str, int] = {}
    for e in result.entities:
        type_counts[e.entity_type] = type_counts.get(e.entity_type, 0) + 1
    # Fire-and-forget event to backend (no raw text stored)
    await _report_event(
        request,
        operation="scan",
        language=body.language or settings.pii_default_language,
        score_threshold=svc.profile.threshold
        if body.threshold_mode == "profile" or body.score_threshold is None
        else body.score_threshold,
        pii_detected=result.has_pii,
        entities_found=len(result.entities),
        entity_type_counts=type_counts or None,
        source=request.headers.get("x-pii-source", "api"),
        request_id=request.headers.get("x-request-id"),
        duration_ms=duration_ms,
    )
    return PIIScanResponse(
        has_pii=result.has_pii,
        entities=[
            DetectedEntityDTO(
                entity_type=e.entity_type,
                start=e.start,
                end=e.end,
                score=e.score,
                text=e.text,
                path=e.path,
            )
            for e in result.entities
        ],
        duration_ms=duration_ms,
    )


@router.post("/redact", response_model=PIIRedactResponse)
async def redact_text(
    body: PIIRedactRequest,
    request: Request,
) -> PIIRedactResponse:
    """Detect and redact PII entities from the provided text."""
    svc = _get_pii_service(request)
    started = time.perf_counter()
    result = await svc.redact(
        body.text,
        language=body.language,
        entities=body.entities,
        score_threshold=svc.validate_profile(
            body.profile_id, body.threshold_mode, body.score_threshold
        ),
    )
    duration_ms = round((time.perf_counter() - started) * 1000.0, 2)
    await _report_event(
        request,
        operation="redact",
        mode="redact",
        language=body.language or settings.pii_default_language,
        score_threshold=svc.profile.threshold
        if body.threshold_mode == "profile" or body.score_threshold is None
        else body.score_threshold,
        pii_detected=result.entities_found > 0,
        entities_found=result.entities_found,
        source=request.headers.get("x-pii-source", "api"),
        request_id=request.headers.get("x-request-id"),
        duration_ms=duration_ms,
    )
    return PIIRedactResponse(
        redacted_text=result.redacted_text,
        entities_found=result.entities_found,
        duration_ms=duration_ms,
    )


@router.post("/sanitize", response_model=PIISanitizeResponse)
async def sanitize_payload(
    body: PIISanitizeRequest,
    request: Request,
) -> PIISanitizeResponse | JSONResponse:
    """Sanitize all text fields in an OpenAI-shaped payload.

    Walks ``messages[].content`` (string or multimodal array) and
    ``input`` (embeddings).  Supports ``redact`` and ``tokenize`` modes.
    """
    if body.mode not in ("redact", "tokenize"):
        return JSONResponse(
            status_code=400,
            content={
                "detail": (
                    f"Unsupported sanitize mode '{body.mode}'. "
                    "Supported modes: 'redact', 'tokenize'."
                ),
            },
        )
    svc = _get_pii_service(request)
    started = time.perf_counter()
    result = await svc.sanitize_payload(
        body.payload,
        mode=body.mode,
        language=body.language,
        entities=body.entities,
        score_threshold=svc.validate_profile(
            body.profile_id, body.threshold_mode, body.score_threshold
        ),
        token_mapping=body.token_mapping,
    )
    duration_ms = round((time.perf_counter() - started) * 1000.0, 2)
    type_counts: dict[str, int] = {}
    for e in result.pii_report:
        type_counts[e.entity_type] = type_counts.get(e.entity_type, 0) + 1
    await _report_event(
        request,
        operation="sanitize",
        mode=body.mode,
        language=body.language or settings.pii_default_language,
        score_threshold=svc.profile.threshold
        if body.threshold_mode == "profile" or body.score_threshold is None
        else body.score_threshold,
        pii_detected=result.entities_found > 0,
        entities_found=result.entities_found,
        entity_type_counts=type_counts or None,
        source=request.headers.get("x-pii-source", "api"),
        request_id=request.headers.get("x-request-id"),
        duration_ms=duration_ms,
    )
    return PIISanitizeResponse(
        sanitized_payload=result.payload,
        entities_found=result.entities_found,
        pii_report=[
            DetectedEntityDTO(
                entity_type=e.entity_type,
                start=e.start,
                end=e.end,
                score=e.score,
                text=e.text,
                path=e.path,
            )
            for e in result.pii_report
        ],
        token_mapping=result.token_mapping,
        profile_fingerprint=svc.profile.fingerprint,
        duration_ms=duration_ms,
    )


@router.post("/detokenize", response_model=PIIDetokenizeResponse)
async def detokenize_payload(
    body: PIIDetokenizeRequest,
    request: Request,
) -> PIIDetokenizeResponse:
    """Restore original PII values in an OpenAI-shaped response payload.

    Replaces surrogate tokens (e.g. ``[PERSON_1]``) with their original
    values using the ``token_mapping`` from a prior ``/sanitize`` call.
    """
    svc = _get_pii_service(request)
    restored = await svc.detokenize_payload(
        body.payload,
        token_mapping=body.token_mapping,
    )
    return PIIDetokenizeResponse(payload=restored)
