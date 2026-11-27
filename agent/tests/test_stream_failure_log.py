"""Expected network failures are one WARNING line; anything else keeps its traceback.

Live run on .22: the Mac's agent started 6 s after boot, before Wi-Fi was up,
and logged ``OSError: [Errno 51] Network is unreachable`` as an ERROR with a
full traceback, twice, then connected. A later ``ConnectionClosedError: no close
frame received or sent`` did the same, then it reconnected 8 s later. The
retry was right; the tracebacks read like a fault.
"""

from __future__ import annotations

import errno
import logging
import os
import socket

import httpx
import pytest
from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close

from faden_agent.service import log_stream_failure, transient_reason


def _os(code: int) -> OSError:
    return OSError(code, os.strerror(code))


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (_os(errno.ENETUNREACH), "network is unreachable"),
        (_os(errno.ENETDOWN), "network is down"),
        (_os(errno.EHOSTUNREACH), "host is unreachable"),
        (ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused"), "connection refused"),
        (ConnectionResetError(errno.ECONNRESET, "Connection reset by peer"), "connection reset"),
        (socket.gaierror(socket.EAI_NONAME, "nodename nor servname provided"), "could not be resolved"),
        (TimeoutError(), "timed out"),
        (httpx.ConnectTimeout("connect timed out"), "timed out"),
        (ConnectionClosedError(None, None), "without a close frame"),
    ],
)
def test_expected_network_failures_have_a_reason(exc: BaseException, expected: str) -> None:
    reason = transient_reason(exc)
    assert reason is not None and expected in reason


def test_a_socket_error_under_an_http_error_is_found() -> None:
    try:
        try:
            raise _os(errno.ENETUNREACH)
        except OSError as inner:
            raise httpx.ConnectError("All connection attempts failed") from inner
    except httpx.ConnectError as outer:
        assert transient_reason(outer) is not None and "network is unreachable" in transient_reason(outer)


@pytest.mark.parametrize(
    "exc",
    [
        ValueError("bad payload"),
        KeyError("credential"),
        _os(errno.EACCES),
        ConnectionClosedError(Close(1011, "internal error"), Close(1011, "internal error"), True),
        httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED]"),
    ],
)
def test_anything_else_is_not_transient(exc: BaseException) -> None:
    assert transient_reason(exc) is None


def test_a_transient_failure_is_one_warning_without_a_traceback(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="faden_agent.service"):
        try:
            raise _os(errno.ENETUNREACH)
        except OSError as exc:
            log_stream_failure(exc, 2.0)
    [record] = caplog.records
    assert record.levelno == logging.WARNING
    assert record.exc_info is None
    message = record.getMessage()
    assert message.startswith("Node stream unavailable: network is unreachable ([Errno ")
    assert message.endswith("; retrying in 2 s.")
    assert "Traceback" not in caplog.text


def test_an_unexpected_failure_keeps_error_and_traceback(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="faden_agent.service"):
        try:
            raise ValueError("bad payload")
        except ValueError as exc:
            log_stream_failure(exc, 4.0)
    [record] = caplog.records
    assert record.levelno == logging.ERROR
    assert record.getMessage() == "Node stream loop failed."
    assert record.exc_info is not None
    assert "Traceback" in caplog.text and "ValueError: bad payload" in caplog.text
