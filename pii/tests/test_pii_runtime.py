"""Resource bounds include abandoned native work and coalesced subscribers."""

import asyncio
from collections import Counter
import threading
import time

import pytest

from faden_pii.settings import Settings
from faden_pii.services.pii.errors import PIIError
from faden_pii.services.pii.profiles import Profile
from faden_pii.services.pii.runtime import InferenceRuntime
from faden_pii.services.pii.windowing import Window, windows

OPTIONS = {"language": "en", "entities": ["PERSON"], "score_threshold": 0.35}


class Detector:
    profile = Profile(backend="gliner2")

    def __init__(self, gate=None):
        self.stats = Counter()
        self.gate = gate

    def prepare(self, text, options):
        return [], [Window(0, text, 1)]

    def predict(self, pieces, options):
        self.stats["active"] += 1
        self.stats["max_active"] = max(self.stats["max_active"], self.stats["active"])
        self.stats["texts"] += len(pieces)
        self.stats["calls"] += 1
        if self.gate:
            assert self.gate.wait(3)
        self.stats["active"] -= 1
        return [[] for _ in pieces]

    def close(self):
        pass


@pytest.mark.anyio
async def test_64_identical_misses_have_one_forward():
    detector = Detector()
    runtime = InferenceRuntime(detector, Settings())
    try:
        results = await asyncio.gather(
            *(runtime.analyze(["Anna"], OPTIONS) for _ in range(64))
        )
        assert results == [[[]]] * 64
        assert detector.stats["texts"] == 1
        assert runtime.stats["singleflight_hits"] == 63
        assert runtime.retained_bytes == 0
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_compatible_languages_share_neural_batch():
    class MultilingualDetector(Detector):
        def batch_key(self, options):
            return tuple(options["entities"]), options["score_threshold"]

    detector = MultilingualDetector()
    runtime = InferenceRuntime(detector, Settings())
    try:
        results = await asyncio.gather(
            *(
                runtime.analyze([f"Anna {lang}"], {**OPTIONS, "language": lang})
                for lang in ("en", "de", "fr", "es")
            )
        )
        assert results == [[[]]] * 4
        assert detector.stats["texts"] == 4
        assert detector.stats["calls"] == 1
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_cancelled_client_retains_native_slot_and_budget():
    gate = threading.Event()
    detector = Detector(gate)
    runtime = InferenceRuntime(detector, Settings(pii_pending_windows=1))
    task = asyncio.create_task(runtime.analyze(["Anna"], OPTIONS))
    try:
        for _ in range(100):
            if detector.stats["active"]:
                break
            await asyncio.sleep(0.01)
        assert detector.stats["active"] == 1
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert runtime.retained_bytes > 0
        with pytest.raises(PIIError, match="pii_overloaded"):
            await runtime.analyze(["different"], OPTIONS)
        assert detector.stats["max_active"] == 1
        gate.set()
        for _ in range(100):
            if runtime.retained_bytes == 0:
                break
            await asyncio.sleep(0.01)
        assert runtime.retained_bytes == 0
    finally:
        gate.set()
        await runtime.close()


@pytest.mark.anyio
async def test_one_cancelled_subscriber_does_not_cancel_another():
    gate = threading.Event()
    runtime = InferenceRuntime(Detector(gate), Settings())
    first = asyncio.create_task(runtime.analyze(["same"], OPTIONS))
    second = asyncio.create_task(runtime.analyze(["same"], OPTIONS))
    await asyncio.sleep(0.05)
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)
    gate.set()
    try:
        assert await second == [[]]
        assert runtime.detector.stats["texts"] == 1
    finally:
        await runtime.close()


def test_complete_windows_and_cross_boundary_source_coverage():
    text = "😀 " + "word " * 100 + "Jörg Müller"
    pieces = windows(text, len, max_tokens=64)
    coverage = set()
    for piece in pieces:
        assert piece.tokens <= 64
        assert text[piece.offset : piece.offset + len(piece.text)] == piece.text
        coverage.update(range(piece.offset, piece.offset + len(piece.text)))
    assert len(coverage) == len(text)
    assert pieces[-1].text.endswith("Jörg Müller")


def _covered(text, pieces, max_tokens):
    coverage = set()
    for piece in pieces:
        assert piece.tokens <= max_tokens
        assert text[piece.offset : piece.offset + len(piece.text)] == piece.text
        coverage.update(range(piece.offset, piece.offset + len(piece.text)))
    return coverage == set(range(len(text)))


def test_one_oversized_word_is_cut_into_windows_not_truncated():
    """A run without whitespace longer than a window is cut, not refused.

    Found 17 Nov 2026: compact JSON and long addresses were refused with 413.
    """
    text = "x" * 700 + " end"
    pieces = windows(text, len, max_tokens=256)
    assert _covered(text, pieces, 256)
    assert len(pieces) >= 3 and pieces[-1].text.endswith("end")


def test_a_long_address_in_a_sentence_is_covered_with_the_labels_counted():
    text = (
        "Please read https://cloud.example.org/"
        + "a1b2c3d4/" * 120
        + " and tell Jörg Müller."
    )
    pieces = windows(text, lambda value: len(value) + 40, max_tokens=256)
    assert _covered(text, pieces, 256)
    assert any("Jörg Müller" in piece.text for piece in pieces)


def test_a_character_that_fits_no_window_is_refused():
    with pytest.raises(PIIError, match="pii_input_too_large"):
        windows("ab", lambda value: len(value) + 300, max_tokens=256)


def test_a_text_needing_too_many_windows_is_still_refused():
    with pytest.raises(PIIError, match="pii_input_too_large"):
        windows("x" * 20_000, len, max_tokens=256, max_windows=64)


@pytest.mark.parametrize(
    "settings",
    [
        {"pii_batch_short": 0},
        {"pii_window_tokens": 1024},
        {"pii_backend": "gliner2", "workers_count": 2},
    ],
)
def test_invalid_runtime_settings_fail_startup(settings):
    with pytest.raises(ValueError):
        Settings(**settings)
