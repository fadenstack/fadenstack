"""The PII policy a new install starts with."""

from faden_backend.web.lifespan import _CORE_DEFAULT_PII_POLICY


def test_dates_are_not_redacted_by_default() -> None:
    """Plain numbers are tagged as dates: "add 17 and 25, then ..." lost its 25."""
    entities = _CORE_DEFAULT_PII_POLICY["presidio"]["entities"]
    assert "DATE_TIME" not in entities
    assert {"PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD", "IBAN_CODE"} <= set(entities)


def test_local_models_are_not_scanned_by_default() -> None:
    """The default protects what leaves for cloud providers."""
    egress = _CORE_DEFAULT_PII_POLICY["egress"]
    assert (egress["enabled_for_cloud"], egress["enabled_for_local"], egress["mode"]) == (True, False, "redact")
