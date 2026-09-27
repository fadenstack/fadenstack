from fastapi import APIRouter

from llm_port_skills.web.api.internal.views import router as internal_router
from llm_port_skills.web.api.skills.views import router as skills_router

api_router = APIRouter()


@api_router.get("/health", tags=["monitoring"])
async def health_check() -> dict[str, str]:
    """Public health endpoint."""
    return {"status": "ok"}


api_router.include_router(skills_router, prefix="/skills", tags=["Skills"])
api_router.include_router(
    internal_router,
    prefix="/internal",
    tags=["Skills Internal"],
)
