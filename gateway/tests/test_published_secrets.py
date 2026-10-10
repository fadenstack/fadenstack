"""Outside development the gateway does not start on a secret published with the source code."""

from __future__ import annotations

import logging

import pytest

from faden_gateway.published_secrets import problems, refuse_published_secrets
from faden_gateway.settings import Settings

PUBLISHED = "dev-settings-master-key-change-me"
OWN = "a-key-of-this-install-0123456789-abcdefghij"


def _settings(**values: object) -> Settings:
    base: dict[str, object] = {
        "environment": "production",
        "settings_master_key": OWN,
        "mcp_enabled": False,
        "skills_enabled": False,
        "allow_published_keys": False,
    }
    return Settings(**{**base, **values})


def test_an_install_with_its_own_secrets_starts() -> None:
    assert problems(_settings()) == []


def test_the_published_master_key_is_refused_with_what_to_do() -> None:
    with pytest.raises(SystemExit) as stopped:
        refuse_published_secrets(_settings(settings_master_key=PUBLISHED))
    assert "FADEN_BACKEND_SETTINGS_MASTER_KEY" in str(stopped.value)
    assert "FADEN_ALLOW_PUBLISHED_KEYS=true" in str(stopped.value)


def test_it_may_be_kept_on_purpose_with_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        refuse_published_secrets(_settings(settings_master_key=PUBLISHED, allow_published_keys=True))
    assert "published development settings master key" in caplog.text


def test_published_tokens_are_refused_for_the_modules_in_use() -> None:
    assert problems(_settings(mcp_enabled=True, mcp_service_token="dev-mcp-service-token-change-me"))
    assert problems(_settings(skills_enabled=True, skills_service_token="dev-skills-service-token-change-me"))
    assert problems(_settings(skills_enabled=False, skills_service_token="dev-skills-service-token")) == []


def test_development_keeps_its_defaults() -> None:
    assert problems(_settings(environment="dev", settings_master_key=PUBLISHED)) == []
