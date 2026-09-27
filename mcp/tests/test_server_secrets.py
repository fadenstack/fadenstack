"""A server's headers and environment: encrypted, edited without reading back.

They carry bearer tokens and API keys. The columns were named ``*_encrypted``
and were plain text: the encrypted column type existed and nothing used it.
The API returned no trace of them either, so the console could not say that
a token was set, and an update replaced them whole.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from llm_port_mcp.db import crypto
from llm_port_mcp.db.crypto import EncryptedJSON
from llm_port_mcp.db.models.mcp import MCPServerModel
from llm_port_mcp.web.api.admin.views import _merged, _server_to_response

TOKEN = {"Authorization": "Bearer t0ken"}


@pytest.fixture
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure an install key for the test; the global is restored after."""
    monkeypatch.setattr(crypto, "_key_provider", None)
    crypto.configure("a key of this install's own")


def test_headers_and_env_are_encrypted_columns() -> None:
    """Both columns use the encrypted type."""
    for column in ("headers_json_encrypted", "env_json_encrypted"):
        assert isinstance(MCPServerModel.__table__.c[column].type, EncryptedJSON)


@pytest.mark.usefixtures("key")
def test_a_token_is_stored_as_ciphertext_and_read_back() -> None:
    """What reaches the database is ciphertext; what comes back is the dict."""
    column = EncryptedJSON("mcp-server-headers")
    stored = column.process_bind_param(TOKEN, None)
    assert stored is not None
    assert stored.startswith("gAAAAA")
    assert "t0ken" not in stored
    assert column.process_result_value(stored, None) == TOKEN


@pytest.mark.usefixtures("key")
def test_what_was_stored_in_plain_text_still_reads() -> None:
    """Rows written before the columns were encrypted."""
    column = EncryptedJSON("mcp-server-headers")
    legacy = '{"Authorization":"Bearer t0ken"}'
    assert column.process_result_value(legacy, None) == TOKEN


def test_an_update_sets_removes_and_keeps() -> None:
    """A value sets, null removes, a key left out stays."""
    stored = {"Authorization": "Bearer old", "X-Tenant": "a"}
    assert _merged(stored, {"Authorization": "Bearer new"}) == {
        "Authorization": "Bearer new",
        "X-Tenant": "a",
    }
    assert _merged(stored, {"X-Tenant": None}) == {"Authorization": "Bearer old"}
    assert _merged(stored, {"Authorization": None, "X-Tenant": None}) is None
    assert _merged(None, TOKEN) == TOKEN


def test_the_api_names_what_is_set_and_never_the_value() -> None:
    """Header names and the last error go out; the token does not."""
    now = datetime.now(timezone.utc)
    server = SimpleNamespace(
        id="s1",
        name="github",
        transport="streamable_http",
        status="error",
        url="https://api.githubcopilot.com/mcp",
        command_json=None,
        args_json=None,
        working_dir=None,
        tool_prefix="gh",
        pii_mode="allow",
        enabled=True,
        timeout_sec=60,
        heartbeat_interval_sec=30,
        tenant_id="default",
        headers_json_encrypted=TOKEN,
        env_json_encrypted=None,
        settings_schema_json=None,
        created_at=now,
        updated_at=now,
        last_discovery_at=None,
        last_error="answered 401 Unauthorized",
        tools=[],
    )
    response = _server_to_response(server)
    assert response.header_names == ["Authorization"]
    assert response.env_names == []
    assert response.last_error == "answered 401 Unauthorized"
    assert "t0ken" not in response.model_dump_json()
