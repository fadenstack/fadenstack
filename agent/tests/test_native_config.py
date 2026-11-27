"""The native settings parse forgivingly: a typo must not stop a (Linux) agent from starting."""

from __future__ import annotations

import logging

import pytest

from faden_agent.config import AgentConfig, _env_count


@pytest.mark.parametrize("raw", ["", "abc", "0", "-1", "1.5", "999"])
def test_a_bad_worker_count_warns_and_keeps_one(monkeypatch: pytest.MonkeyPatch, caplog, raw: str) -> None:
    monkeypatch.setenv("FADEN_NODE_AGENT_NATIVE_MAX_WORKERS", raw)
    with caplog.at_level(logging.WARNING):
        assert _env_count("FADEN_NODE_AGENT_NATIVE_MAX_WORKERS", 1) == 1
    assert (raw == "") or "NATIVE_MAX_WORKERS" in caplog.text


def test_a_good_worker_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FADEN_NODE_AGENT_NATIVE_MAX_WORKERS", " 2 ")
    assert _env_count("FADEN_NODE_AGENT_NATIVE_MAX_WORKERS", 1) == 2


def test_the_config_loads_with_a_typo_and_sandbox_defaults_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FADEN_NODE_AGENT_NATIVE_MAX_WORKERS", "two")
    monkeypatch.delenv("FADEN_NODE_AGENT_NATIVE_SANDBOX", raising=False)
    config = AgentConfig.from_env()
    assert config.native_max_workers == 1 and config.native_sandbox is True
