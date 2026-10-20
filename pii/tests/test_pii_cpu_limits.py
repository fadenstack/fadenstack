"""The service rechecks container limits, even if installer settings are larger."""

import pytest

from faden_pii import cpu
from faden_pii.settings import Settings


@pytest.mark.parametrize(
    ("available", "expected"), [(1, 1), (2, 1), (8, 2), (16, 4), (64, 8)]
)
def test_cpu_default_uses_available_thread_budget(monkeypatch, available, expected):
    monkeypatch.setattr(cpu, "available_threads", lambda: available)
    assert Settings(pii_backend="gliner-onnx").pii_cpu_threads == expected
    assert Settings(pii_cpu_threads=64).pii_cpu_threads == available


def test_cgroup_quota_limits_affinity(monkeypatch):
    monkeypatch.setattr(cpu.os, "process_cpu_count", lambda: 64, raising=False)
    monkeypatch.setattr(
        cpu.os, "sched_getaffinity", lambda _: set(range(8)), raising=False
    )

    def read(path):
        if path.name == "cpu.max":
            return "200000 100000"
        raise FileNotFoundError()

    monkeypatch.setattr(cpu.Path, "read_text", read)
    assert cpu.available_threads() == 2
