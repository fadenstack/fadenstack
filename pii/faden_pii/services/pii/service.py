"""Presidio-based PII detection and redaction service.

Wraps ``presidio-analyzer`` and ``presidio-anonymizer`` with a thin async
interface.  The heavy NLP model loading happens once at startup (via
``PIIService.create()``) so individual requests are fast.

Supports PII **redaction** — replacing detected entities with
placeholder tags (e.g. ``<PERSON>``) — and **tokenization** — replacing
detected entities with reversible surrogate tokens (e.g. ``[PERSON_1]``)
that preserve semantic meaning for the LLM while hiding real values.
"""

from __future__ import annotations

import hashlib
import logging
import re
import sys
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from presidio_analyzer import AnalyzerEngine, BatchAnalyzerEngine, RecognizerResult
from presidio_anonymizer import AnonymizerEngine

from faden_pii.services.pii.extraction import ExtractedPayload, name_value
from faden_pii.services.pii.spans import resolve_spans, validate_spans
from faden_pii.services.pii.errors import PIIError
from faden_pii.services.pii.limits import validate_units
from faden_pii.services.pii.runtime import (
    InferenceRuntime,
    LegacyDetector,
    request_budget,
)
from faden_pii.settings import settings as default_settings

#: A value the SDK masked on the device before it left (``[[fs:PERSON_1]]``).
#: Already a token: detecting it again would mask the mask.
_LOCAL_TOKEN = re.compile(r"\[\[fs:[A-Z][A-Z0-9_]*_\d+\]\]")

log = logging.getLogger(__name__)

# Default PII entity types Presidio should look for.
DEFAULT_ENTITIES: list[str] = [
    "PERSON",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "CREDIT_CARD",
    "IBAN_CODE",
    "IP_ADDRESS",
    "US_SSN",
    "LOCATION",
    "DATE_TIME",
    "NRP",
    "MEDICAL_LICENSE",
    "URL",
]


@dataclass
class DetectedEntity:
    """A single PII entity detected in the text."""

    entity_type: str
    start: int
    end: int
    score: float
    text: str
    path: str | None = None


@dataclass
class ScanResult:
    """Result of a PII scan operation."""

    entities: list[DetectedEntity] = field(default_factory=list)

    @property
    def has_pii(self) -> bool:
        return len(self.entities) > 0


@dataclass
class RedactResult:
    """Result of a PII redaction operation."""

    original_text: str
    redacted_text: str
    entities_found: int


@dataclass
class SanitizeResult:
    """Result of sanitizing an OpenAI-shaped payload."""

    payload: dict[str, Any]
    pii_report: list[DetectedEntity]
    token_mapping: dict[str, str] | None
    entities_found: int


