"""Refuse to start, outside development, on a service token published with Fadenstack's source code.

The compose file fell back to a fixed development token, which is in the public repository; an empty token let
every caller in. ``faden deploy`` and ``faden module enable skills`` write a random one into ``.env``; an install
started by hand has to set it (Help: Installing, Secrets in .env). Nothing is stored with it.
"""

from __future__ import annotations

from faden_skills.settings import Settings

PUBLISHED_TOKENS = frozenset({"dev-skills-service-token", "dev-skills-service-token-change-me"})

_TOKEN_NAMES = (
    "FADEN_GATEWAY_SKILLS_SERVICE_TOKEN, FADEN_BACKEND_SKILLS_SERVICE_TOKEN and FADEN_SKILLS_SERVICE_TOKEN"
)

TOKEN_REFUSED = (
    "FADEN_SKILLS_SERVICE_TOKEN is the development default published with Fadenstack's source code: anyone could "
    f"call the skills service as the gateway and the backend do. Set {_TOKEN_NAMES} in .env to one long random "
    "value (openssl rand -base64 32) and start again. Nothing is stored with it, so a new value loses nothing."
)
TOKEN_EMPTY = (
    "FADEN_SKILLS_SERVICE_TOKEN is empty: the skills service would answer every caller without a token. "
    f"Set {_TOKEN_NAMES} in .env to one long random value (openssl rand -base64 32) and start again."
)


def problems(settings: Settings) -> list[str]:
    """What keeps the skills service from starting with *settings*; empty in the dev environment."""
    if settings.environment == "dev":
        return []
    if not settings.service_token:
        return [TOKEN_EMPTY]
    if settings.service_token in PUBLISHED_TOKENS:
        return [TOKEN_REFUSED]
    return []


def refuse_published_secrets(settings: Settings) -> None:
    """Stop the process when *settings* hold a published or empty token."""
    found = problems(settings)
    if found:
        raise SystemExit("The skills service does not start:\n" + "\n".join(f"* {problem}" for problem in found))
