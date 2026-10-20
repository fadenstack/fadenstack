"""Null uses the default while zero is an explicit threshold."""

from types import SimpleNamespace

import pytest
from presidio_anonymizer import AnonymizerEngine

from faden_pii.services.pii.service import PIIService


@pytest.mark.anyio
@pytest.mark.parametrize(
    "threshold,expected", [(None, 0.35), (0.0, 0.0), (0.35, 0.35), (0.6, 0.6)]
)
@pytest.mark.parametrize("operation", ["scan", "redact", "sanitize_payload"])
async def test_effective_threshold(threshold, expected, operation):
    service = PIIService(SimpleNamespace(), AnonymizerEngine())
    seen = []
    service._batch.analyze_iterator = lambda *, texts, **kw: (
        seen.append(kw["score_threshold"]) or [[] for t in texts]
    )
    value = {"input": "clean"} if operation == "sanitize_payload" else "clean"
    await getattr(service, operation)(value, score_threshold=threshold)
    assert seen == [expected]
