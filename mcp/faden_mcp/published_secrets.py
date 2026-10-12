"""Refuse to start, outside development, on a secret published with Fadenstack's source code.

The compose file fell back to fixed development values for the MCP service's token and encryption key; they
are in the public repository. ``faden deploy`` and ``faden module enable mcp`` write random values into
``.env``; an install started by hand has to set them (Help: Installing, Secrets in .env).

The encryption key seals the MCP servers' stored credentials. An install that has stored them under the
published key reads them only with it, so it may go on with it once the operator says so
(FADEN_ALLOW_PUBLISHED_KEYS), with a warning at every start. The token seals nothing; an empty one let every
caller in, so it is refused too.
"""

from __future__ import annotations

import logging

from faden_mcp.settings import Settings

log = logging.getLogger(__name__)

PUBLISHED_ENCRYPTION_KEYS = frozenset({"dev-mcp-encryption-key-change-me-32b"})
PUBLISHED_TOKENS = frozenset({"dev-mcp-service-token", "dev-mcp-service-token-change-me"})

_TOKEN_NAMES = "FADEN_GATEWAY_MCP_SERVICE_TOKEN, FADEN_BACKEND_MCP_SERVICE_TOKEN and FADEN_MCP_SERVICE_TOKEN"

TOKEN_REFUSED = (
    "FADEN_MCP_SERVICE_TOKEN is the development default published with Fadenstack's source code: anyone could "
    f"call the MCP service as the gateway and the backend do. Set {_TOKEN_NAMES} in .env to one long random "
    "value (openssl rand -base64 32) and start again. Nothing is stored with it, so a new value loses nothing."
)
TOKEN_EMPTY = (
    "FADEN_MCP_SERVICE_TOKEN is empty: the MCP service would answer every caller without a token. "
    f"Set {_TOKEN_NAMES} in .env to one long random value (openssl rand -base64 32) and start again."
)
ENCRYPTION_KEY_REFUSED = (
    "FADEN_MCP_ENCRYPTION_KEY is the development default published with Fadenstack's source code: anyone could "
    "read the MCP server credentials stored with it.\n"
    "  - MCP has stored nothing yet: set it in .env to a long random value (openssl rand -base64 48) and start "
    "again. faden deploy and faden module enable mcp write one.\n"
    "  - MCP has stored credentials under it, and no other key reads them. To keep them, leave the key as it is, "
    "set FADEN_ALLOW_PUBLISHED_KEYS=true in .env, and change the key later with `faden secrets rotate` (Help: "
    "Rotating the keys)."
)
ROTATION_UNFINISHED = (
    "A key rotation is unfinished: the previous encryption key is still set, and values sealed with it "
    "still open. Run `faden secrets rotate` again to finish it."
)
ENCRYPTION_KEY_KEPT = (
    "This install keeps MCP credentials under the published development encryption key "
    "(FADEN_ALLOW_PUBLISHED_KEYS=true). Change FADEN_MCP_ENCRYPTION_KEY with `faden secrets rotate` "
    "(Help: Rotating the keys)."
)


def problems(settings: Settings) -> list[str]:
    """What keeps the MCP service from starting with *settings*; empty in the dev environment."""
    if settings.environment == "dev":
        return []
    found: list[str] = []
    if not settings.service_token:
        found.append(TOKEN_EMPTY)
    elif settings.service_token in PUBLISHED_TOKENS:
        found.append(TOKEN_REFUSED)
    if settings.encryption_key in PUBLISHED_ENCRYPTION_KEYS and not settings.allow_published_keys:
        found.append(ENCRYPTION_KEY_REFUSED)
    return found


def refuse_published_secrets(settings: Settings) -> None:
    """Stop the process when *settings* hold a published secret; warn when the key is kept on purpose."""
    found = problems(settings)
    if found:
        raise SystemExit("The MCP service does not start:\n" + "\n".join(f"* {problem}" for problem in found))
    if settings.environment != "dev" and settings.encryption_key in PUBLISHED_ENCRYPTION_KEYS:
        log.warning(ENCRYPTION_KEY_KEPT)
    if settings.encryption_key_previous:
        log.warning(ROTATION_UNFINISHED)
