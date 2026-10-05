"""Let Ray Serve 2.58 see the machines being drained (installed into the runtime container).

Ray Serve moves a model's copies off a machine that is being drained: it
starts a replacement on another machine, waits until it serves, and only then
stops the copy on the draining one. In every released Ray up to 2.59 that
code never runs: the Serve controller's node cache answers "no machine is
draining" whatever Ray says (``DefaultClusterNodeInfoCache.get_draining_nodes``
returns ``{}``). ray-project/ray#65502 fixes that with a new GCS call; this
reads the same fact from a call Ray 2.58 already has -- the autoscaler's
cluster resource state, whose node states say ``DRAINING``.

This file is copied into the runtime container's site-packages by the
Fadenstack agent (``ray/drain.py``) with a ``.pth`` line that calls
:func:`install` in every Python process there. That only registers an import
hook: the patch is applied when Serve's node cache module is imported, which
the Serve controller does, and nothing else is touched. A Ray that carries the
upstream fix (the base class answers itself) is left alone.

The controller records that it runs patched (``KV_KEY``, with its pid), so the
agent can tell a controller started before the file was there from one that
loaded it.
"""

from __future__ import annotations

import importlib.abc
import json
import os
import sys
import time
from typing import Any

VERSION = 1
TARGET = "ray.serve._private.cluster_node_info_cache"
KV_KEY = b"faden_serve_drain"
KV_NAMESPACE = b"faden"
#: GCS has no deadline for a node drained without one; Serve then must never stop copies early.
NO_DEADLINE = 2**63 - 1
#: How often the controller asks GCS which machines drain (its loop runs about every 0.1 s).
REFRESH_SEC = 2.0


def read_draining(gcs_client: Any, alive: Any) -> dict[str, int]:
    """Machines GCS reports as draining, ``{node id hex: deadline}``, among *alive* ones."""
    from ray.core.generated.autoscaler_pb2 import (  # noqa: PLC0415 - only in the container
        GetClusterResourceStateReply,
        NodeStatus,
    )

    reply = GetClusterResourceStateReply()
    reply.ParseFromString(gcs_client.get_cluster_resource_state(timeout_s=5))
    draining = NodeStatus.Value("DRAINING")
    out: dict[str, int] = {}
    for node in reply.cluster_resource_state.node_states:
        if node.status != draining:
            continue
        node_id = node.node_id.hex()
        if not alive or node_id in alive:
            out[node_id] = NO_DEADLINE
    return out


def _announce(cache: Any) -> None:
    """Record once per process that this controller runs with the patch."""
    if getattr(cache, "_faden_drain_announced", False):
        return
    cache._faden_drain_announced = True
    try:
        value = json.dumps({"version": VERSION, "pid": os.getpid()}).encode()
        cache._gcs_client.internal_kv_put(KV_KEY, value, True, KV_NAMESPACE)
    except Exception:  # noqa: BLE001 - a missing marker only makes the agent restart the controller again
        pass


def apply(module: Any) -> bool:
    """Patch Serve's node cache *module*; ``False`` when there is nothing to patch."""
    base = getattr(module, "ClusterNodeInfoCache", None)
    cache_cls = getattr(module, "DefaultClusterNodeInfoCache", None)
    if base is None or cache_cls is None or getattr(cache_cls, "_faden_drain", None):
        return False
    if "get_draining_nodes" not in cache_cls.__dict__:
        # The upstream fix moved the answer into the base class: Ray knows by itself.
        return False

    original_update = base.update

    def update(self: Any) -> None:
        original_update(self)
        now = time.monotonic()
        if now - getattr(self, "_faden_drain_at", 0.0) >= REFRESH_SEC:
            self._faden_drain_at = now
            alive = getattr(self, "_alive_node_id_set", None)
            try:
                self._faden_draining = read_draining(self._gcs_client, alive)
            except Exception:  # noqa: BLE001 - keep the last answer for machines still alive
                previous = getattr(self, "_faden_draining", None) or {}
                self._faden_draining = {k: v for k, v in previous.items() if not alive or k in alive}
        _announce(self)

    def get_draining_nodes(self: Any) -> dict[str, int]:
        return dict(getattr(self, "_faden_draining", None) or {})

    cache_cls.update = update
    cache_cls.get_draining_nodes = get_draining_nodes
    cache_cls._faden_drain = VERSION
    return True


class _Loader(importlib.abc.Loader):
    """Runs the real loader, then patches what it loaded."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def create_module(self, spec: Any) -> Any:
        return self._inner.create_module(spec)

    def exec_module(self, module: Any) -> None:
        self._inner.exec_module(module)
        try:
            apply(module)
        except Exception:  # noqa: BLE001 - never break Serve over the patch
            pass


class _Finder(importlib.abc.MetaPathFinder):
    """Finds Serve's node cache module the usual way, with :class:`_Loader` around its loader."""

    def find_spec(self, name: str, path: Any, target: Any = None) -> Any:
        if name != TARGET:
            return None
        for finder in sys.meta_path:
            if finder is self or not hasattr(finder, "find_spec"):
                continue
            spec = finder.find_spec(name, path, target)
            if spec is not None:
                if spec.loader is not None and hasattr(spec.loader, "exec_module"):
                    spec.loader = _Loader(spec.loader)
                return spec
        return None


def install() -> None:
    """Patch Serve's node cache now if it is loaded, or when it is (from the ``.pth`` line)."""
    try:
        if TARGET in sys.modules:
            apply(sys.modules[TARGET])
            return
        if not any(isinstance(f, _Finder) for f in sys.meta_path):
            sys.meta_path.insert(0, _Finder())
    except Exception:  # noqa: BLE001 - a Python process must start whatever happens here
        pass
