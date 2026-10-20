"""Declared fields must protect values, avoid broad number/keyword matching,
and refuse typed sensitive arguments rather than silently leaking them.
"""

import json
from types import SimpleNamespace

import pytest
from presidio_anonymizer import AnonymizerEngine

from faden_pii.services.pii.detectors import _clean_neural_span
from faden_pii.services.pii.extended import ExtendedFieldsRecognizer, EXTENDED_ENTITIES
from faden_pii.services.pii.errors import PIIError
from faden_pii.services.pii.extraction import ExtractedPayload
from faden_pii.services.pii.profiles import Profile
from faden_pii.services.pii.service import PIIService


@pytest.mark.parametrize(
    "language,text,value,entity",
    [
        ("de", "Steuer-ID: 12345678901; Status: aktiv", "12345678901", "TAX_ID"),
        (
            "fr",
            "Numéro de sécurité sociale: 1 84 12 75 123 456 78; ok",
            "1 84 12 75 123 456 78",
            "SOCIAL_SECURITY_NUMBER",
        ),
        ("es", "Pasaporte: X1234567; válido", "X1234567", "PASSPORT"),
        (
            "en",
            "Driver license is AB12345678 and the package is ready.",
            "AB12345678",
            "DRIVER_LICENSE",
        ),
        ("de", "PLZ: 10115 Berlin", "10115", "POSTAL_CODE"),
        ("fr", "Âge: 42; sexe: féminin", "42", "AGE"),
        ("es", "Género: no binario; listo", "no binario", "GENDER"),
        (
            "de",
            "Diagnose: Diabetes Typ 2; Termin folgt",
            "Diabetes Typ 2",
            "HEALTH_DATA",
        ),
        (
            "en",
            "Diagnosis: asthma, diabetes; follow-up",
            "asthma, diabetes",
            "HEALTH_DATA",
        ),
        ("fr", "Religion: bouddhisme; champ suivant", "bouddhisme", "RELIGION"),
        (
            "es",
            "Orientación sexual: bisexual; registro",
            "bisexual",
            "SEXUAL_ORIENTATION",
        ),
        (
            "en",
            "Genetic data: BRCA1 c.68_69delAG; reviewed",
            "BRCA1 c.68_69delAG",
            "GENETIC_DATA",
        ),
        ("de", "Geboren am 14.03.1984.", "14.03.1984", "DATE_TIME"),
        ("fr", "Né le 14 mars 1984.", "14 mars 1984", "DATE_TIME"),
        ("es", "Fecha: 14 mayo de 1984.", "14 mayo de 1984", "DATE_TIME"),
        ("en", "Born March 14, 1984.", "March 14, 1984", "DATE_TIME"),
        ("en", "Issued on December 12th, 1998.", "December 12th, 1998", "DATE_TIME"),
        ("de", "Termin: März 14., 2024.", "März 14., 2024", "DATE_TIME"),
        ("es", "Fecha: enero 15º, 2021.", "enero 15º, 2021", "DATE_TIME"),
        ("fr", "Validité: août/26.", "août/26", "DATE_TIME"),
        ("en", "Applicant is 34 years old.", "34", "AGE"),
        ("de", "Der Kunde ist 29 Jahre alt.", "29", "AGE"),
        ("fr", "Candidat de 41 ans.", "41", "AGE"),
        ("es", "Paciente de 52 años de edad.", "52", "AGE"),
        ("en", "Gender identity: genderfluid; active", "genderfluid", "GENDER"),
        ("de", "Geschlechtsidentität: nicht-binär; aktiv", "nicht-binär", "GENDER"),
        ("fr", "Identité de genre: non-binaire; actif", "non-binaire", "GENDER"),
        ("es", "Sexo biológico (femenino) registrado", "femenino", "GENDER"),
        ("es", "Licencia de conducir: B12345678", "B12345678", "DRIVER_LICENSE"),
        ("es", "Número de identificación: 12345678Z", "12345678Z", "NATIONAL_ID"),
        ("de", "Bürgermeisterin Anna Schmidt", "Bürgermeisterin", "TITLE"),
    ],
)
def test_declared_values_and_dates(language, text, value, entity):
    matches = ExtendedFieldsRecognizer(language).analyze(text, [entity])
    assert value in [text[m.start : m.end] for m in matches]


