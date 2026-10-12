from __future__ import annotations

import asyncpg
from loguru import logger

from faden_gateway.db.crypto import settings_fernet
from faden_gateway.settings import settings

_JWT_SECRET_KEYS: tuple[str, ...] = (
    "faden_gateway.jwt_secret",
    "faden_backend.users_secret",
)


async def load_jwt_secret_from_backend_db() -> bool:
    """Load JWT secret from backend system settings DB.

    Preferred key is ``faden_gateway.jwt_secret``; if absent/empty, fallback to
    ``faden_backend.users_secret`` to keep signing and verification aligned.
    """
    if not settings.backend_db_base or not settings.settings_master_key:
        return False

    dsn = (
        f"postgresql://{settings.db_user}:{settings.db_pass}"
        f"@{settings.db_host}:{settings.db_port}/{settings.backend_db_base}"
    )
    try:
        conn = await asyncpg.connect(dsn)
    except Exception as exc:  # noqa: BLE001
        logger.warning("JWT secret: could not connect to backend DB - {}", exc)
        return False

    try:
        rows = await conn.fetch(
            "SELECT key, ciphertext FROM system_setting_secret WHERE key = ANY($1::text[])",
            list(_JWT_SECRET_KEYS),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("JWT secret: query failed - {}", exc)
        return False
    finally:
        await conn.close()

    ciphertext_by_key = {str(row["key"]): str(row["ciphertext"]) for row in rows}
    if not ciphertext_by_key:
        logger.info("JWT secret: no JWT secret keys found in backend DB, using env var fallback.")
        return False

    fernet = settings_fernet()

    for db_key in _JWT_SECRET_KEYS:
        ciphertext = ciphertext_by_key.get(db_key, "")
        if not ciphertext:
            continue
        try:
            secret = fernet.decrypt(ciphertext.encode()).decode().strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("JWT secret: decryption failed for {} - {}", db_key, exc)
            continue
        if not secret:
            logger.warning("JWT secret: key '{}' decrypted to empty value.", db_key)
            continue

        settings.jwt_secret = secret
        if db_key == "faden_gateway.jwt_secret":
            logger.info("JWT secret loaded from backend DB (faden_gateway.jwt_secret).")
        else:
            logger.warning(
                "JWT secret loaded from backend fallback key '{}'. "
                "Set faden_gateway.jwt_secret explicitly to avoid drift.",
                db_key,
            )
        return True

    logger.info("JWT secret: backend DB keys exist but no usable secret was found.")
    return False
