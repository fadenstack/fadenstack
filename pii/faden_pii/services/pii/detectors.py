"""Optional neural loaders; no external PII model requests."""

import hashlib
import importlib.metadata
import json
import os
import re

from presidio_analyzer import AnalyzerEngine, BatchAnalyzerEngine, RecognizerResult
from presidio_analyzer.nlp_engine import NlpEngineProvider

from faden_pii.services.pii.errors import PIIError
from faden_pii.services.pii.spans import validate_spans
from faden_pii.services.pii.profiles import (
    LANGUAGES,
    Profile,
    pipeline_sources,
    load_calibration,
    verify_manifest,
    verify_calibration,
)
from faden_pii.services.pii.windowing import Window, windows
from faden_pii.catalog import catalog, selection

_GREETING_PREFIX_RE = re.compile(
    r"^(?:hi|hello|hey|yo|sup|hallo|moin|servus|bonjour|salut|coucou|bonsoir|hola|buenas)[\s,!.:;-]+",
    re.IGNORECASE,
)

_CONVERSATIONAL_STOPWORDS = frozenset(
    {
        # EN
        "hi",
        "hello",
        "hey",
        "yo",
        "sup",
        "hi there",
        "hello there",
        "hey there",
        "good morning",
        "good afternoon",
        "good evening",
        "good night",
        "ok",
        "okay",
        "yes",
        "no",
        "yep",
        "nope",
        "sure",
        "thanks",
        "thank you",
        "ty",
        "thx",
        "please",
        "cool",
        "great",
        "nice",
        "awesome",
        "perfect",
        "got it",
        "understood",
        "test",
        "help",
        "ping",
        "start",
        "stop",
        "continue",
        "retry",
        "why",
        "how",
        "what",
        # DE
        "hallo",
        "moin",
        "servus",
        "grüß gott",
        "gruess gott",
        "guten morgen",
        "guten tag",
        "guten abend",
        "gute nacht",
        "danke",
        "vielen dank",
        "bitte",
        "ja",
        "nein",
        "alles klar",
        "klar",
        "genau",
        # FR
        "bonjour",
        "salut",
        "coucou",
        "bonsoir",
        "bonne nuit",
        "merci",
        "merci beaucoup",
        "oui",
        "non",
        "d'accord",
        "daccord",
        "allô",
        "allo",
        # ES
        "hola",
        "buenas",
        "buenos días",
        "buenos dias",
        "buenas tardes",
        "buenas noches",
        "gracias",
        "muchas gracias",
        "si",
        "sí",
        "vale",
        "claro",
        "perfecto",
    }
)


def _clean_neural_span(
    text: str, start: int, end: int, entity_type: str = "PERSON"
) -> tuple[int, int] | None:
    """Limit greeting cleanup to PERSON and conversational units.

    A matching word inside a sentence may be a real name ("My name is Ty"),
    and place names such as Nice must never go through a person stop list.
    """
    if entity_type != "PERSON":
        return start, end
    span_text = text[start:end]
    normalized = " ".join(span_text.strip(" \t\r\n,!?.:;¡¿\"'").lower().split())
    whole_unit = " ".join(text.strip(" \t\r\n,!?.:;¡¿\"'").lower().split())
    if not normalized or (
        normalized == whole_unit and normalized in _CONVERSATIONAL_STOPWORDS
    ):
        return None
    match = _GREETING_PREFIX_RE.match(span_text) if not text[:start].strip() else None
    if match:
        new_start = start + match.end()
        if new_start < end:
            rem_norm = " ".join(
                text[new_start:end].strip(" \t\r\n,!?.:;¡¿\"'").lower().split()
            )
            if not rem_norm:
                return None
            return new_start, end
    return start, end


