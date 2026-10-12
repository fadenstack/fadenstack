"""Refuse to start, outside development, on a secret published with Fadenstack's source code.

The compose file fell back to fixed development values for these, and the settings still default to them.
They are in the public repository: an install that runs on one is open to anyone who reads it. ``faden deploy``
writes random values into ``.env``; an install started by hand has to set them (Help: Installing, Secrets in
.env).

The settings master key seals stored secrets. An install that has run on the published one keeps them
readable only under that key, so it may go on with it once the operator says so (FADEN_ALLOW_PUBLISHED_KEYS),
with a warning at every start. Tokens seal nothing: a new value loses nothing, so there is no such way round.
"""

from __future__ import annotations

import logging

from faden_backend.settings import Settings

log = logging.getLogger(__name__)

#: Development values of the settings master key: settings.py's default, and the compose file's fallback.
PUBLISHED_MASTER_KEYS = frozenset({"dev-settings-master-key-change-me"})
#: Development values of the MCP and skills service tokens (settings.py's defaults, the compose file's).
PUBLISHED_MCP_TOKENS = frozenset({"dev-mcp-service-token", "dev-mcp-service-token-change-me"})
PUBLISHED_SKILLS_TOKENS = frozenset({"dev-skills-service-token", "dev-skills-service-token-change-me"})

MASTER_KEY_REFUSED = (
    "FADEN_BACKEND_SETTINGS_MASTER_KEY is the development default published with Fadenstack's source code: "
    "anyone could read the secrets this install stores with it.\n"
    "  - A new install: set it in .env to a long random value (openssl rand -base64 48) and start again. "
    "faden deploy writes one.\n"
    "  - An install that has already run on it sealed its stored secrets with this key, and no other key reads "
    "them. To keep them, leave the key as it is, set FADEN_ALLOW_PUBLISHED_KEYS=true in .env, and change the "
    "key later with `faden secrets rotate` (Help: Rotating the keys)."
)

ROTATION_UNFINISHED = (
    "A key rotation is unfinished: the previous settings master key is still set, and values sealed with it "
    "still open. Run `faden secrets rotate` again to finish it."
)

MASTER_KEY_KEPT = (
    "This install runs on the published development settings master key (FADEN_ALLOW_PUBLISHED_KEYS=true): "
    "anyone could read the secrets it stores. Change FADEN_BACKEND_SETTINGS_MASTER_KEY with `faden secrets rotate` "
    "(Help: Rotating the keys)."
)


def token_refused(module: str, prefix: str) -> str:
    """Why a module's published service token is refused, and what to set."""
    names = f"FADEN_GATEWAY_{prefix}_SERVICE_TOKEN, FADEN_BACKEND_{prefix}_SERVICE_TOKEN and FADEN_{prefix}_SERVICE_TOKEN"
    return (
        f"FADEN_BACKEND_{prefix}_SERVICE_TOKEN is the development default published with Fadenstack's source "
        f"code: anyone could call the {module} service as the backend does. Set {names} in .env to one long "
        "random value (openssl rand -base64 32) and start again. Nothing is stored with it, so a new value "
        "loses nothing."
    )


def problems(settings: Settings) -> list[str]:
    """What keeps the backend from starting with *settings*; empty in the dev environment."""
    if settings.environment == "dev":
        return []
    found: list[str] = []
    if settings.settings_master_key in PUBLISHED_MASTER_KEYS and not settings.allow_published_keys:
        found.append(MASTER_KEY_REFUSED)
    if settings.mcp_enabled and settings.mcp_service_token in PUBLISHED_MCP_TOKENS:
        found.append(token_refused("MCP", "MCP"))
    if settings.skills_enabled and settings.skills_service_token in PUBLISHED_SKILLS_TOKENS:
        found.append(token_refused("skills", "SKILLS"))
    return found


def refuse_published_secrets(settings: Settings) -> None:
    """Stop the process when *settings* hold a published secret; warn when one is kept on purpose."""
    found = problems(settings)
    if found:
        raise SystemExit("The backend does not start:\n" + "\n".join(f"* {problem}" for problem in found))
    if settings.environment != "dev" and settings.settings_master_key in PUBLISHED_MASTER_KEYS:
        log.warning(MASTER_KEY_KEPT)
    if settings.settings_master_key_previous:
        log.warning(ROTATION_UNFINISHED)
