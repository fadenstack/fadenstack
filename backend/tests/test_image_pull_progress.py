"""A pull's progress stream that fails says so, not what the exception said.

The stream is for anyone with ``images:read`` (viewers too); the exception's
text -- the Docker endpoint, a registry's answer, a path -- goes to the log.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from faden_backend.db.models.users import User, current_active_user
from faden_backend.web.api.admin.images import views

pytestmark = pytest.mark.anyio

WHAT_DOCKER_SAID = "tcp://10.0.0.5:2375 refused the registry token s3cr3t"


async def test_a_failed_stream_says_so_and_logs_why(
    client: AsyncClient, fastapi_app: FastAPI, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    user = MagicMock(spec=User)
    user.id, user.is_active, user.is_superuser = uuid.uuid4(), True, True
    fastapi_app.dependency_overrides[current_active_user] = lambda: user

    async def subscribe(_job: Any) -> AsyncIterator[dict[str, Any]]:
        yield {"event": "progress", "data": {"status": "Downloading"}}
        raise RuntimeError(WHAT_DOCKER_SAID)

    monkeypatch.setattr(views.pull_tracker, "get_job", lambda _pull_id: SimpleNamespace(pull_id="p1"))
    monkeypatch.setattr(views.pull_tracker, "subscribe", subscribe)

    with caplog.at_level(logging.ERROR, logger=views.logger.name):
        resp = await client.get("/api/admin/images/pull/p1/progress")
    assert resp.status_code == 200
    assert "event: progress" in resp.text
    assert "event: error" in resp.text
    assert views._PROGRESS_FAILED in resp.text
    assert "s3cr3t" not in resp.text and "10.0.0.5" not in resp.text
    assert WHAT_DOCKER_SAID in caplog.text
