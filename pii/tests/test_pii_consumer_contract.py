"""Real consumer clients call the real Core handler rather than fabricated success shapes."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from presidio_analyzer import RecognizerResult
from presidio_anonymizer import AnonymizerEngine

from faden_pii.services.pii.errors import PIIError
from faden_pii.services.pii.profiles import Profile
from faden_pii.services.pii.service import DEFAULT_ENTITIES, PIIService
from faden_pii.services.pii.windowing import Window
from faden_pii.web.api.pii import views

ROOT = Path(__file__).resolve().parents[2]


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



# ── The default policy every install starts with, against each kind of detector ───────
#
# Until 31 October 2026 this ran against the Presidio profile only: the default named
# NRP and a threshold on Presidio's scale, which a local detector refuses, so every
# request to a remote model was refused on installs with a local detector (the CPU and
# GPU choices of `faden deploy`).

#: What a local detector finds: Presidio's pattern rules and the model's PERSON and
#: LOCATION; not NRP or DATE_TIME, which only spaCy's model tags.
LOCAL_ENTITIES = sorted(
    {
        "CREDIT_CARD",
        "EMAIL_ADDRESS",
        "IBAN_CODE",
        "IP_ADDRESS",
        "LOCATION",
        "MEDICAL_LICENSE",
        "PERSON",
        "PHONE_NUMBER",
        "URL",
        "US_SSN",
    },
)


def _anna(text):
    start = text.index("Anna")
    return RecognizerResult("PERSON", start, start + 4, 0.9)


class LocalDetector:
    """A local detector as the service and runtime use it; finds "Anna" as a PERSON."""

    profile = Profile(backend="gliner-onnx", languages=("en",))
    entities = LOCAL_ENTITIES
    entity_languages = {"en": LOCAL_ENTITIES}

    def __init__(self):
        self.asked = []

    def prepare(self, text, options):
        return [], [Window(0, text, 1)]

    def predict(self, pieces, options):
        self.asked.append(sorted(options["entities"]))
        wanted = "PERSON" in options["entities"]
        return [[_anna(p.text)] if wanted and "Anna" in p.text else [] for p in pieces]

    def close(self):
        pass


def _backend_defaults():
    """The backend's module of default policies, loaded on its own (no package)."""
    path = ROOT / "backend" / "faden_backend" / "services" / "pii_default_policy.py"
    spec = importlib.util.spec_from_file_location("backend_pii_default_policy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _refusal(request, error):
    """As the service's own handler answers (web/application.py)."""
    return JSONResponse({"detail": {"code": error.code}}, status_code=error.status)


async def _gateway_sanitize(monkeypatch, service, policy_json):
    """The gateway's client and parsed policy against *service*'s real handler."""
    monkeypatch.syspath_prepend(str(ROOT / "gateway"))
    from faden_gateway.services.gateway.pii_client import PIIClient
    from faden_gateway.services.gateway.pii_policy import parse_pii_policy

    async def no_events(**kwargs):
        pass

    monkeypatch.setattr(views, "_log_event_async", no_events)
    app = FastAPI()
    app.state.pii_service = service
    app.include_router(views.router, prefix="/api/v1/pii")
    app.add_exception_handler(PIIError, _refusal)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://pii") as http:
        client = PIIClient(base_url="http://pii/api", http_client=http)
        return await client.sanitize(
            payload={
                "model": "remote",
                "messages": [{"role": "user", "content": "I am Anna"}],
            },
            policy=parse_pii_policy(policy_json),
        )


@pytest.mark.anyio
async def test_the_default_policy_reaches_a_local_detector(monkeypatch):
    detector = LocalDetector()
    service = PIIService(SimpleNamespace(), AnonymizerEngine(), detector=detector)
    policy = _backend_defaults().default_policy()
    try:
        result = await _gateway_sanitize(monkeypatch, service, policy)
    finally:
        await service.close()
    assert result.pii_detected
    assert "Anna" not in str(result.sanitized_payload)
    # The standard types the detector finds: NRP left out, as it does not find it;
    # never DATE_TIME.
    standard = [e for e in sorted(DEFAULT_ENTITIES) if e in LOCAL_ENTITIES]
    assert detector.asked == [standard]
    assert "NRP" in DEFAULT_ENTITIES and "DATE_TIME" not in DEFAULT_ENTITIES


@pytest.mark.anyio
async def test_the_default_policy_keeps_the_presidio_engines_coverage(monkeypatch):
    """On the older engine: the eleven types the earlier default named, NRP too."""
    service = PIIService(SimpleNamespace(), AnonymizerEngine())
    asked = []

    def analyze(*, texts, **kw):
        asked.append(sorted(kw["entities"]))
        return [[_anna(t)] if "Anna" in t else [] for t in texts]

    service._batch.analyze_iterator = analyze
    defaults = _backend_defaults()
    result = await _gateway_sanitize(monkeypatch, service, defaults.default_policy())
    assert result.pii_detected
    assert asked == [sorted(defaults.EARLIER_DEFAULTS[1]["presidio"]["entities"])]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("adopted", "code"),
    [(False, "pii_profile_required"), (True, "pii_unsupported_profile")],
    ids=["as-seeded", "profile-adopted-nrp-kept"],
)
async def test_the_earlier_default_is_refused_by_a_local_detector(
    monkeypatch,
    adopted,
    code,
):
    """Why it is replaced at start: its threshold, then NRP, are refused."""
    policy = _backend_defaults().EARLIER_DEFAULTS[1]
    if adopted:
        policy["presidio"] |= {
            "profile_id": "patronus-small-pii",
            "threshold_mode": "profile",
        }
    detector = LocalDetector()
    service = PIIService(SimpleNamespace(), AnonymizerEngine(), detector=detector)
    monkeypatch.syspath_prepend(str(ROOT / "gateway"))
    from faden_gateway.services.gateway.pii_client import PIIPolicyMismatchError

    try:
        with pytest.raises(PIIPolicyMismatchError) as refused:
            await _gateway_sanitize(monkeypatch, service, policy)
    finally:
        await service.close()
    assert refused.value.code == code
