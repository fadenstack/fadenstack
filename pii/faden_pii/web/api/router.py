from fastapi import Depends
from fastapi.routing import APIRouter

from faden_pii.web.api import docs, monitoring
from faden_pii.web.api.auth import verify_service_token
from faden_pii.web.api.pii.views import router as pii_router

api_router = APIRouter()
# Health stays open (container health checks, the MCP service's mode default).
api_router.include_router(monitoring.router)
api_router.include_router(docs.router)
# Everything that reads or returns personal data needs the service token.
api_router.include_router(
    pii_router, prefix="/v1/pii", tags=["pii"], dependencies=[Depends(verify_service_token)],
)
