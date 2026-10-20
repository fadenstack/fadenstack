"""Detect actual compatibility and preserve deliberate upgrade choices."""

import hashlib
import json
from types import SimpleNamespace

from click.testing import CliRunner
import pytest

from faden_cli.core import compose, detect, pii_artifacts, pii_hardware, pii_runtime, sysinfo
from faden_cli.core.env_gen import read_env_file, set_env_values


def gpu_info():
    return detect.GpuInfo(
        vendor="nvidia", cuda_version="12.8", devices=[detect.GpuDevice(0, "NVIDIA example", 8192, "570")]
    )


@pytest.mark.parametrize(
    ("capability", "free", "supported"),
    [("7.5", 6000, True), ("6.1", 6000, False), ("8.9", 6000, True), ("7.5", 4000, False)],
)
def test_gpu_checks_architecture_and_headroom(monkeypatch, capability, free, supported):
    monkeypatch.setattr(compose, "has_nvidia_gpu", lambda: True)
    monkeypatch.setattr(pii_hardware.shutil, "which", lambda _: "nvidia-smi")
    monkeypatch.setattr(
        pii_hardware.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=f"0, GPU-abcd-1234, {capability}, {free}\n"),
    )
    result = pii_hardware.detect_support(gpu_info())
    assert result.available is supported
    assert bool(result.reasons) is not supported


def test_gpu_needs_driver_and_docker(monkeypatch):
    info = gpu_info()
    info.cuda_version = "12.7"
    assert "driver" in pii_hardware.detect_support(info).reasons[0]
    info.cuda_version = "12.8"
    monkeypatch.setattr(compose, "has_nvidia_gpu", lambda: False)
    assert "Docker" in pii_hardware.detect_support(info).reasons[0]
    assert not pii_hardware.detect_support(detect.GpuInfo()).available
    assert not pii_hardware.detect_support(detect.GpuInfo(vendor="amd")).available


@pytest.mark.parametrize("header", ["CUDA Version: 12.8", "CUDA UMD Version: 13.4"])
def test_detects_both_nvidia_header_formats(monkeypatch, header):
    monkeypatch.setattr(detect.shutil, "which", lambda n: n if n == "nvidia-smi" else None)
    responses = iter(["0, NVIDIA example, 8192, 616.92", header])
    monkeypatch.setattr(
        detect, "_run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout=next(responses))
    )
    assert detect.detect_gpu().cuda_version == header.split()[-1]


@pytest.fixture
def selection(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    set_env_values(env, {"SECRET_TO_KEEP": "private-fixture"})
    downloaded = []

    def provision(backend, root):
        downloaded.append(backend)
        root.mkdir(parents=True, exist_ok=True)
        (root / "model").write_bytes(b"fixture")
        (root / "manifest.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "backend": backend,
                    "revision": pii_artifacts.MODELS[backend][1],
                    "files": {"model": hashlib.sha256(b"fixture").hexdigest()},
                }
            )
        )
        return root

    monkeypatch.setattr(pii_artifacts, "provision", provision)
    monkeypatch.setattr(sysinfo, "effective_threads", lambda: 4)
    monkeypatch.setattr(
        pii_hardware, "detect_support", lambda **kw: pii_hardware.PIIGpuSupport(reasons=["No supported GPU"])
    )
    return env, downloaded


def test_gpu_fallback_provisions_cpu_and_clamps_threads(selection):
    env, downloaded = selection
    set_env_values(
        env,
        {
            "FADEN_PII_BACKEND": "gliner2",
            "FADEN_PII_EXECUTION_PROFILE": "gpu-shared",
            "FADEN_PII_CALIBRATION_PATH": "/gpu.json",
        },
    )
    pii_runtime.deploy_step(env, yes=True, threads=64, upgrade=True)
    values = read_env_file(env)
    assert downloaded == ["gliner2"]
    assert values["FADEN_PII_BACKEND"] == "gliner2"
    assert values["FADEN_PII_EXECUTION_PROFILE"] == "cpu"
    assert values["FADEN_PII_CPU_THREADS"] == "4"
    assert values["FADEN_PII_CALIBRATION_PATH"] == "/gpu.json"
    assert values["FADEN_PII_BUILD_TARGET"] == "prod-gpu"
    assert values["FADEN_PII_BATCH_SHORT"] == "8"
    assert values["SECRET_TO_KEEP"] == "private-fixture"
    assert pii_runtime.gpu_overlay(env, env.parent) is None


def test_existing_legacy_upgrade_keeps_its_semantics_without_download(selection):
    env, downloaded = selection
    pii_runtime.deploy_step(env, yes=True, upgrade=True)
    assert read_env_file(env)["FADEN_PII_BACKEND"] == "presidio"
    assert downloaded == []


def test_new_cpu_install_and_published_variant(selection):
    env, downloaded = selection
    set_env_values(env, {"VERSION": "1.2.3", "REGISTRY": "registry.example.internal/faden"})
    assert not pii_runtime.deploy_step(env, yes=True, published=True)
    values = read_env_file(env)
    assert downloaded == ["gliner-onnx"]
    assert values["FADEN_PII_IMAGE"] == "registry.example.internal/faden/pii:1.2.3-cpu"
    assert values["FADEN_PII_CPU_THREADS"] == "1"
    assert values["FADEN_PII_CPU_THREADS_MODE"] == "auto"


def test_interactive_gpu_choice_and_noninteractive_preservation(selection, monkeypatch):
    env, downloaded = selection
    device = pii_hardware.PIIGpu("0", "GPU-abcd-1234", "NVIDIA example", "7.5", 8192)
    monkeypatch.setattr(pii_hardware, "detect_support", lambda **kw: pii_hardware.PIIGpuSupport([device]))
    prompts = []
    answers = iter(["europe-west-v1", "gliner2-multi-v1", "gpu-dedicated"])
    monkeypatch.setattr(
        pii_runtime.click, "prompt", lambda *a, **kw: prompts.append(kw["default"]) or next(answers)
    )
    assert pii_runtime.deploy_step(env)
    values = read_env_file(env)
    assert prompts == ["europe-west-v1", "auto", "cpu"]
    assert values["FADEN_PII_GPU_ID"] == device.uuid
    assert values["FADEN_PII_EXECUTION_PROFILE"] == "gpu-dedicated"
    pii_runtime.deploy_step(env, yes=True, upgrade=True)
    assert len(prompts) == 3
    assert downloaded == ["gliner2"]


@pytest.mark.parametrize(
    ("available", "cpu", "gpu"), [(1, 1, 1), (2, 1, 1), (8, 2, 2), (16, 4, 2), (64, 8, 2)]
)
def test_thread_defaults_are_bounded(available, cpu, gpu):
    assert sysinfo.pii_threads(available) == cpu <= available
    assert sysinfo.pii_threads(available, gpu=True) == gpu <= available