class PIIService:
    """Facade over Presidio analyzer + anonymizer engines."""

    def __init__(
        self,
        analyzer: AnalyzerEngine,
        anonymizer: AnonymizerEngine,
        *,
        default_language: str = "en",
        default_score_threshold: float = 0.35,
        analysis_cache_size: int = 20_000,
        config: Any = None,
        detector: Any = None,
    ) -> None:
        self._analyzer = analyzer
        self._batch = BatchAnalyzerEngine(analyzer_engine=analyzer)
        self._anonymizer = anonymizer
        self._default_language = default_language
        self._default_score_threshold = (
            detector.profile.threshold
            if detector is not None
            else default_score_threshold
        )
        self.config = config or default_settings
        self._analyses = _AnalysisCache(
            analysis_cache_size, self.config.pii_cache_bytes
        )
        self.runtime = InferenceRuntime(
            detector or LegacyDetector(lambda: self._batch, analyzer), self.config
        )
        self.profile = self.runtime.profile

    @property
    def supported_languages(self) -> list[str]:
        """Languages the loaded NLP models cover: the only ones a check can run in."""
        return sorted(self.profile.languages)

    @property
    def supported_entities(self) -> list[str]:
        if hasattr(self.runtime.detector, "entities"):
            return self.runtime.detector.entities
        return (
            sorted(self._analyzer.get_supported_entities())
            if hasattr(self._analyzer, "get_supported_entities")
            else DEFAULT_ENTITIES
        )

    def entities_for_language(self, language):
        return getattr(self.runtime.detector, "entity_languages", {}).get(
            language, self.supported_entities
        )

    def default_entities(self, language):
        supported = self.entities_for_language(language)
        if getattr(self.config, "pii_entity_profile", "standard") == "extended":
            # Match the common multilingual catalog, rather than silently enabling
            # country-specific English-only recognizers in other language policies.
            from faden_pii.services.pii.extended import EXTENDED_ENTITIES

            return sorted(
                set(DEFAULT_ENTITIES + list(EXTENDED_ENTITIES)) & set(supported)
            )
        return [entity for entity in DEFAULT_ENTITIES if entity in supported]

    def _on_reload(self, detector):
        self._analyzer = detector.analyzer
        self._batch = BatchAnalyzerEngine(analyzer_engine=detector.analyzer)
        self.profile = detector.profile
        self._default_score_threshold = detector.profile.threshold
        self._analyses = _AnalysisCache(
            self.config.pii_cache_entries, self.config.pii_cache_bytes
        )

    async def close(self):
        await self.runtime.close()

    def validate_profile(
        self, profile_id=None, threshold_mode="legacy", score_threshold=None
    ):
        if profile_id is not None and profile_id != self.profile.id:
            raise PIIError("pii_unsupported_profile")
        if threshold_mode not in ("legacy", "profile", "override"):
            raise PIIError("pii_unsupported_profile")
        if (
            self.profile.backend != "presidio"
            and score_threshold is not None
            and profile_id is None
        ):
            raise PIIError("pii_profile_required")
        return None if threshold_mode == "profile" else score_threshold

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------
    @classmethod
    def create(
        cls,
        *,
        default_language: str = "en",
        default_score_threshold: float = 0.35,
        config: Any = None,
    ) -> PIIService:
        """Create service instance; loads spaCy model (slow, do once)."""
        log.info("Initializing Presidio engines (loading spaCy model)...")
        config = config or default_settings
        detector = None
        if config.pii_backend != "presidio":
            if config.workers_count != 1:
                raise ValueError("Neural PII profiles require exactly one HTTP worker")
            from faden_pii.services.pii.detectors import NeuralDetector

            detector = NeuralDetector(config)
        if detector is None:
            import importlib.util

            if importlib.util.find_spec("en_core_web_lg") is None:
                raise ValueError(
                    "Local English NLP package is missing; provision en_core_web_lg before offline startup"
                )
        analyzer = detector.analyzer if detector is not None else AnalyzerEngine()
        anonymizer = AnonymizerEngine()
        service = cls(
            analyzer,
            anonymizer,
            default_language=default_language,
            default_score_threshold=default_score_threshold,
            analysis_cache_size=config.pii_cache_entries,
            config=config,
            detector=detector,
        )
        # The first analysis finishes setting up spaCy's pipeline: the first
        # chat after a start waited ~0.9 s for it. Pay it here, at startup.
        if detector is None:
            service._batch.analyze_iterator(
                texts=["Warm-up: John Smith lives in Berlin."],
                language=default_language,
            )
        else:
            service.runtime.detector_factory = lambda: NeuralDetector(config)
            service.runtime.on_reload = service._on_reload
        log.info("Presidio engines ready.")
        return service

    # ------------------------------------------------------------------
    # Public API -- raw text
    # ------------------------------------------------------------------

    async def scan(
        self,
        text: str,
        *,
        language: str | None = None,
        entities: list[str] | None = None,
        score_threshold: float | None = None,
    ) -> ScanResult:
        """Detect PII entities in *text*."""
        lang = language or self._default_language
        ents = entities or self.default_entities(lang)
        threshold = (
            self._default_score_threshold
            if score_threshold is None
            else score_threshold
        )

        (results,) = await self._analyze_many(
            [text],
            language=lang,
            entities=ents,
            score_threshold=threshold,
            entity_thresholds=self.profile.thresholds.get(lang)
            if score_threshold is None
            else None,
        )

        detected = [
            DetectedEntity(
                entity_type=r.entity_type,
                start=r.start,
                end=r.end,
                score=round(r.score, 4),
                text=text[r.start : r.end],
            )
            for r in results
        ]
        return ScanResult(entities=detected)

    async def redact(
        self,
        text: str,
        *,
        language: str | None = None,
        entities: list[str] | None = None,
        score_threshold: float | None = None,
    ) -> RedactResult:
        """Detect and redact PII entities in *text*."""
        lang = language or self._default_language
        ents = entities or self.default_entities(lang)
        threshold = (
            self._default_score_threshold
            if score_threshold is None
            else score_threshold
        )

        (results,) = await self._analyze_many(
            [text],
            language=lang,
            entities=ents,
            score_threshold=threshold,
            entity_thresholds=self.profile.thresholds.get(lang)
            if score_threshold is None
            else None,
        )

        redacted = _redact_spans(text, results)

        return RedactResult(
            original_text=text,
            redacted_text=redacted,
            entities_found=len(results),
        )

    # ------------------------------------------------------------------
    # Public API -- OpenAI-shaped payloads
    # ------------------------------------------------------------------

    async def sanitize_payload(
        self,
        payload: dict[str, Any],
        *,
        mode: str = "redact",
        language: str | None = None,
        entities: list[str] | None = None,
        score_threshold: float | None = None,
        token_mapping: dict[str, str] | None = None,
    ) -> SanitizeResult:
        """Sanitize all text-bearing fields in an OpenAI-shaped payload.

        Walks ``messages[].content`` (string or multimodal array) and the
        ``input`` field (for embeddings).  All other fields are forwarded
        unchanged.

        Modes
        -----
        ``redact``
            Replaces detected PII with entity-type tags
            (e.g. ``<PERSON>``).
        ``tokenize``
            Replaces detected PII with reversible surrogate tokens
            (e.g. ``[PERSON_1]``) and returns a ``token_mapping`` dict
            so the caller can restore originals after the LLM response.
        """
        if mode not in ("redact", "tokenize"):
            raise ValueError(
                f"Unsupported sanitize mode '{mode}'. "
                "Supported modes: 'redact', 'tokenize'.",
            )

        lang = language or self._default_language
        ents = entities or self.default_entities(lang)
        threshold = (
            self._default_score_threshold
            if score_threshold is None
            else score_threshold
        )
        extracted = ExtractedPayload(
            payload,
            declared_entities=set(ents)
            if getattr(self.config, "pii_entity_profile", "standard") == "extended"
            else None,
        )
        validate_units(
            [unit.text for unit in extracted.units], token_mapping, self.config
        )

        all_entities: list[DetectedEntity] = []

        # Every text of the payload analysed at once, in the order the walk
        # below visits them: what was seen before comes from the cache, the
        # rest goes through spaCy in one batch. Analysed one by one, each
        # message cost a spaCy run and a thread hop -- about 15 ms apiece, on
        # the whole chat history, again on every turn.
        analysis_rows = await self._analyze_many(
            [unit.text for unit in extracted.units],
            language=lang,
            entities=ents,
            score_threshold=threshold,
            entity_thresholds=self.profile.thresholds.get(lang)
            if score_threshold is None
            else None,
        )
        # Explicit schema/conversation context stays outside the model cache.
        # Only the target's offsets are masked; neighboring prompts are not copied.
        if "PERSON" in ents:
            for unit, results in zip(extracted.units, analysis_rows, strict=True):
                if unit.person_hint and name_value(unit.text):
                    results.append(RecognizerResult("PERSON", 0, len(unit.text), 1.0))
                    results[:] = resolve_spans(unit.text, results)
        for unit, results in zip(extracted.units, analysis_rows, strict=True):
            if unit.entity_hint in ents and unit.text.strip():
                results.append(
                    RecognizerResult(unit.entity_hint, 0, len(unit.text), 1.0)
                )
                results[:] = resolve_spans(unit.text, results)
        if token_mapping:
            for unit, results in zip(extracted.units, analysis_rows, strict=True):
                for token, value in token_mapping.items():
                    match = re.fullmatch(r"\[([A-Z][A-Z0-9_]*)_\d+\]", token)
                    if (
                        match is None
                        or not value
                        or len(value) < 2
                        or match[1] not in ents
                    ):
                        continue
                    for found in re.finditer(
                        r"(?<!\w)" + re.escape(value) + r"(?!\w)", unit.text
                    ):
                        results.append(
                            RecognizerResult(match[1], found.start(), found.end(), 1.0)
                        )
                results[:] = resolve_spans(unit.text, results)
        analyses = iter(analysis_rows)

        if mode == "tokenize":
            # Shared mutable state for building the token mapping.
            # Continued from an earlier call when given one: a value it has
            # keeps its token, and new tokens are numbered after its own.
            token_mapping = dict(token_mapping or {})
            value_to_token = {value: token for token, value in token_mapping.items()}
            token_counters = _counters(token_mapping)

            async def _tokenize_text(text: str) -> str:
                results = next(analyses)
                for r in results:
                    all_entities.append(
                        DetectedEntity(
                            entity_type=r.entity_type,
                            start=r.start,
                            end=r.end,
                            score=round(r.score, 4),
                            text=text[r.start : r.end],
                        ),
                    )
                if not results:
                    return text

                # Sort by start position descending so we can
                # replace from end to start without shifting offsets.
                sorted_results = sorted(
                    results,
                    key=lambda r: r.start,
                    reverse=True,
                )
                chars = list(text)
                for r in sorted_results:
                    original = text[r.start : r.end]
                    if original in value_to_token:
                        token = value_to_token[original]
                    else:
                        count = token_counters.get(r.entity_type, 0) + 1
                        token_counters[r.entity_type] = count
                        token = f"[{r.entity_type}_{count}]"
                        value_to_token[original] = token
                        token_mapping[token] = original
                    chars[r.start : r.end] = list(token)
                return "".join(chars)

            sanitize_fn = _tokenize_text
        else:
            token_mapping = None  # type: ignore[assignment]

            async def _redact_text(text: str) -> str:
                """Redact a single text string, from its analysis."""
                results = next(analyses)
                for r in results:
                    all_entities.append(
                        DetectedEntity(
                            entity_type=r.entity_type,
                            start=r.start,
                            end=r.end,
                            score=round(r.score, 4),
                            text=text[r.start : r.end],
                        ),
                    )
                if not results:
                    return text

                # Inline: replacing spans is cheap next to the analysis, and
                # a thread hop per message is what this change removes.
                return _redact_spans(text, results)

            sanitize_fn = _redact_text

        for unit in extracted.units:
            before = len(all_entities)
            unit.parent[unit.key] = await sanitize_fn(unit.text)
            for entity in all_entities[before:]:
                entity.path = unit.path
        sanitized = extracted.rebuild()
        validate_units([], token_mapping, self.config)

        return SanitizeResult(
            payload=sanitized,
            pii_report=all_entities,
            token_mapping=token_mapping or None,
            entities_found=len(all_entities),
        )

    async def detokenize_payload(
        self,
        payload: dict[str, Any],
        token_mapping: dict[str, str],
    ) -> dict[str, Any]:
        """Restore original PII values in an OpenAI-shaped response payload.

        Walks ``choices[].message.content`` and replaces surrogate tokens
        (e.g. ``[PERSON_1]``) with their original values using the mapping
        returned from a prior ``sanitize_payload(mode='tokenize')`` call.
        """
        if not token_mapping:
            return payload

        def _replace_tokens(text: str) -> str:
            result = text
            for token, original in token_mapping.items():
                result = result.replace(token, original)
            return result

        sanitized = dict(payload)

        # Chat completion response: choices[].message.content
        choices = sanitized.get("choices")
        if isinstance(choices, list):
            new_choices: list[dict[str, Any]] = []
            for choice in choices:
                if not isinstance(choice, dict):
                    new_choices.append(choice)
                    continue
                new_choice = dict(choice)
                message = choice.get("message")
                if isinstance(message, dict):
                    new_message = dict(message)
                    content = message.get("content")
                    if isinstance(content, str):
                        new_message["content"] = _replace_tokens(content)
                    new_choice["message"] = new_message
                new_choices.append(new_choice)
            sanitized["choices"] = new_choices

        return sanitized

    # ------------------------------------------------------------------
    # Private helpers -- OpenAI schema walkers
    # ------------------------------------------------------------------

    async def _analyze_many(
        self,
        texts: list[str],
        *,
        language: str,
        entities: list[str],
        score_threshold: float,
        entity_thresholds: dict[str, float] | None = None,
    ) -> list[list[RecognizerResult]]:
        """The analysis of each of *texts*, in order.

        Remembered per text and settings: a chat sends its whole history on
        every turn, and each message's analysis is the same as the last time.
        The texts not seen before go through spaCy together, in one batch and
        one thread hop.
        """
        if not self.runtime.healthy or self.runtime.closed:
            raise PIIError("pii_unavailable", 503)
        validate_units(texts, None, self.config)
        if language not in self.supported_languages or any(
            e not in self.entities_for_language(language) for e in entities
        ):
            raise PIIError("pii_unsupported_profile")
        scope = request_budget.get().scope if request_budget.get() else ""
        import math

        if not math.isfinite(score_threshold) or not 0 <= score_threshold <= 1:
            raise PIIError("pii_invalid_threshold")
        thresholds = {
            entity: (entity_thresholds or {}).get(entity, score_threshold)
            for entity in entities
        }
        settings_key = (
            self.profile.fingerprint,
            scope,
            language,
            tuple(sorted(entities)),
            tuple(sorted(thresholds.items())),
        )
        keys = [
            (hashlib.sha256(t.encode("utf-8")).digest(), settings_key) for t in texts
        ]
        missing: dict[tuple[Any, ...], str] = {}
        cached: dict[tuple, tuple] = {}
        for key, text in zip(keys, texts, strict=True):
            if text and key in self._analyses:
                # Retain this request's result across the inference await. Other
                # requests (or this batch's own inserts) can evict the LRU entry.
                cached[key] = self._analyses.get(key)
            elif text and key not in missing:
                missing[key] = text
        found: dict[tuple[Any, ...], tuple[tuple[str, int, int, float], ...]] = {}
        if missing:
            batch = await self.runtime.analyze(
                list(missing.values()),
                {
                    "language": language,
                    "entities": sorted(set(entities)),
                    "score_threshold": min(
                        thresholds.values(), default=score_threshold
                    ),
                },
            )
            for (key, text), results in zip(missing.items(), batch, strict=True):
                try:
                    validate_spans(text, results)
                except PIIError:
                    self.runtime.invalidate()
                    raise
                found[key] = tuple(
                    (r.entity_type, r.start, r.end, r.score)
                    for r in resolve_spans(
                        text,
                        [
                            r
                            for r in results
                            if r.score >= thresholds.get(r.entity_type, score_threshold)
                        ],
                    )
                )
                self._analyses.put(key, found[key])
        analyses: list[list[RecognizerResult]] = []
        for key, text in zip(keys, texts, strict=True):
            spans = found[key] if key in found else (cached[key] if text else ())
            analyses.append(
                [
                    RecognizerResult(entity_type=e, start=b, end=f, score=sc)
                    for e, b, f, sc in spans
                ],
            )
        return analyses

    @staticmethod
    def _texts_of(payload: dict[str, Any]) -> list[str]:
        """The same ordered units that the sanitizer rebuilds."""
        return [unit.text for unit in ExtractedPayload(payload).units]


