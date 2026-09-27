"""Commands to a machine carry a signature its agent checks.

The key is HMAC-SHA256(credential, "command-signing") -- the credential the
agent opened the stream with, exactly as the agent derives it
(``llm_port_node_agent.command_verifier``). Nothing is stored: the credential
is on the connection for as long as it lasts, and the backend itself keeps
only a hash of it.

An agent that checks says so when it connects (:data:`HEADER`). The backend
then signs every command it sends on that stream and says so in ``hello_ack``
(``signs_commands``); from then on the agent refuses a command without a valid
signature, on this connection and every later one. Until then commands went
unsigned, and on plain HTTP anyone on the path could send a machine one.

Agents before 0.1.15 do not announce it: they changed their key mid-stream on
every credential rotation, so a signature would have broken them after twelve
hours. They get no signatures, as before.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

#: The header an agent that checks signatures connects with, and its value.
HEADER = "x-command-signing"
SCHEME = "hmac-sha256"


def derive_signing_key(credential: str) -> bytes:
    """The key an agent verifies commands with, from the credential it presented."""
    return hmac.new(credential.encode("utf-8"), b"command-signing", hashlib.sha256).digest()


def canonical(command: dict[str, Any]) -> bytes:
    """The bytes a signature covers: every field but the signature, keys sorted, compact JSON."""
    filtered = {k: v for k, v in sorted(command.items()) if k != "signature"}
    return json.dumps(filtered, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign(command: dict[str, Any], key: bytes) -> dict[str, Any]:
    """*command* with its ``signature``; anything added to it later breaks the signature."""
    unsigned = {k: v for k, v in command.items() if k != "signature"}
    return {**unsigned, "signature": hmac.new(key, canonical(unsigned), hashlib.sha256).hexdigest()}


def stream_key(headers: Any, authorization: str | None) -> bytes | None:
    """The signing key for a stream, when its agent checks signatures; ``None`` when it does not."""
    if (headers.get(HEADER) or "").strip().lower() != SCHEME:
        return None
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[len("Bearer ") :].strip()
    return derive_signing_key(token) if token else None


def sign_all(commands: list[dict[str, Any]], key: bytes | None) -> list[dict[str, Any]]:
    """Every command signed with *key*, or unchanged when the stream's agent does not check."""
    return [sign(command, key) for command in commands] if key else commands
