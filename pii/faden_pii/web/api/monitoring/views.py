from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/health")
def health_check() -> None:
    """Liveness, independent of model availability or queue capacity."""


@router.get("/ready")
def readiness(request: Request):
    from faden_pii.settings import settings

    service = getattr(request.app.state, "pii_service", None)
    ready = bool(
        service
        and service.runtime.healthy
        and not service.runtime.closed
        and settings.service_token
    )
    return JSONResponse({"ready": ready}, status_code=200 if ready else 503)
