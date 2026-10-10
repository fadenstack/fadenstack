"""Outside development the MCP service does not start on a published secret, nor without a token.

The compose file fell back to a fixed token and a fixed encryption key, both in the public repository; an
empty token let every caller in.
"""

from __future__ import annotations

import logging

import pytest

from faden_mcp.published_secrets import problems, refuse_published_secrets
from faden_mcp.settings import Settings

PUBLISHED_KEY = "dev-mcp-encryption-key-change-me-32b"


def _settings(**values: object) -> Settings:
    base: dict[str, object] = {
        "environment": "production",
        "service_token": "a-token-of-this-install-0123456789",
        "encryption_key": "a-key-of-this-install-0123456789",
        "allow_published_keys": False,
    }
    return Settings(**{**base, **values})


def test_an_install_with_its_own_secrets_starts() -> None:
    assert problems(_settings()) == []


@pytest.mark.parametrize("token", ["", "dev-mcp-service-token", "dev-mcp-service-token-change-me"])
def test_an_empty_or_published_token_is_refused(token: str) -> None:
    with pytest.raises(SystemExit) as stopped:
        refuse_published_secrets(_settings(service_token=token))
    assert "FADEN_MCP_SERVICE_TOKEN" in str(stopped.value)


def test_the_published_encryption_key_is_refused_with_what_to_do() -> None:
    with pytest.raises(SystemExit) as stopped:
        refuse_published_secrets(_settings(encryption_key=PUBLISHED_KEY))
    assert "FADEN_MCP_ENCRYPTION_KEY" in str(stopped.value)
    assert "FADEN_ALLOW_PUBLISHED_KEYS=true" in str(stopped.value)


def test_stored_credentials_may_stay_under_it_with_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        refuse_published_secrets(_settings(encryption_key=PUBLISHED_KEY, allow_published_keys=True))
    assert "published development encryption key" in caplog.text


def test_development_keeps_its_defaults() -> None:
    assert problems(_settings(environment="dev", service_token="", encryption_key=PUBLISHED_KEY)) == []