class NeuralDetector:
    """One loaded semantic model with local Presidio rules on original units."""

    def __init__(self, settings):
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
        self.settings = settings
        self.model_id, self.model_entry, self.locale_pack, self.pack = selection(
            settings.pii_backend, settings.pii_model_id, settings.pii_locale_pack
        )
        self.languages = tuple(self.pack["languages"])
        self.labels = self.model_entry["labels"]
        self.metadata = {
            "model_id": self.model_id,
            "repository": self.model_entry["repository"],
            "revision": self.model_entry["revision"],
            "adapter_version": self.model_entry["adapter_version"],
            "locale_pack": self.locale_pack,
            "installed_languages": list(self.languages),
            "qualified_languages": self.model_entry["languages"],
            "catalog_version": catalog()["version"],
            "rules_version": catalog()["rules_version"],
            "execution": settings.pii_execution_profile,
        }
        self.lock = None
        if settings.pii_model_path is None or settings.pii_manifest_path is None:
            raise ValueError(
                "Neural PII requires a local model path and verified manifest"
            )
        manifest = verify_manifest(
            settings.pii_model_path, settings.pii_manifest_path, settings.pii_backend
        )
        if (
            settings.pii_backend == "gliner-onnx"
            and settings.pii_precision != "float16"
        ):
            raise ValueError("The validated Patronus ONNX artifact is FP16")
        calibration = load_calibration(settings.pii_calibration_path)
        verify_calibration(
            calibration,
            Profile(
                backend=settings.pii_backend,
                entity_profile=settings.pii_entity_profile,
                locale_pack=self.locale_pack,
            ),
            manifest["revision"],
        )
        self.device = (
            "cpu" if settings.pii_execution_profile == "cpu" else settings.pii_device
        )
        if self.device != "cpu":
            from filelock import FileLock
            import torch

            # CUDA ordinals may be remapped by Docker/CUDA_VISIBLE_DEVICES.
            # Lock the actual selected device rather than an nvidia-smi index.
            uuid = str(torch.cuda.get_device_properties(self.device).uuid)
            if not uuid.startswith("GPU-"):
                uuid = "GPU-" + uuid
            if not uuid.startswith("GPU-") or any(
                c not in "GPU-abcdefABCDEF0123456789" for c in uuid
            ):
                raise ValueError("Invalid PII GPU identity")
            settings.pii_lock_dir.mkdir(parents=True, exist_ok=True)
            self.lock = FileLock(str(settings.pii_lock_dir / (uuid + ".lock")))
            self.lock.acquire(timeout=0)
        try:
            import torch

            self.torch = torch
            torch.set_num_threads(settings.pii_cpu_threads)
            if torch.get_num_interop_threads() != 1:
                torch.set_num_interop_threads(1)
            if settings.pii_backend == "gliner2":
                from gliner2 import AutoExtractor

                if self.device != "cpu":
                    total = torch.cuda.get_device_properties(self.device).total_memory
                    fraction = settings.pii_gpu_memory_mib * 2**20 / total
                    if not 0 < fraction <= 1:
                        raise ValueError(
                            "PII GPU memory budget exceeds device capacity"
                        )
                    torch.cuda.set_per_process_memory_fraction(fraction, self.device)
                self.model = AutoExtractor.from_pretrained(
                    str(settings.pii_model_path), map_location=self.device
                ).eval()
                if settings.pii_precision == "float16":
                    if self.device == "cpu":
                        raise ValueError("GLiNER2 FP16 requires CUDA")
                    self.model.quantize()
                schema = self.model.create_schema().entities(list(self.labels))
                self.schema = self.model._build_schema_dicts_and_metadata([schema])[0][
                    0
                ]
                packages = ["gliner2", "torch", "transformers"]
            else:
                import onnxruntime as ort
                from gliner import GLiNER

                options = ort.SessionOptions()
                options.log_severity_level = 3
                options.intra_op_num_threads = settings.pii_cpu_threads
                options.inter_op_num_threads = 1
                options.add_session_config_entry("session.intra_op.allow_spinning", "0")
                providers = ["CPUExecutionProvider"]
                if self.device != "cpu":
                    if "CUDAExecutionProvider" not in ort.get_available_providers():
                        raise ValueError("PII ONNX CUDA provider is not installed")
                    providers = [
                        (
                            "CUDAExecutionProvider",
                            {
                                "device_id": int(self.device.removeprefix("cuda:")),
                                "gpu_mem_limit": settings.pii_gpu_memory_mib * 2**20,
                            },
                        )
                    ]
                self.model = GLiNER.from_pretrained(
                    str(settings.pii_model_path),
                    local_files_only=True,
                    load_tokenizer=True,
                    runtime="onnxruntime",
                    runtime_model_file="onnx/fp16/model_fp16.onnx",
                    map_location="cpu",
                    runtime_options={
                        "session_options": options,
                        "providers": providers,
                    },
                ).eval()
                packages = ["gliner", "torch", "onnxruntime", "transformers"]
            models = [
                {"lang_code": lang, "model_name": settings.pii_nlp_models[lang]}
                for lang in LANGUAGES
            ]
            import importlib.util

            if any(
                importlib.util.find_spec(item["model_name"]) is None for item in models
            ):
                raise ValueError(
                    "Local PII NLP packages are missing; provision them before offline startup"
                )
            engine = NlpEngineProvider(
                nlp_configuration={"nlp_engine_name": "spacy", "models": models}
            ).create_engine()
            for pipeline in engine.nlp.values():
                if "ner" in pipeline.pipe_names:
                    pipeline.disable_pipes("ner")
            self.analyzer = AnalyzerEngine(
                nlp_engine=engine, supported_languages=list(LANGUAGES)
            )
            self.analyzer.registry.remove_recognizer("SpacyRecognizer")
            from copy import copy
            from presidio_analyzer.predefined_recognizers import PhoneRecognizer

            universal = {
                "EMAIL_ADDRESS",
                "CREDIT_CARD",
                "IBAN_CODE",
                "IP_ADDRESS",
                "URL",
            }
            for lang in LANGUAGES:
                if lang == "en":
                    continue
                for recognizer in list(self.analyzer.registry.recognizers):
                    if (
                        recognizer.supported_language == "en"
                        and set(recognizer.supported_entities) <= universal
                    ):
                        localized = copy(recognizer)
                        localized.supported_language = lang
                        self.analyzer.registry.add_recognizer(localized)
                self.analyzer.registry.add_recognizer(
                    PhoneRecognizer(
                        supported_language=lang,
                        supported_regions=["DE", "FR", "ES", "GB", "US"],
                    )
                )
            if settings.pii_entity_profile == "extended":
                from faden_pii.services.pii.extended import ExtendedFieldsRecognizer

                for lang in LANGUAGES:
                    self.analyzer.registry.add_recognizer(
                        ExtendedFieldsRecognizer(lang)
                    )
            self.rules = BatchAnalyzerEngine(analyzer_engine=self.analyzer)
            identity = {
                "manifest": manifest,
                "calibration": calibration,
                "labels": self.labels,
                "nlp": settings.pii_nlp_models,
                "precision": settings.pii_precision,
                "device": self.device,
                "rules": "presidio-local-v2",
                "pipeline_sources": pipeline_sources(),
                "entity_profile": settings.pii_entity_profile,
                "catalog_version": catalog()["version"],
                "model_id": self.model_id,
                "locale_pack": self.locale_pack,
                "languages": self.languages,
                "versions": {
                    p: importlib.metadata.version(p)
                    for p in packages + ["presidio-analyzer", "spacy"]
                },
            }
            self.profile = Profile(
                settings.pii_backend,
                hashlib.sha256(
                    json.dumps(identity, sort_keys=True).encode()
                ).hexdigest(),
                self.languages,
                calibration.get("default_threshold", settings.pii_score_threshold),
                calibration.get("thresholds", {}),
                calibration.get("reviewed", False) is True,
                settings.pii_entity_profile,
                self.locale_pack,
            )
            self.entities = sorted(
                set(self.analyzer.get_supported_entities()) | set(self.labels.values())
            )
            self.entity_languages = {
                lang: sorted(
                    set(self.analyzer.get_supported_entities(language=lang))
                    | set(self.labels.values())
                )
                for lang in self.languages
            }
            if any(
                entity not in self.entity_languages[language]
                for language, values in calibration.get("thresholds", {}).items()
                for entity in values
            ):
                raise PIIError("pii_invalid_calibration", 503)
            self.warm_up()
        except BaseException:
            self.close()
            raise

    def warm_up(self):
        for language, text, name in [
            ("en", "My first name is Anna.", "Anna"),
            ("de", "Mein Vorname ist Jörg.", "Jörg"),
            ("fr", "Mon prénom est Camille.", "Camille"),
            ("es", "Mi nombre es María.", "María"),
        ]:
            if language not in self.languages:
                continue
            threshold = self.profile.thresholds.get(language, {}).get(
                "PERSON", self.profile.threshold
            )
            options = {
                "language": language,
                "entities": ["PERSON"],
                "score_threshold": threshold,
            }
            _, pieces = self.prepare(text, options)
            spans = self.predict(pieces, options)[0]
            start = text.index(name)
            coverage = {i for span in spans for i in range(span.start, span.end)}
            if not set(range(start, start + len(name))) <= coverage:
                raise ValueError("Local PII model failed multilingual startup smoke")

    def length(self, text: str) -> int:
        """Count the same encoded schema/text that the forward pass receives."""
        if self.settings.pii_backend == "gliner2":
            batch = self.model.processor.collate_fn_inference(
                [(text, self.schema)],
                error_policy="raise",
                architecture=self.model.architecture,
            )
            return int(batch.input_ids.shape[1])
        processor = self.model.data_processor
        words = [word for word, _, _ in processor.words_splitter(text)]
        inputs, _ = processor.prepare_inputs([words], list(self.labels))
        return len(
            processor.transformer_tokenizer(
                inputs, is_split_into_words=True, truncation=False
            )["input_ids"][0]
        )

    def prepare(
        self, text: str, options: dict
    ) -> tuple[list[RecognizerResult], list[Window]]:
        pieces = windows(
            text,
            self.length,
            max_tokens=self.settings.pii_window_tokens,
            overlap=self.settings.pii_window_overlap,
            max_windows=self.settings.pii_max_request_windows,
        )
        supported = self.analyzer.get_supported_entities(language=options["language"])
        rule_entities = [
            entity for entity in options["entities"] if entity in supported
        ]
        rows = (
            self.rules.analyze_iterator(
                texts=[text], batch_size=1, **{**options, "entities": rule_entities}
            )[0]
            if rule_entities
            else []
        )
        validate_spans(text, rows)
        return rows, pieces

    def predict(
        self, pieces: list[Window], options: dict
    ) -> list[list[RecognizerResult]]:
        threshold = options["score_threshold"]
        texts = [p.text for p in pieces]
        with self.torch.inference_mode():
            if self.settings.pii_backend == "gliner2":
                rows = self.model.batch_extract_entities(
                    texts,
                    list(self.labels),
                    batch_size=len(texts),
                    threshold=threshold,
                    include_spans=True,
                    include_confidence=True,
                )
                rows = [
                    [
                        dict(v, label=label)
                        for label, values in row["entities"].items()
                        for v in values
                    ]
                    for row in rows
                ]
            else:
                rows = self.model.inference(
                    texts,
                    list(self.labels),
                    batch_size=len(texts),
                    threshold=threshold,
                    flat_ner=False,
                    multi_label=True,
                )
        output = []
        for text, row in zip(texts, rows, strict=True):
            spans = []
            for value in row:
                start, end = value["start"], value["end"]
                expected = value["text"]
                # GLiNER2 occasionally includes a trailing punctuation token beyond
                # the window's final character (e.g. emits "M." for source "M").
                # Clip only a verified exact source prefix plus punctuation overhang.
                # Never relocate a name or accept an unrelated substring.
                if (
                    type(start) is int
                    and type(end) is int
                    and 0 <= start < len(text) < end
                    and isinstance(value.get("text"), str)
                    and value["text"].startswith(text[start:])
                    and end - start == len(value["text"])
                ):
                    suffix = value["text"][len(text) - start :]
                    if suffix and all(not character.isalnum() for character in suffix):
                        end = len(text)
                        expected = text[start:end]
                if (
                    type(start) is not int
                    or type(end) is not int
                    or not 0 <= start < end <= len(text)
                    or text[start:end] != expected
                ):
                    raise PIIError("pii_invalid_detector_result", 503)
                kind = self.labels[value["label"]]
                cleaned = _clean_neural_span(text, start, end, kind)
                if cleaned is None:
                    continue
                start, end = cleaned
                if kind in options["entities"]:
                    spans.append(
                        RecognizerResult(
                            kind,
                            start,
                            end,
                            float(value.get("confidence", value.get("score", 0))),
                        )
                    )
            validate_spans(text, spans)
            output.append(spans)
        return output

    def batch_key(self, options: dict):
        # Rules are prepared separately in each request's language. The neural
        # schema is identical across languages, so compatible requests can share
        # a forward without splitting the available batch capacity by locale.
        return (
            self.model_id,
            self.locale_pack,
            tuple(self.labels.items()),
            tuple(options["entities"]),
            options["score_threshold"],
        )

    def close(self):
        try:
            if hasattr(self, "model"):
                del self.model
            if getattr(self, "device", "cpu") != "cpu" and hasattr(self, "torch"):
                self.torch.cuda.empty_cache()
        finally:
            if self.lock is not None:
                self.lock.release()
