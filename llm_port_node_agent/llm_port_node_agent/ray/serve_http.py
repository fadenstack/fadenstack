"""Where a cluster's Serve proxy listens, and shutting Serve down -- asked of Ray inside the runtime container.

The address Serve's HTTP proxy binds is set once, when Serve starts on the
cluster; ``serve.start`` with other options later is a no-op. So before the
agent puts the model-TLS proxy in front of Serve it asks what Serve actually
binds: a proxy already on ``0.0.0.0`` at the published port (a cluster from
before model TLS) cannot be fronted until Serve is started again, which is
what ``shutdown`` is for -- the backend then applies its applications anew,
and the first one starts Serve on the loopback address.

Both run as scripts fed to the container's Python (``python3 -``), attached
with the token the container was started with, like ``inspect.py``.
``get_http_config`` is the Serve controller's own accessor (Ray 2.58).
"""

from __future__ import annotations

import json
from typing import Any

_ATTACH = r'''
import json, sys, logging, warnings
warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)
try:
    import ray
    ray.init(address="auto", logging_level="ERROR", log_to_driver=False)
except Exception as exc:
    print(json.dumps({"attached": False, "error": "attach: %s" % exc}))
    sys.exit(0)
'''

READ_SCRIPT = _ATTACH + r'''
try:
    from ray.serve.context import _get_global_client
    client = _get_global_client(raise_if_no_controller_running=False)
except Exception as exc:
    print(json.dumps({"attached": True, "error": "serve client: %s" % exc}))
    sys.exit(0)
if client is None:
    print(json.dumps({"attached": True, "running": False}))
    sys.exit(0)
try:
    cfg = ray.get(client._controller.get_http_config.remote(), timeout=30)
    print(json.dumps({"attached": True, "running": True, "host": cfg.host, "port": int(cfg.port)}))
except Exception as exc:
    print(json.dumps({"attached": True, "running": True, "error": "http config: %s" % exc}))
'''

SHUTDOWN_SCRIPT = _ATTACH + r'''
try:
    from ray import serve
    serve.shutdown()
    print(json.dumps({"attached": True, "shut_down": True}))
except Exception as exc:
    print(json.dumps({"attached": True, "shut_down": False, "error": "%s" % exc}))
'''

LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


def parse(stdout: str) -> dict[str, Any] | None:
    """The script's JSON line, among whatever Ray printed around it."""
    for line in reversed((stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                doc = json.loads(line)
            except ValueError:
                continue
            if isinstance(doc, dict):
                return doc
    return None
