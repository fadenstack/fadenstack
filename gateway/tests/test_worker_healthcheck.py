"""The gateway's workers get time before uvicorn's supervisor kills them as hung.

uvicorn kills a worker that does not answer its supervisor's ping within ``timeout_worker_healthcheck`` (5 s by
default). A worker serving its first chat sometimes stalled that long: it was killed and the reply it was writing
was lost (test server, 0.7.0rc3 and rc4).
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
import uvicorn

from faden_gateway import __main__ as gateway_main


def test_the_supervisor_waits_longer_than_uvicorns_default(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    monkeypatch.setattr(gateway_main.uvicorn, "run", lambda *_args, **kwargs: seen.update(kwargs))
    monkeypatch.setattr(gateway_main, "set_multiproc_dir", lambda: None)
    monkeypatch.setattr(gateway_main, "_build_uvicorn_ssl_kwargs", dict)
    monkeypatch.setattr("faden_gateway.published_secrets.refuse_published_secrets", lambda _settings: None)

    gateway_main.main()

    default = inspect.signature(uvicorn.Config.__init__).parameters["timeout_worker_healthcheck"].default
    assert seen["timeout_worker_healthcheck"] == gateway_main.WORKER_HEALTHCHECK_SEC == 30
    assert seen["timeout_worker_healthcheck"] > default