def _redact_spans(text, results):
    """Replace exactly the accepted intervals; do not merge adjacent matches."""
    for result in sorted(results, key=lambda span: span.start, reverse=True):
        text = (
            text[: result.start] + "<" + result.entity_type + ">" + text[result.end :]
        )
    return text


class _AnalysisCache:
    """The most recent analyses, by text hash and scan settings.

    Holds entity types, offsets and scores -- never the text itself.
    """

    def __init__(self, size: int, byte_limit: int = 134_217_728) -> None:
        self._size = max(size, 0)
        self._byte_limit = byte_limit
        self._bytes = 0
        self._costs: dict = {}
        self._entries: OrderedDict[
            tuple[Any, ...],
            tuple[tuple[str, int, int, float], ...],
        ] = OrderedDict()

    def __contains__(self, key: object) -> bool:
        return key in self._entries

    def get(self, key: tuple[Any, ...]) -> tuple[tuple[str, int, int, float], ...]:
        self._entries.move_to_end(key)
        return self._entries[key]

    def put(
        self,
        key: tuple[Any, ...],
        value: tuple[tuple[str, int, int, float], ...],
    ) -> None:
        if not self._size:
            return
        cost = (
            sys.getsizeof(key)
            + 1024
            + sys.getsizeof(value)
            + sum(
                sys.getsizeof(row) + sum(sys.getsizeof(part) for part in row)
                for row in value
            )
        )
        self._bytes -= self._costs.pop(key, 0)
        self._entries[key] = value
        self._costs[key] = cost
        self._bytes += cost
        self._entries.move_to_end(key)
        while len(self._entries) > self._size or self._bytes > self._byte_limit:
            removed, _ = self._entries.popitem(last=False)
            self._bytes -= self._costs.pop(removed)


def _counters(token_mapping: dict[str, str]) -> dict[str, int]:
    """The highest number used per entity type in *token_mapping*."""
    counters: dict[str, int] = {}
    for token in token_mapping:
        entity, _, number = token.strip("[]").rpartition("_")
        if entity and number.isdigit():
            counters[entity] = max(counters.get(entity, 0), int(number))
    return counters
