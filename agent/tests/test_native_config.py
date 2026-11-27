"""The native settings parse forgivingly: a typo must not stop a (Linux) agent from starting."""

from __future__ import annotations

import logging

import pytest

from faden_agent.config import AgentConfig, _env_bool, _env_count, _env_strict_switch


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


@pytest.mark.parametrize(
    ("raw", "value", "error"),
    [
        (None, True, False),
        ("1", True, False), ("true", True, False), ("YES", True, False), (" on ", True, False),
        ("0", False, False), ("false", False, False), ("No", False, False), ("off", False, False),
        ("", True, True), ("   ", True, True), ("ture", True, True), ("y", True, True), ("n", True, True),
        ("disable", True, True), ("2", True, True),
    ],
)
def test_the_sandbox_switch_is_strict(monkeypatch: pytest.MonkeyPatch, raw, value, error) -> None:
    if raw is None:
        monkeypatch.delenv("FADEN_NODE_AGENT_NATIVE_SANDBOX", raising=False)
    else:
        monkeypatch.setenv("FADEN_NODE_AGENT_NATIVE_SANDBOX", raw)
    got, why = _env_strict_switch("FADEN_NODE_AGENT_NATIVE_SANDBOX", True)
    assert got is value and (why is not None) is error
    config = AgentConfig.from_env()
    assert config.native_sandbox is value and (config.native_sandbox_error is not None) is error


def test_other_flags_keep_their_lenient_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    # Linux behaviour unchanged: _env_bool still reads anything unknown as off.
    monkeypatch.setenv("SOME_FLAG", "ture")
    assert _env_bool("SOME_FLAG", True) is False
    monkeypatch.setenv("SOME_FLAG", "y")
    assert _env_bool("SOME_FLAG", False) is True
