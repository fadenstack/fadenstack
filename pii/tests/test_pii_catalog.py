"""Configured coverage must agree with service and policy identities."""

import pytest
from pydantic import ValidationError

from faden_pii.catalog import selection
from faden_pii.settings import Settings
from faden_pii.services.pii.profiles import (
    Profile,
    pipeline_sources,
    verify_calibration,
)
from faden_pii.services.pii.windowing import windows


def test_environment_configures_actual_locale(monkeypatch):
    monkeypatch.setenv("FADEN_PII_BACKEND", "gliner-onnx")
    monkeypatch.setenv("FADEN_PII_MODEL_ID", "patronus-onnx-v1")
    monkeypatch.setenv("FADEN_PII_LOCALE_PACK", "german-v1")
    monkeypatch.setenv("FADEN_PII_DEFAULT_LANGUAGE", "de")
    settings = Settings(_env_file=None)
    assert settings.pii_locale_pack == "german-v1"
    assert selection(
        settings.pii_backend, settings.pii_model_id, settings.pii_locale_pack
    )[3]["languages"] == ["de"]
    assert (
        Profile(backend="gliner-onnx", locale_pack="german-v1").id
        == "patronus-small-pii:german-v1"
    )
    assert Profile(backend="gliner-onnx").id == "patronus-small-pii"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"pii_locale_pack": "asia"},
        {"pii_model_id": "gliner2-multi-v1"},
        {"pii_model_revision": "other"},
        {"pii_catalog_version": "other"},
        {"pii_locale_pack": "german-v1"},
        {"pii_execution_profile": "gpu-shared"},
        {"pii_batch_short": 32},
    ],
)
def test_mismatched_runtime_identity_is_refused(kwargs):
    with pytest.raises(ValidationError):
        Settings(pii_backend="gliner-onnx", **kwargs)


def test_unspaced_cjk_and_mixed_unicode_have_complete_bounded_windows():
    text = "前文🙂" + "東京都新宿区山田太郎" * 100 + " fin"
    result = windows(text, lambda value: len(value) + 20, max_tokens=256)
    covered = set()
    for window in result:
        assert window.text == text[window.offset : window.offset + len(window.text)]
        assert window.tokens <= 256
        covered.update(range(window.offset, window.offset + len(window.text)))
    assert covered == set(range(len(text)))


def test_calibration_requires_exact_rule_and_locale_identity():
    profile = Profile(backend="gliner-onnx")
    data = {
        "profile_id": profile.id,
        "model_revision": "fixture",
        "locale_pack": profile.locale_pack,
        "pipeline_sources": pipeline_sources(),
    }
    verify_calibration(data, profile, "fixture")
    for key in ("profile_id", "model_revision", "locale_pack", "pipeline_sources"):
        invalid = {**data, key: "different"}
        with pytest.raises(ValueError, match="pipeline and locale"):
            verify_calibration(invalid, profile, "fixture")
