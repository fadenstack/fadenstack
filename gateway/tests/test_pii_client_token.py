"""The gateway presents the PII service token on its calls."""

from __future__ import annotations

import httpx
import pytest

from faden_gateway.services.gateway.pii_client import PIIClient
from faden_gateway.services.gateway.pii_policy import parse_pii_policy


def _policy() -> dict:
    return {
        "egress": {"enabled_for_cloud": True, "mode": "redact", "fail_action": "block"},
        "presidio": {"language": "en", "threshold": 0.5, "entities": ["PERSON"]},
    }


@pytest.mark.anyio
@pytest.mark.parametrize(("token", "header"), [("secret-t", "Bearer secret-t"), ("", None)])
async def test_the_client_presents_the_service_token(token: str, header: str | None) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"sanitized_payload": {}, "entities_found": [], "pii_report": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = PIIClient(base_url="http://pii.local", http_client=http, service_token=token)
        policy = parse_pii_policy(_policy())
        assert policy is not None
        await client.sanitize(payload={"messages": []}, policy=policy)

    assert seen[0].headers.get("authorization") == header
    assert seen[0].headers["x-pii-source"] == "gateway"