@pytest.mark.parametrize(
    "text",
    [
        "Religion is studied in history.",
        "Health data can require special protection.",
        "Die Diagnose wird später gestellt.",
        "Le numéro fiscal est obligatoire.",
        "El pasaporte es necesario.",
        "Order: 123456789; version: 4.2; quantity: 42",
        "Age: 999",
        "The patient and her doctor discussed treatment.",
    ],
)
def test_no_uncontextual_numbers_or_sensitive_subject_keywords(text):
    assert not ExtendedFieldsRecognizer("en").analyze(text, list(EXTENDED_ENTITIES))


def tool_payload(arguments):
    return {
        "messages": [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "type": "function",
                        "function": {
                            "name": "submit",
                            "arguments": json.dumps(arguments),
                        },
                    }
                ],
            }
        ]
    }


def test_structured_keys_preserve_declared_sensitive_context():
    extracted = ExtractedPayload(
        tool_payload({"diagnosis": "Diabetes", "age": "42", "count": 3}),
        declared_entities={"HEALTH_DATA", "AGE"},
    )
    assert [(u.text, u.entity_hint) for u in extracted.units] == [
        ("Diabetes", "HEALTH_DATA"),
        ("42", "AGE"),
    ]


def test_sensitive_number_refused_only_when_that_filter_is_active():
    payload = tool_payload({"age": 42, "count": 3})
    with pytest.raises(PIIError, match="pii_unsupported_sensitive_value"):
        ExtractedPayload(payload, declared_entities={"AGE"})
    assert ExtractedPayload(payload).rebuild() == payload
    assert (
        ExtractedPayload(payload, declared_entities={"HEALTH_DATA"}).rebuild()
        == payload
    )


@pytest.mark.anyio
async def test_declared_tool_value_masks_only_value_and_round_trips():
    service = PIIService(SimpleNamespace(), AnonymizerEngine())
    service.config = SimpleNamespace(
        **{**service.config.model_dump(), "pii_entity_profile": "extended"}
    )
    service._batch.analyze_iterator = lambda *, texts, **kw: [[] for _ in texts]
    service.runtime.detector.entities = ["HEALTH_DATA"]
    payload = tool_payload({"diagnosis": "Diabetes", "count": 3, "ready": True})
    result = await service.sanitize_payload(
        payload, mode="tokenize"
    )
    arguments = json.loads(
        result.payload["messages"][0]["tool_calls"][0]["function"]["arguments"]
    )
    assert arguments == {"diagnosis": "[HEALTH_DATA_1]", "count": 3, "ready": True}
    assert result.token_mapping == {"[HEALTH_DATA_1]": "Diabetes"}
    await service.close()


def test_extended_identity_requires_explicit_policy_adoption():
    assert Profile(backend="gliner-onnx").id == "patronus-small-pii"
    assert (
        Profile(backend="gliner-onnx", entity_profile="extended").id
        == "patronus-small-pii-extended-v1"
    )


@pytest.mark.parametrize(
    "text",
    [
        "hi",
        "Hi",
        "hey",
        "Hallo",
        "Danke",
        "Bonjour",
        "Merci",
        "Hola",
        "OK",
        "Sure",
        "Ja",
        "Nein",
        "Vale",
        "claro",
        "Guten Morgen",
        "Buenas tardes",
    ],
)
def test_conversational_greetings_filtered_from_neural_spans(text):
    assert _clean_neural_span(text, 0, len(text)) is None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Hey Max", "Max"),
        ("Hallo Jörg", "Jörg"),
        ("Hi, Anna", "Anna"),
        ("Bonjour Camille", "Camille"),
        ("Hola María", "María"),
        ("John Smith", "John Smith"),
    ],
)
def test_greeting_prefix_trimmed_from_neural_person_spans(text, expected):
    cleaned = _clean_neural_span(text, 0, len(text))
    assert cleaned is not None
    start, end = cleaned
    assert text[start:end] == expected
