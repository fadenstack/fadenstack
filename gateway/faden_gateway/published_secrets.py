"""Refuse to start, outside development, on a secret published with Fadenstack's source code.

Mirrors the backend's check (faden_backend/published_secrets.py) for what the gateway holds: the backend's
settings master key, which it reads stored secrets with, and the tokens it presents to the MCP, skills
and provenance services. ``faden deploy`` writes random values into ``.env``; an install started by hand has
to set them (Help: Installing, Secrets in .env).
"""

from __future__ import annotations

import logging

from faden_gateway.settings import Settings

log = logging.getLogger(__name__)

PUBLISHED_MASTER_KEYS = frozenset({"dev-settings-master-key-change-me"})
PUBLISHED_MCP_TOKENS = frozenset({"dev-mcp-service-token", "dev-mcp-service-token-change-me"})
PUBLISHED_SKILLS_TOKENS = frozenset({"dev-skills-service-token", "dev-skills-service-token-change-me"})
PUBLISHED_PROVENANCE_TOKENS = frozenset(
    {"dev-provenance-service-token", "dev-provenance-service-token-change-me"},
)
PUBLISHED_CHAT_KEYS = frozenset({"dev-encryption-key-change-me"})

CHAT_KEY_MISSING = (
    "FADEN_GATEWAY_ENCRYPTION_KEY is empty: chats would be stored readable in the database. "
    "`faden upgrade` writes one into .env; an install started by hand sets it to a long random value "
    "(openssl rand -base64 48). Chats written without a key stay readable and are sealed by "
    "`faden secrets seal`. Keep the key with the backups: chats sealed with it open with no other."
)
CHAT_KEY_REFUSED = (
    "FADEN_GATEWAY_ENCRYPTION_KEY is the development default published with Fadenstack's source code: "
    "anyone could read the chats sealed with it. To keep them, set FADEN_ALLOW_PUBLISHED_KEYS=true in .env "
    "and change the key with `faden secrets rotate --only chat` (Help: Rotating the keys)."
)

MASTER_KEY_REFUSED = (
    "FADEN_GATEWAY_SETTINGS_MASTER_KEY (the backend's FADEN_BACKEND_SETTINGS_MASTER_KEY) is the development "
    "default published with Fadenstack's source code: anyone could read the secrets the install stores with it.\n"
    "  - A new install: set FADEN_BACKEND_SETTINGS_MASTER_KEY in .env to a long random value "
    "(openssl rand -base64 48) and start again. faden deploy writes one.\n"
    "  - An install that has already run on it sealed its stored secrets with this key, and no other key reads "
    "them. To keep them, leave the key as it is, set FADEN_ALLOW_PUBLISHED_KEYS=true in .env, and change the "
    "key later with `faden secrets rotate` (Help: Rotating the keys)."
)

ROTATION_UNFINISHED = (
    "A key rotation is unfinished: a previous key is still set, and values sealed with it still open. "
    "Run `faden secrets rotate` again to finish it."
)

MASTER_KEY_KEPT = (
    "This install runs on the published development settings master key (FADEN_ALLOW_PUBLISHED_KEYS=true). "
    "Change FADEN_BACKEND_SETTINGS_MASTER_KEY with `faden secrets rotate` (Help: Rotating the keys)."
)


def token_refused(module: str, prefix: str) -> str:
    """Why a module's published service token is refused, and what to set."""
    names = (
        f"FADEN_GATEWAY_{prefix}_SERVICE_TOKEN, FADEN_BACKEND_{prefix}_SERVICE_TOKEN "
        f"and FADEN_{prefix}_SERVICE_TOKEN"
    )
    return (
        f"FADEN_GATEWAY_{prefix}_SERVICE_TOKEN is the development default published with Fadenstack's source "
        f"code: anyone could call the {module} service as the gateway does. Set {names} in .env to one long "
        "random value (openssl rand -base64 32) and start again. Nothing is stored with it, so a new value "
        "loses nothing."
    )


def problems(settings: Settings) -> list[str]:
    """What keeps the gateway from starting with *settings*; empty in the dev environment."""
    if settings.environment == "dev":
        return []
    found: list[str] = []
    if settings.settings_master_key in PUBLISHED_MASTER_KEYS and not settings.allow_published_keys:
        found.append(MASTER_KEY_REFUSED)
    if settings.mcp_enabled and settings.mcp_service_token in PUBLISHED_MCP_TOKENS:
        found.append(token_refused("MCP", "MCP"))
    if settings.skills_enabled and settings.skills_service_token in PUBLISHED_SKILLS_TOKENS:
        found.append(token_refused("skills", "SKILLS"))
    provenance_token = settings.provenance_service_token
    if settings.provenance_enabled and provenance_token in PUBLISHED_PROVENANCE_TOKENS:
        found.append(token_refused("provenance", "PROVENANCE"))
    if not settings.encryption_key:
        found.append(CHAT_KEY_MISSING)
    elif settings.encryption_key in PUBLISHED_CHAT_KEYS and not settings.allow_published_keys:
        found.append(CHAT_KEY_REFUSED)
    return found


def refuse_published_secrets(settings: Settings) -> None:
    """Stop the process when *settings* hold a published secret; warn when the master key is kept on purpose."""
    found = problems(settings)
    if found:
        raise SystemExit("The API gateway does not start:\n" + "\n".join(f"* {problem}" for problem in found))
    if settings.environment != "dev" and settings.settings_master_key in PUBLISHED_MASTER_KEYS:
        log.warning(MASTER_KEY_KEPT)
    if settings.settings_master_key_previous or settings.encryption_key_previous:
        log.warning(ROTATION_UNFINISHED)
