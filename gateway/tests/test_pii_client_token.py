"""The gateway presents the PII service token on its calls."""

from __future__ import annotations

import httpx
import json
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
        return httpx.Response(200, json={"sanitized_payload": {"messages": []}, "entities_found": 0, "pii_report": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = PIIClient(base_url="http://pii.local", http_client=http, service_token=token)
        policy = parse_pii_policy(_policy())
        assert policy is not None
        await client.sanitize(payload={"messages": []}, policy=policy)

    assert seen[0].headers.get("authorization") == header
    assert seen[0].headers["x-pii-source"] == "gateway"


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["v1", "changed_type", "invalid_json", "empty_fingerprint"])
async def test_tool_arguments_require_complete_preserved_v2_contract(failure):
    def payload(arguments):
        return {"messages": [{"role": "assistant", "tool_calls": [{
            "type": "function", "function": {"name": "contact", "arguments": arguments},
        }]}]}

    original = payload(json.dumps({"first_name": "Anna", "n": 7}))
    arguments = json.dumps({"first_name": "<PERSON>", "n": "7" if failure == "changed_type" else 7})
    data = {"sanitized_payload": payload("broken" if failure == "invalid_json" else arguments),
            "entities_found": 0, "pii_report": [], "contract_version": 1 if failure == "v1" else 2,
            "coverage_status": "complete", "profile_fingerprint": "" if failure == "empty_fingerprint" else "test"}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))) as http:
        policy = parse_pii_policy(_policy())
        with pytest.raises(ValueError):
            await PIIClient(base_url="http://pii", http_client=http).sanitize(payload=original, policy=policy)
