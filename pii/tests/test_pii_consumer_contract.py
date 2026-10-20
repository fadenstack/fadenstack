"""Real consumer clients call the real Core handler rather than fabricated success shapes."""

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from presidio_anonymizer import AnonymizerEngine
from types import SimpleNamespace
from presidio_analyzer import RecognizerResult

from faden_pii.services.pii.service import PIIService
from faden_pii.web.api.pii import views


@pytest.mark.anyio
async def test_core_mcp_and_gateway_contract(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root / "gateway"))
    monkeypatch.syspath_prepend(str(root / "mcp"))
    from faden_mcp.services.privacy_proxy import PrivacyProxy
    from faden_gateway.services.gateway.pii_client import PIIClient
    from faden_gateway.services.gateway.pii_policy import PIIPolicy

    service = PIIService(SimpleNamespace(), AnonymizerEngine())
    service._batch.analyze_iterator = lambda *, texts, **kw: [
        [RecognizerResult("PERSON", 0, len(t), 0.9)] if t == "Anna" else []
        for t in texts
    ]

    async def no_events(**kwargs):
        pass

    monkeypatch.setattr(views, "_log_event_async", no_events)
    app = FastAPI()
    app.state.pii_service = service
    app.include_router(views.router, prefix="/api/v1/pii")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://pii"
    ) as client:
        proxy = PrivacyProxy(pii_base_url="http://pii", http_client=client)
        assert (
            await proxy.check(arguments={"value": "clean"}, pii_mode="block")
        ).allowed
        assert not (
            await proxy.check(arguments={"value": "Anna"}, pii_mode="block")
        ).allowed
        result = await proxy.check(
            arguments={"value": "Anna", "n": 7}, pii_mode="redact"
        )
        assert result.sanitized_args == {"value": "<PERSON>", "n": 7}
        # The declared-field fallback must survive the real MCP-to-Core request.
        for name in ("Jörg", "Jo\u0308rg"):
            hinted = await proxy.check(
                arguments={"first_name": name, "n": 7}, pii_mode="redact"
            )
            assert hinted.sanitized_args == {"first_name": "<PERSON>", "n": 7}
        gateway = PIIClient(base_url="http://pii/api", http_client=client)
        result = await gateway.sanitize(payload={"input": "Anna"}, policy=PIIPolicy())
        assert result.pii_detected and result.entities_found == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "data",
    [
        {},
        {"entities_found": 0},
        {"sanitized_payload": {}, "entities_found": False, "pii_report": []},
        {"sanitized_payload": {}, "entities_found": -1, "pii_report": []},
        {"sanitized_payload": {}, "entities_found": "0", "pii_report": []},
    ],
)
async def test_gateway_rejects_invalid_success(monkeypatch, data):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "gateway"))
    from faden_gateway.services.gateway.pii_client import PIIClient
    from faden_gateway.services.gateway.pii_policy import PIIPolicy

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=data))
    ) as http:
        with pytest.raises(ValueError):
            await PIIClient(base_url="http://pii", http_client=http).sanitize(
                payload={"input": "Anna"}, policy=PIIPolicy()
            )
