"""The keys that open content, as files compose mounts into the services, not as their environment.

``docker inspect`` shows a container's environment to anyone who may use Docker, and the console's container
pages showed it to administrators: the chat key there opened every chat. The compose file now mounts these
keys as files under ``/run/secrets`` (its ``secrets:``), where the services read them (pydantic-settings
``secrets_dir``). ``.env`` stays where they are kept and changed: every compose command the CLI runs writes
the files from it first (:meth:`ComposeContext.base_cmd`), so a key rotated, restored or set by hand reaches the
services the next time they start. The provenance engine's signing key and hash keys go the same way.

The folder is the install user's only (0700); the files in it are 0644, so a service that does not run as
root can read the one it is given, while nobody else on the server reaches them. Each is rewritten in place:
a mounted file keeps showing the file it was mounted from.
"""

from __future__ import annotations

from pathlib import Path

from faden_cli.core.env_gen import read_env_file
from faden_cli.core.private_files import private_dir, write_private

FOLDER = "secrets"
#: The files, by the .env key each is written from; compose's ``secrets:`` names them.
SECRET_FILES: tuple[str, ...] = (
    "FADEN_GATEWAY_ENCRYPTION_KEY",
    "FADEN_GATEWAY_ENCRYPTION_KEY_PREVIOUS",
    "FADEN_BACKEND_SETTINGS_MASTER_KEY",
    "FADEN_BACKEND_SETTINGS_MASTER_KEY_PREVIOUS",
    "FADEN_BACKEND_CREDENTIAL_PEPPER",
    "FADEN_MCP_ENCRYPTION_KEY",
    "FADEN_MCP_ENCRYPTION_KEY_PREVIOUS",
    # The provenance engine's signer and hash keys (core/provenance_keys.py). Not required: only
    # faden-provenance reads them, and the module can be switched off; an empty file is what it gets then.
    "FADEN_PROVENANCE_SIGNING_KEY",
    "FADEN_PROVENANCE_SIGNING_CERTS",
    "FADEN_PROVENANCE_HASH_KEYS",
)
#: Never empty when the services start (compose refused to start without them while they were environment).
REQUIRED: frozenset[str] = frozenset({
    "FADEN_GATEWAY_ENCRYPTION_KEY",
    "FADEN_BACKEND_SETTINGS_MASTER_KEY",
    "FADEN_BACKEND_CREDENTIAL_PEPPER",
    "FADEN_MCP_ENCRYPTION_KEY",
})
_READABLE = 0o644


class SecretFileError(RuntimeError):
    """A key's file cannot be written."""


def uses_secret_files(compose_files: list[Path]) -> bool:
    """Whether the compose files mount the keys as files (an older compose file passes them as environment)."""
    for path in compose_files:
        try:
            if "./secrets/FADEN_GATEWAY_ENCRYPTION_KEY" in Path(path).read_text(encoding="utf-8"):
                return True
        except OSError:
            continue
    return False


def _clear_docker_folder(path: Path) -> None:
    """Remove the empty folder Docker makes where a mounted file was missing, so the file can be written."""
    try:
        path.rmdir()
    except OSError as exc:
        raise SecretFileError(
            f"{path} is a folder, made by Docker when the file was missing; remove it "
            f"(sudo rmdir {path}) and run the command again."
        ) from exc


def write(project_dir: Path, env_file: Path) -> list[str]:
    """Write every key's file from *env_file* into ``<project_dir>/secrets/``; the required keys .env lacks."""
    values = read_env_file(env_file) if env_file.is_file() else {}
    folder = private_dir(project_dir / FOLDER)
    for name in SECRET_FILES:
        path = folder / name
        if path.is_dir():
            _clear_docker_folder(path)
        data = values.get(name, "").encode("utf-8")
        try:
            current = path.read_bytes() if path.is_file() else None
        except OSError:
            current = None
        if current != data:
            write_private(path, data, mode=_READABLE)
    return sorted(name for name in REQUIRED if not values.get(name, "").strip())
