"""Locale/model migrations must preserve the installed detector and fail atomically."""

import json
from pathlib import Path

import pytest

from faden_cli.core import pii_catalog, pii_runtime
from faden_cli.core.env_gen import read_env_file, set_env_values
from test_pii_hardware import selection


def test_single_catalog_matches_service_and_has_no_unqualified_language():
    source = Path(__file__).resolve().parents[2] / "pii/faden_pii/pii-catalog.json"
    assert pii_catalog.catalog() == json.loads(source.read_text(encoding="utf-8"))
    for pack in pii_catalog.catalog()["locales"].values():
        assert set(pack["languages"]) <= {"en", "de", "fr", "es"}
        pii_catalog.resolve(pack["recommended_model"], "europe-west-v1", "cpu")


def test_default_compose_bounds_host_ram_for_runtime_and_candidate():
    import yaml
    root = Path(__file__).resolve().parents[2]
    service = yaml.safe_load((root / "shared/docker-compose.yaml").read_text())["services"]["faden-pii"]
    assert service["mem_limit"] == "${FADEN_PII_MEMORY_LIMIT:-4g}"


def test_new_german_install_pins_identity_and_default_language(selection):
    env, downloaded = selection
    pii_runtime.deploy_step(env, yes=True, locale="german-v1", model="auto")
    values = read_env_file(env)
    assert values["FADEN_PII_MODEL_ID"] == "patronus-onnx-v1"
    assert values["FADEN_PII_LOCALE_PACK"] == "german-v1"
    assert values["FADEN_PII_DEFAULT_LANGUAGE"] == "de"
    assert (
        values["FADEN_PII_MODEL_REVISION"] == pii_catalog.catalog()["models"]["patronus-onnx-v1"]["revision"]
    )
    pii_runtime.deploy_step(env, yes=True, upgrade=True)
    assert downloaded == ["gliner-onnx"]
    assert read_env_file(env)["FADEN_PII_LOCALE_PACK"] == "german-v1"


def test_model_is_preserved_when_changing_execution_to_cpu(selection):
    env, downloaded = selection
    pii_runtime.deploy_step(env, yes=True, model="gliner2-multi-v1")
    pii_runtime.deploy_step(env, yes=True, requested="cpu", upgrade=True)
    assert read_env_file(env)["FADEN_PII_MODEL_ID"] == "gliner2-multi-v1"
    assert downloaded == ["gliner2"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"locale": "asia"},
        {"model": "https://unapproved.example/model"},
        {"model": "patronus-onnx-v1", "requested": "gpu-shared"},
    ],
)
def test_invalid_selection_does_not_change_configuration(selection, monkeypatch, kwargs):
    from faden_cli.core import pii_hardware

    env, downloaded = selection
    device = pii_hardware.PIIGpu("0", "GPU-abcd-1234", "NVIDIA example", "7.5", 8192)
    monkeypatch.setattr(pii_hardware, "detect_support", lambda **kw: pii_hardware.PIIGpuSupport([device]))
    before = env.read_bytes()
    with pytest.raises(ValueError):
        pii_runtime.deploy_step(env, yes=True, **kwargs)
    assert env.read_bytes() == before
    assert not downloaded


def test_revision_drift_requires_explicit_model_migration(selection):
    env, _ = selection
    set_env_values(
        env,
        {
            "FADEN_PII_BACKEND": "gliner2",
            "FADEN_PII_MODEL_ID": "gliner2-multi-v1",
            "FADEN_PII_MODEL_REVISION": "unknown",
        },
    )
    before = env.read_bytes()
    with pytest.raises(ValueError, match="revision differs"):
        pii_runtime.deploy_step(env, yes=True, upgrade=True)
    assert env.read_bytes() == before


@pytest.mark.parametrize("failure", [False, True, "timeout"])
def test_candidate_warmup_is_cpu_bounded_and_always_cleans_its_container(tmp_path, monkeypatch, failure):
    import subprocess
    from types import SimpleNamespace

    env = tmp_path / ".env"
    set_env_values(env, {"FADEN_PII_BACKEND": "gliner2"})
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[1] != "rm" and failure == "timeout":
            raise subprocess.TimeoutExpired(command, 180)
        return SimpleNamespace(returncode=1 if failure and command[1] != "rm" else 0)

    monkeypatch.setattr(subprocess, "run", run)
    ctx = SimpleNamespace(env_file=env, base_cmd=lambda: ["docker", "compose"])
    if failure:
        with pytest.raises(ValueError, match="PII startup stopped"):
            pii_runtime.warm_candidate(ctx)
    else:
        pii_runtime.warm_candidate(ctx)
    assert len(calls) == 2
    assert "FADEN_PII_CPU_THREADS=1" in calls[0]
    assert "FADEN_PII_EXECUTION_PROFILE=cpu" in calls[0]
    name = calls[0][calls[0].index("--name") + 1]
    assert name.startswith("faden-pii-validate-")
    assert calls[1] == ["docker", "rm", "-f", name]
