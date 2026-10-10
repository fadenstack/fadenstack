"""Outside development the skills service does not start on a published or empty token."""

from __future__ import annotations

import pytest

from faden_skills.published_secrets import problems, refuse_published_secrets
from faden_skills.settings import Settings


def _settings(**values: object) -> Settings:
    base: dict[str, object] = {"environment": "production", "service_token": "a-token-of-this-install-0123456789"}
    return Settings(**{**base, **values})


def test_an_install_with_its_own_token_starts() -> None:
    assert problems(_settings()) == []


@pytest.mark.parametrize("token", ["", "dev-skills-service-token", "dev-skills-service-token-change-me"])
def test_an_empty_or_published_token_is_refused(token: str) -> None:
    with pytest.raises(SystemExit) as stopped:
        refuse_published_secrets(_settings(service_token=token))
    assert "FADEN_SKILLS_SERVICE_TOKEN" in str(stopped.value)


def test_development_keeps_its_defaults() -> None:
    assert problems(_settings(environment="dev", service_token="")) == []
