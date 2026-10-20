"""Redact mode must never send the original arguments."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from faden_mcp.services.privacy_proxy import PrivacyProxy


def _proxy(sanitized: str) -> PrivacyProxy:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "entities_found": 1,
                "pii_report": [{"entity_type": "PERSON", "start": 0, "end": 12, "text": "Anna Schmidt", "score": 1.0}],
                "contract_version": 2, "coverage_status": "complete", "profile_fingerprint": "test-profile",
                "sanitized_payload": {"messages": [{"tool_calls": [{"function": {"arguments": json.dumps({"to": sanitized})}}]}]},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return PrivacyProxy(pii_base_url="http://pii", http_client=client)


ARGS: dict[str, Any] = {"to": "Anna Schmidt"}


@pytest.mark.asyncio
async def test_redacted_json_is_sent() -> None:
    proxy = _proxy("<PERSON>")
    decision = await proxy.check(arguments=ARGS, pii_mode="redact")
    assert decision.allowed
    assert decision.sanitized_args == {"to": "<PERSON>"}


@pytest.mark.asyncio
@pytest.mark.parametrize("sanitized", [None, [], [7], ["<PERSON>", "extra"], {"to": "<PERSON>"}])
async def test_unparseable_redaction_is_refused(sanitized: str) -> None:
    proxy = _proxy(sanitized)
    decision = await proxy.check(arguments=ARGS, pii_mode="redact")
    assert not decision.allowed
    assert decision.sanitized_args == {}
    assert decision.redaction_summary["reason"] == "check_unavailable"
