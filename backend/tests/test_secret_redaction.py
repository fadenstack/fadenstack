"""Secret values leave the backend hidden, and come back as they were."""

from __future__ import annotations

import pytest

from faden_backend.services.secret_redaction import (
    MASK,
    HiddenValueError,
    hidden_names,
    mask_cmd,
    mask_env,
    mask_env_text,
    mask_mapping,
    restore_cmd,
    restore_env,
    restore_env_text,
    restore_mapping,
    secret_name,
)


@pytest.mark.parametrize(
    "name",
    [
        "FADEN_GATEWAY_ENCRYPTION_KEY", "FADEN_GATEWAY_ENCRYPTION_KEY_PREVIOUS", "POSTGRES_PASSWORD",
        "FADEN_BACKEND_DB_PASS", "REDISCLI_AUTH", "USERS_SECRET", "FADEN_MCP_SERVICE_TOKEN", "HF_TOKEN",
        "FADEN_BACKEND_CREDENTIAL_PEPPER", "SALT", "Authorization", "x-api-key", "api_key", "SENTRY_DSN",
        "requirepass",
    ],
)
def test_secret_names(name: str) -> None:
    assert secret_name(name)


@pytest.mark.parametrize("name", ["PORT", "FADEN_GATEWAY_HOST", "KEYBOARD", "MONKEY_COUNT", "LOG_LEVEL", "TZ"])
def test_plain_names(name: str) -> None:
    assert not secret_name(name)


def test_env_hides_secrets_and_url_passwords_but_not_switches() -> None:
    env = [
        "PORT=3001",
        "FADEN_GATEWAY_ENCRYPTION_KEY=abc123",
        "DATABASE_URL=postgresql://postgres:s3cret@postgres:5432/langfuse",
        "FADEN_ALLOW_PUBLISHED_KEYS=true",
        "GF_AUTH_ANONYMOUS_ENABLED=false",
        "EMPTY_TOKEN=",
        "NOVALUE",
    ]
    masked = mask_env(env)
    assert masked == [
        "PORT=3001",
        f"FADEN_GATEWAY_ENCRYPTION_KEY={MASK}",
        f"DATABASE_URL=postgresql://postgres:{MASK}@postgres:5432/langfuse",
        "FADEN_ALLOW_PUBLISHED_KEYS=true",
        "GF_AUTH_ANONYMOUS_ENABLED=false",
        "EMPTY_TOKEN=",
        "NOVALUE",
    ]
    assert "abc123" not in str(masked) and "s3cret" not in str(masked)
    assert hidden_names(env) == ["FADEN_GATEWAY_ENCRYPTION_KEY", "DATABASE_URL"]


def test_env_round_trip_keeps_stored_values_and_takes_new_ones() -> None:
    stored = ["PORT=3001", "POSTGRES_PASSWORD=old-pass", "DATABASE_URL=postgresql://u:pw@h/db"]
    form = mask_env(stored)
    assert restore_env(form, stored) == stored
    # A value typed anew replaces the stored one; a removed row stays removed; a new row is taken.
    edited = [f"POSTGRES_PASSWORD={MASK}", "PORT=8080", "NEW=1", "API_TOKEN=fresh"]
    assert restore_env(edited, stored) == ["POSTGRES_PASSWORD=old-pass", "PORT=8080", "NEW=1", "API_TOKEN=fresh"]


def test_env_mask_for_a_new_name_is_refused() -> None:
    with pytest.raises(HiddenValueError):
        restore_env([f"OTHER_SECRET={MASK}"], ["POSTGRES_PASSWORD=x"])
    # A URL whose visible part was changed around the mask is refused too.
    with pytest.raises(HiddenValueError):
        restore_env([f"DATABASE_URL=postgresql://u:{MASK}@other/db"], ["DATABASE_URL=postgresql://u:pw@h/db"])


def test_cmd_hides_secret_flags() -> None:
    cmd = ["redis-server", "--appendonly", "yes", "--requirepass", "r3dis", "--password=pw", "--env=DB_PASSWORD=x"]
    masked = mask_cmd(cmd)
    assert masked == [
        "redis-server", "--appendonly", "yes", "--requirepass", MASK, f"--password={MASK}", f"--env=DB_PASSWORD={MASK}",
    ]
    assert restore_cmd(masked, cmd) == cmd
    assert mask_cmd(None) is None and restore_cmd(None, cmd) is None


def test_cmd_moved_mask_is_refused() -> None:
    cmd = ["redis-server", "--requirepass", "r3dis"]
    with pytest.raises(HiddenValueError):
        restore_cmd(["redis-server", "--verbose", "--requirepass", MASK], cmd)


def test_env_text_masks_and_restores_lines() -> None:
    stored = "# app\nPORT=80\nAPI_KEY=k1\nexport DB_PASSWORD=p1\n\nURL=https://u:pw@x/\n"
    masked = mask_env_text(stored)
    assert masked == f"# app\nPORT=80\nAPI_KEY={MASK}\nexport DB_PASSWORD={MASK}\n\nURL=https://u:{MASK}@x/\n"
    assert restore_env_text(masked, stored) == stored
    assert restore_env_text("PORT=81\n", stored) == "PORT=81\n"
    assert mask_env_text(None) is None


def test_mapping_masks_nested_headers() -> None:
    stored = {"extra_headers": {"Authorization": "Bearer t", "X-Region": "eu"}, "api_base": "https://x", "n": 3}
    masked = mask_mapping(stored)
    assert masked == {"extra_headers": {"Authorization": MASK, "X-Region": "eu"}, "api_base": "https://x", "n": 3}
    assert restore_mapping(masked, stored) == stored
    with pytest.raises(HiddenValueError):
        restore_mapping({"extra_headers": {"X-Api-Key": MASK}}, stored)
