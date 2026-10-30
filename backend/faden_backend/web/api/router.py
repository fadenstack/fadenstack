from fastapi.routing import APIRouter

from faden_backend.web.api import about, docs, i18n, logs, monitoring, users
from faden_backend.web.api.admin import admin_router
from faden_backend.web.api.agents.views import router as agents_router
from faden_backend.web.api.agents.views import tokens_router as agent_tokens_router
from faden_backend.web.api.auth_sso import router as auth_sso_router
from faden_backend.web.api.bootstrap.views import router as bootstrap_router
from faden_backend.web.api.chat.views import router as chat_router
from faden_backend.web.api.inference import inference_router
from faden_backend.web.api.install import router as install_router
from faden_backend.web.api.llm import llm_router
from faden_backend.web.api.node_files import router as node_files_router
from faden_backend.web.api.provenance.public import router as public_provenance_router
from faden_backend.web.api.provenance.views import router as provenance_router
from faden_backend.web.api.skills.views import router as skills_router
from faden_backend.web.api.usage import router as usage_router

api_router = APIRouter()
api_router.include_router(monitoring.router)
api_router.include_router(about.router)
api_router.include_router(users.router)
api_router.include_router(auth_sso_router, prefix="/auth/sso", tags=["auth"])
api_router.include_router(docs.router)
api_router.include_router(bootstrap_router)
api_router.include_router(admin_router, prefix="/admin", tags=["admin"])
api_router.include_router(llm_router, prefix="/llm", tags=["llm"])
api_router.include_router(chat_router, tags=["chat"])
# Skills for everyone: what each user may do is RBAC plus the skill's owner
# and grants (the admin pages use it too; there is no separate admin API).
api_router.include_router(skills_router, tags=["skills"])
# Checking content for marks: for everyone signed in with provenance:check, and -- when an
# administrator offers it -- pictures and files without an account (404 otherwise).
api_router.include_router(provenance_router)
api_router.include_router(public_provenance_router)
api_router.include_router(agents_router)
api_router.include_router(agent_tokens_router)
# Every signed-in person's own usage (the administrators' view is /admin/usage).
api_router.include_router(usage_router, prefix="/usage", tags=["usage"])
api_router.include_router(logs.router, prefix="/logs", tags=["logs"])
api_router.include_router(i18n.router, prefix="/i18n", tags=["i18n"])
api_router.include_router(node_files_router)
api_router.include_router(inference_router, prefix="/inference", tags=["inference"])
# Unauthenticated by design: it carries no secret, and a machine that has
# never enrolled has no credential to present.
api_router.include_router(install_router)
