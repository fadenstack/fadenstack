"""A Ray deployment's log page is one Loki query, and the choice on the page is the query.

The page used to read Loki first -- ignoring which source and machine it
asked for -- and fall back to a command that had a machine tail its files.
The engine's own output, where a failed start's root cause is, was read by
neither.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from faden_backend.services.inference.drivers.ray import logs
from faden_backend.services.inference.drivers.ray.logs import (
    RayLogReader,
    _lines_from_loki,
    engine_root_cause,
    log_selector,
)
from faden_backend.services.inference.observability import LogSource

APP = "faden-534e4266-257f-407d-b5b7-b4bb19fad198"
HOSTS = ["10.250.10.49", "10.250.10.71"]


# ── the selector ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("source", "hosts", "expected"),
    [
        (LogSource.SERVE_REPLICA, HOSTS,
         '{job="ray-serve", app="%s", source!="engine", host=~"10\\\\.250\\\\.10\\\\.49|10\\\\.250\\\\.10\\\\.71"}' % APP),
        (LogSource.ENGINE, ["10.250.10.71"], '{job="ray-serve", app="%s", source="engine", host="10.250.10.71"}' % APP),
        (LogSource.AGENT, ["10.250.10.49"], '{job="node-agent", host="10.250.10.49"}'),
    ],
    ids=["replicas-on-every-machine", "engine-on-one", "agent-of-one"],
)
def test_the_source_and_machine_chosen_are_the_query(source: LogSource, hosts: list[str], expected: str) -> None:
    assert log_selector(source, app_name=APP, hosts=hosts) == expected


def test_a_replica_narrows_the_replica_page() -> None:
    assert 'replica="wib8cvxx"' in log_selector(LogSource.SERVE_REPLICA, app_name=APP, hosts=[], replica_id="wib8cvxx")


# ── reading ─────────────────────────────────────────────────────────────────


def _loki(*streams: tuple[dict[str, str], list[tuple[int, str]]]) -> dict[str, Any]:
    return {"data": {"result": [
        {"stream": labels, "values": [[str(ts), line] for ts, line in values]} for labels, values in streams
    ]}}


def test_lines_carry_their_machine_and_read_oldest_first() -> None:
    lines = _lines_from_loki(_loki(
        ({"host": "10.250.10.71", "level": "error"}, [(2_000, "(EngineCore pid=1) ValueError: no")]),
        ({"host": "10.250.10.49", "level": "info"}, [(1_000, '{"levelname": "INFO", "message": "Started"}')]),
    ))
    assert [(line.host, line.level, line.message) for line in lines] == [
        ("10.250.10.49", "INFO", "Started"),
        ("10.250.10.71", "ERROR", "(EngineCore pid=1) ValueError: no"),
    ]


@pytest.mark.anyio()
async def test_a_page_says_when_loki_cannot_be_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    async def down(*_: Any, **__: Any) -> dict[str, Any]:
        raise RuntimeError("Loki connection failed")

    monkeypatch.setattr(logs, "_query", down)
    page = await RayLogReader().read(SimpleNamespace(id=uuid.uuid4()), source=LogSource.ENGINE, app_name=APP, hosts=HOSTS)
    assert page.lines == [] and "Loki could not be asked" in (page.detail or "")


@pytest.mark.anyio()
async def test_the_old_source_name_reads_the_replicas(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []

    async def query(selector: str, **_: Any) -> dict[str, Any]:
        asked.append(selector)
        return _loki()

    monkeypatch.setattr(logs, "_query", query)
    page = await RayLogReader().read(
        SimpleNamespace(id=uuid.uuid4()), source=LogSource.RUNTIME_CONTAINER, app_name=APP, hosts=HOSTS, tail=10,
    )
    assert page.source == LogSource.SERVE_REPLICA and 'source!="engine"' in asked[0]
    assert page.detail == "nothing logged here in the last 24 hours"


# ── the root cause of a failed start ───────────────────────────────────────


def _failed_start(*lines: tuple[int, str]) -> dict[str, Any]:
    return _loki(({"host": "10.250.10.71", "source": "engine"}, list(lines)))


NOW_NS = int(datetime.now(tz=UTC).timestamp() * 1_000_000_000)


@pytest.mark.anyio()
async def test_the_root_cause_is_the_engine_s_last_exception_not_serve_s_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Qwen3.8 on vLLM 0.29, 2026-09-30: Serve only said "See root cause above"."""
    async def query(selector: str, **_: Any) -> dict[str, Any]:
        assert 'source="engine"' in selector
        return _failed_start(
            (NOW_NS - 3_000, "(EngineCore pid=13728) ERROR 09-30 06:44:12 [core.py:1349] EngineCore failed to start."),
            (NOW_NS - 2_000, "(EngineCore pid=13728) ValueError: There is no module or parameter named "
                             "'ngram_embedding.weight_scale' in Qwen4ExpNGramEmbedding"),
            (NOW_NS - 1_000, "RuntimeError: Engine core initialization failed. See root cause above. Failed core proc(s): {}"),
        )

    monkeypatch.setattr(logs, "_query", query)
    failure = await engine_root_cause(APP)
    assert failure is not None and failure.cause == (
        "ValueError: There is no module or parameter named 'ngram_embedding.weight_scale' "
        "in Qwen4ExpNGramEmbedding (on 10.250.10.71)"
    )
    assert failure.start_failed and failure.recurs, "loading the same weights again fails again"


