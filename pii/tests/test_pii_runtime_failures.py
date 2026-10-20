"""Bad batches and input deadlines must not strand unrelated requests."""

import asyncio
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from faden_pii.services.pii.detectors import NeuralDetector
from faden_pii.services.pii.errors import PIIError
from faden_pii.services.pii.extraction import ExtractedPayload
from faden_pii.services.pii.extraction import string_leaves
from faden_pii.services.pii.limits import IngressLimits
from faden_pii.services.pii.limits import validate_units
from faden_pii.services.pii.profiles import Profile
from faden_pii.services.pii.service import PIIService
from faden_pii.services.pii.runtime import InferenceRuntime
from faden_pii.services.pii.windowing import Window
from faden_pii.settings import Settings


@pytest.mark.parametrize(
    ("text", "value", "accepted"),
    [
        ("M", {"text": "M.", "start": 0, "end": 2}, True),
        ("Anna", {"text": "Anna", "start": 0, "end": 4}, True),
        ("Anna", {"text": "Annaa", "start": 0, "end": 5}, False),
        ("Anna", {"text": "Anne", "start": 0, "end": 4}, False),
        ("Anna", {"text": "Anna.", "start": 0, "end": 4}, False),
    ],
)
def test_model_offsets_allow_only_verified_punctuation_overhang(text, value, accepted):
    detector = object.__new__(NeuralDetector)
    detector.torch = SimpleNamespace(inference_mode=nullcontext)
    detector.settings = SimpleNamespace(pii_backend="gliner2")
    detector.labels = {"person": "PERSON"}
    detector.model = SimpleNamespace(
        batch_extract_entities=lambda *a, **kw: [
            {"entities": {"person": [{**value, "confidence": 0.9}]}}
        ]
    )
    if accepted:
        rows = detector.predict(
            [Window(0, text, 1)], {"entities": ["PERSON"], "score_threshold": 0.35}
        )
        assert rows[0][0].end == len(text)
    else:
        with pytest.raises(PIIError, match="pii_invalid_detector_result"):
            detector.predict(
                [Window(0, text, 1)], {"entities": ["PERSON"], "score_threshold": 0.35}
            )


@pytest.mark.anyio
async def test_malformed_model_batch_refuses_it_and_next_batch_still_runs():
    class Detector:
        profile = Profile(backend="gliner2")

        def prepare(self, text, options):
            return [], [Window(0, text, 1)]

        def predict(self, pieces, options):
            if pieces[0].text == "bad":
                raise PIIError("pii_invalid_detector_result", 503)
            return [[] for piece in pieces]

        def close(self):
            pass

    runtime = InferenceRuntime(Detector(), Settings())
    options = {"language": "en", "entities": ["PERSON"], "score_threshold": 0.35}
    try:
        with pytest.raises(PIIError, match="pii_invalid_detector_result"):
            await runtime.analyze(["bad"], options)
        assert runtime.healthy
        assert await runtime.analyze(["good"], options) == [[]]
        assert runtime.retained_bytes == runtime.pending_tokens == 0
    finally:
        await runtime.close()


def test_deep_structures_are_rejected_before_copying():
    value = {}
    for _ in range(66):
        value = {"next": value}
    with pytest.raises(PIIError, match="pii_input_too_deep"):
        ExtractedPayload(value)


def test_argument_extraction_is_bounded_before_building_all_leaves():
    with pytest.raises(PIIError, match="pii_input_too_large"):
        string_leaves({"values": ["x"] * 257})


def test_embedding_unit_count_is_checked_before_expanding_units():
    with pytest.raises(PIIError, match="pii_input_too_large"):
        ExtractedPayload({"input": [""] * 257})


@pytest.mark.anyio
async def test_slow_body_releases_admission_and_bytes():
    called, sent = [], []

    async def app(*args):
        called.append(True)

    async def receive():
        await asyncio.sleep(0.1)
        return {"type": "http.request", "body": b"{}"}

    async def send(message):
        sent.append(message)

    middleware = IngressLimits(app, Settings())
    await middleware(
        {"type": "http", "method": "POST", "headers": [(b"x-pii-budget-ms", b"1")]},
        receive,
        send,
    )
    assert sent[0]["status"] == 408
    assert not called
    assert middleware.requests == middleware.retained == 0


def test_mapping_must_not_replace_sensitive_text_with_an_arbitrary_plain_value():
    with pytest.raises(PIIError, match="pii_invalid_mapping"):
        validate_units([], {"Anna": "other-sensitive-value"}, Settings())


def test_disabled_cache_and_empty_optional_calibration_are_supported():
    settings = Settings(pii_cache_entries=0, pii_cache_bytes=0, pii_calibration_path="")
    assert settings.pii_cache_entries == settings.pii_cache_bytes == 0
    assert settings.pii_calibration_path is None


@pytest.mark.anyio
async def test_cached_history_survives_eviction_while_fresh_text_is_pending():
    service = PIIService(SimpleNamespace(), SimpleNamespace(), analysis_cache_size=1)
    options = {"language": "en", "entities": ["PERSON"], "score_threshold": 0.35}

    async def analyze(texts, options):
        # A concurrent request's completed analysis evicts the cached history.
        service._analyses.put(("another-request",), ())
        return [[] for _ in texts]

    service.runtime.analyze = analyze
    try:
        await service._analyze_many(["history"], **options)
        assert await service._analyze_many(["history", "new"], **options) == [[], []]
        assert len(service._analyses._entries) == 1
    finally:
        await service.close()
