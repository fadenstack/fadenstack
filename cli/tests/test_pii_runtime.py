"""Installation must retain explicit runtime choices and one model owner."""

import hashlib
import json
from pathlib import Path

import pytest

from faden_cli.core.env_gen import read_env_file, set_env_values
from faden_cli.core.pii_runtime import configure, gpu_overlay
from faden_cli.core.sysinfo import SystemInfo, calculate_tune_profile


def test_cpu_preset_verifies_local_artifacts_and_retains_other_settings(tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    (model / "weights.onnx").write_bytes(b"fixture")
    data = {
        "version": 1,
        "backend": "gliner-onnx",
        "revision": "6cfcbd93d7fc6f5e1658f80afa38f73891b91693",
        "files": {"weights.onnx": hashlib.sha256(b"fixture").hexdigest()},
    }
    (model / "manifest.json").write_text(json.dumps(data))
    env = tmp_path / ".env"
    set_env_values(env, {"EXISTING_POLICY": "keep", "FADEN_PII_SERVICE_TOKEN": "fixture",
                         "FADEN_PII_ENTITY_PROFILE": "extended"})
    assert configure(env, "cpu", model)
    values = read_env_file(env)
    assert values["EXISTING_POLICY"] == "keep"
    assert values["FADEN_PII_WORKERS_COUNT"] == "1"
    assert values["FADEN_PII_BACKEND"] == "gliner-onnx"
    assert values["FADEN_PII_ENTITY_PROFILE"] == "extended"
    assert values["FADEN_PII_BUILD_TARGET"] == "prod-cpu"
    before = env.read_text()
    assert not configure(env, None, None)
    assert env.read_text() == before
    (model / "weights.onnx").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        configure(env, "cpu", model)
    configure(env, "legacy", None)
    assert read_env_file(env)["FADEN_PII_ENTITY_PROFILE"] == "standard"


def test_neural_sizing_keeps_one_owner_and_reserves_more_ram():
    system = SystemInfo(physical_cores=32, logical_cores=64, total_ram_bytes=32 * 1024**3)
    legacy = calculate_tune_profile("prod", system=system, modules={"pii"})
    neural = calculate_tune_profile("prod", system=system, modules={"pii"}, pii_backend="gliner2")
    assert neural.workers["pii"] == 1
    assert neural.memory_mb()["pii x1"] > legacy.memory_mb()["pii x1"]


def test_gpu_overlay_is_selected_for_every_compose_invocation(tmp_path):
    env = tmp_path / ".env"
    set_env_values(env, {"FADEN_PII_EXECUTION_PROFILE": "gpu-shared"})
    overlay = tmp_path / "docker-compose.pii-gpu.yaml"
    overlay.write_text("services: {}")
    assert gpu_overlay(env, tmp_path) == overlay
    set_env_values(env, {"FADEN_PII_EXECUTION_PROFILE": "cpu"})
    assert gpu_overlay(env, tmp_path) is None