@pytest.mark.anyio()
async def test_a_failure_that_can_pass_is_left_to_ray_s_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Memory a stopping copy still holds is freed by the next attempt."""
    async def query(selector: str, **_: Any) -> dict[str, Any]:
        return _failed_start(
            (NOW_NS - 2_000, "(EngineCore pid=1) ERROR 09-30 06:44:12 [core.py:1349] EngineCore failed to start."),
            (NOW_NS - 1_000, "(EngineCore pid=1) ValueError: Free memory on device cuda:0 (1.6/121.69 GiB) on startup "
                             "is less than desired GPU memory utilization"),
        )

    monkeypatch.setattr(logs, "_query", query)
    failure = await engine_root_cause(APP)
    assert failure is not None and failure.start_failed and not failure.recurs


@pytest.mark.anyio()
async def test_an_exception_the_engine_lived_through_is_not_a_failed_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """The engine logs tracebacks it recovers from; without "failed to start" nothing is stopped."""
    async def query(selector: str, **_: Any) -> dict[str, Any]:
        return _failed_start((NOW_NS - 1_000, "(EngineCore pid=1) ModuleNotFoundError: No module named 'hf_transfer'"))

    monkeypatch.setattr(logs, "_query", query)
    failure = await engine_root_cause(APP)
    assert failure is not None and not failure.start_failed and not failure.recurs


@pytest.mark.anyio()
async def test_the_cause_is_the_exception_after_the_failed_start_not_one_lived_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exception printed before "failed to start" would stop a start that can pass."""
    async def query(selector: str, **_: Any) -> dict[str, Any]:
        return _failed_start(
            (NOW_NS - 4_000, "(EngineCore pid=1) ModuleNotFoundError: No module named 'hf_transfer'"),
            (NOW_NS - 3_000, "(EngineCore pid=1) ERROR 09-30 06:44:12 [core.py:1349] EngineCore failed to start."),
            (NOW_NS - 2_000, "(EngineCore pid=1) torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 2.00 GiB"),
            (NOW_NS - 1_000, "RuntimeError: Engine core initialization failed. See root cause above. Failed core proc(s): {}"),
        )

    monkeypatch.setattr(logs, "_query", query)
    failure = await engine_root_cause(APP)
    assert failure is not None and failure.cause.startswith("torch.OutOfMemoryError: CUDA out of memory")
    assert failure.start_failed and not failure.recurs


@pytest.mark.anyio()
async def test_an_earlier_run_s_failure_is_not_this_one_s(monkeypatch: pytest.MonkeyPatch) -> None:
    async def query(selector: str, **_: Any) -> dict[str, Any]:
        return _failed_start(
            (NOW_NS - 10_000_000_000, "(EngineCore pid=1) EngineCore failed to start."),
            (NOW_NS - 9_000_000_000, "(EngineCore pid=1) ValueError: from the run before"),
        )

    monkeypatch.setattr(logs, "_query", query)
    assert await engine_root_cause(APP, after=datetime.now(tz=UTC) - timedelta(seconds=1)) is None


def test_the_whole_deployment_is_one_stream_and_each_line_says_where_from() -> None:
    assert log_selector(LogSource.ALL, app_name=APP, hosts=[]) == f'{{job="ray-serve", app="{APP}"}}'
    lines = _lines_from_loki(_loki(
        ({"host": "10.250.10.71", "source": "engine"}, [(2_000, "vLLM")]),
        ({"host": "10.250.10.49"}, [(1_000, '{"message": "Serve, shipped before the source label"}')]),
    ))
    assert [(line.source, line.host) for line in lines] == [("replica", "10.250.10.49"), ("engine", "10.250.10.71")]


@pytest.mark.anyio()
async def test_no_cause_found_leaves_serve_s_message(monkeypatch: pytest.MonkeyPatch) -> None:
    async def down(*_: Any, **__: Any) -> dict[str, Any]:
        raise RuntimeError("no Loki")

    monkeypatch.setattr(logs, "_query", down)
    assert await engine_root_cause(APP) is None
