from fastapi.routing import APIRouter

from faden_gateway.web.api import docs, monitoring
from faden_gateway.web.api.v1 import router as v1_router
from faden_gateway.web.api.v1.attachments import router as attachments_router
from faden_gateway.web.api.v1.memory import router as memory_router
from faden_gateway.web.api.v1.sessions import router as sessions_router

api_router = APIRouter()
public_router = APIRouter()
api_router.include_router(monitoring.router)
api_router.include_router(docs.router)
public_router.include_router(v1_router, tags=["gateway"])
public_router.include_router(sessions_router)
public_router.include_router(memory_router)
public_router.include_router(attachments_router)
