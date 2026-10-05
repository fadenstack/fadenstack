"""Draining a machine out of a Ray cluster, run inside the runtime container.

Draining moves a model's copies off one machine and keeps new ones from
landing there. Ray does half of that by itself: a machine drained in GCS
(``DrainNode``, as ``ray drain-node`` does) gets no new work. Serve does the
other half -- start a replacement elsewhere, wait until it serves, then stop
the copy on the draining machine -- once it can see the drain, which in Ray
2.58 takes the patch in ``serve_drain_patch.py``. Four steps, each a short
script fed to the container's Python (as ``inspect.py`` does):

``ENABLE_SCRIPT``
    On the head: put the patch into site-packages, and restart Serve's
    controller if the one running started without it (it is a detached actor
    Ray restarts at once; the copies keep serving meanwhile).
``DRAIN_SCRIPT``
    On the machine to drain: drain this node in GCS, without a deadline, so
    nothing is ever stopped before its replacement serves.
``PLACEMENT_SCRIPT``
    On the head: every machine with its state and free resources, and every
    Serve copy with the resources its placement group holds and on which
    machines -- what the server needs to say which copies can move, and to
    follow them as they do.
``STOP_SCRIPT``
    On the head: stop the named copies, for those that have nowhere to go
    and the operator agreed to stop. Serve keeps asking for their
    replacements, which start when room appears.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from faden_agent.ray import serve_drain_patch

BEGIN, END = "__DRAIN_BEGIN__", "__DRAIN_END__"
#: The patch's file names in the container's site-packages.
MODULE_NAME = "faden_serve_drain.py"
PTH_NAME = "faden_serve_drain.pth"
PTH_LINE = "import faden_serve_drain as _faden_serve_drain; _faden_serve_drain.install()\n"
#: Why a machine is drained, as Ray records it.
DRAIN_MESSAGE = "Drained in Fadenstack: its model copies move to the other machines."

_PRELUDE = r'''
import json, os, sys, time, logging, warnings
warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)
out = {"errors": []}

def done():
    print("__DRAIN_BEGIN__" + json.dumps(out, default=str) + "__DRAIN_END__")
    sys.exit(0)

def attach():
    import ray
    out["ray_version"] = ray.__version__
    try:
        ray.init(address="auto", logging_level="ERROR", log_to_driver=False, namespace="serve")
    except Exception as exc:
        out["attached"] = False
        out["error"] = "attach: %s" % exc
        done()
    out["attached"] = True
    return ray

def marker(ray):
    try:
        raw = ray._private.worker.global_worker.gcs_client.internal_kv_get(
            __KV_KEY__, __KV_NAMESPACE__)
        return json.loads(raw) if raw else None
    except Exception:
        return None

def controller(ray):
    try:
        return ray.get_actor("SERVE_CONTROLLER_ACTOR", namespace="serve")
    except Exception:
        return None

def controller_pid(ray, handle):
    try:
        from ray._private import state as ray_state
        info = ray_state.actors(actor_id=handle._actor_id.hex())
        if info.get("State") != "ALIVE":
            return None
        return int(info.get("Pid") or 0) or None
    except Exception:
        return None

def patch_active(ray):
    handle = controller(ray)
    if handle is None:
        return None
    pid = controller_pid(ray, handle)
    m = marker(ray)
    return bool(m and pid and m.get("pid") == pid and int(m.get("version") or 0) >= __VERSION__)
'''


def _prelude() -> str:
    return (
        _PRELUDE.replace("__KV_KEY__", repr(serve_drain_patch.KV_KEY))
        .replace("__KV_NAMESPACE__", repr(serve_drain_patch.KV_NAMESPACE))
        .replace("__VERSION__", str(serve_drain_patch.VERSION))
    )


_ENABLE = r'''
MODULE = json.loads(__MODULE__)
PTH = json.loads(__PTH__)
WAIT_SEC = __WAIT__
RESTART = __RESTART__
try:
    import ray
    out["ray_version"] = ray.__version__
    site = os.path.dirname(os.path.dirname(os.path.abspath(ray.__file__)))
    changed = False
    for name, text in ((__MODULE_NAME__, MODULE), (__PTH_NAME__, PTH)):
        path = os.path.join(site, name)
        try:
            with open(path, encoding="utf-8") as f:
                current = f.read()
        except OSError:
            current = None
        if current != text:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, path)
            changed = True
    out["installed"] = True
    out["changed"] = changed
except Exception as exc:
    out["installed"] = False
    out["error"] = "install: %s" % exc
    done()

ray = attach()
active = patch_active(ray)
if active is None:
    # No Serve yet: the controller started later loads the file by itself.
    out["serve_running"] = False
    out["active"] = None
    done()
out["serve_running"] = True
if active:
    out["active"] = True
    done()
if not RESTART:
    out["active"] = False
    done()
ray.kill(controller(ray), no_restart=False)
out["restarted"] = True
deadline = time.time() + WAIT_SEC
while time.time() < deadline:
    time.sleep(2)
    if patch_active(ray):
        out["active"] = True
        done()
out["active"] = False
out["error"] = "Serve's controller did not come back with the drain patch within %d s" % WAIT_SEC
done()
'''

_DRAIN = r'''
ray = attach()
try:
    from ray.core.generated import autoscaler_pb2
    node_id = ray.get_runtime_context().get_node_id()
    out["node_id"] = node_id
    gcs = ray._private.worker.global_worker.gcs_client
    accepted, message = gcs.drain_node(
        node_id,
        autoscaler_pb2.DrainNodeReason.Value("DRAIN_NODE_REASON_PREEMPTION"),
        __MESSAGE__,
        0,
    )
    out["accepted"] = bool(accepted)
    if not accepted:
        out["error"] = "Ray refused the drain: %s" % message
except Exception as exc:
    out["accepted"] = False
    out["error"] = "drain: %s" % exc
done()
'''

_PLACEMENT = r'''
ray = attach()
try:
    from ray.core.generated.autoscaler_pb2 import GetClusterResourceStateReply, NodeStatus
    reply = GetClusterResourceStateReply()
    reply.ParseFromString(
        ray._private.worker.global_worker.gcs_client.get_cluster_resource_state(timeout_s=10))
    out["nodes"] = [
        {
            "node_id": n.node_id.hex(),
            "ip": n.node_ip_address,
            "status": NodeStatus.Name(n.status),
            "total": {k: float(v) for k, v in n.total_resources.items()},
            "available": {k: float(v) for k, v in n.available_resources.items()},
        }
        for n in reply.cluster_resource_state.node_states
    ]
except Exception as exc:
    out["errors"].append("nodes: %s" % exc)

groups = {}
try:
    for info in ray.util.placement_group_table().values():
        if info.get("state") == "REMOVED" or not info.get("name"):
            continue
        groups[info["name"]] = info
except Exception as exc:
    out["errors"].append("placement groups: %s" % exc)

replicas = []
try:
    from ray.serve.context import _get_global_client
    client = _get_global_client(raise_if_no_controller_running=False)
    details = client.get_serve_details() if client is not None else {}
    out["serve_running"] = client is not None
    for app_name, app in (details.get("applications") or {}).items():
        for dep_name, dep in (app.get("deployments") or {}).items():
            for r in dep.get("replicas") or []:
                group = groups.get(r.get("actor_name") or "")
                bundles = []
                if group:
                    where = group.get("bundles_to_node_id") or {}
                    for index, resources in sorted((group.get("bundles") or {}).items()):
                        bundles.append({
                            "node_id": where.get(index) or None,
                            "resources": {k: float(v) for k, v in (resources or {}).items()},
                        })
                replicas.append({
                    "app": app_name,
                    "deployment": dep_name,
                    "replica_id": r.get("replica_id"),
                    "actor_name": r.get("actor_name"),
                    "state": r.get("state"),
                    "node_id": r.get("node_id"),
                    "strategy": (group or {}).get("strategy"),
                    "bundles": bundles,
                })
except Exception as exc:
    out["errors"].append("serve: %s" % exc)
out["replicas"] = replicas
out["drain_patch_active"] = patch_active(ray)
done()
'''

_STOP = r'''
ray = attach()
stopped, missing = [], []
for name in json.loads(__NAMES__):
    try:
        ray.kill(ray.get_actor(name, namespace="serve"), no_restart=True)
        stopped.append(name)
    except ValueError:
        missing.append(name)
    except Exception as exc:
        out["errors"].append("%s: %s" % (name, exc))
out["stopped"] = stopped
out["missing"] = missing
done()
'''


def patch_source() -> str:
    """The patch module's text, as it is copied into the container.

    Read from beside the module: in the one-file binary the module itself is
    bytecode, and ``node_agent.spec`` ships the source file next to it.
    """
    path = Path(serve_drain_patch.__file__).with_name("serve_drain_patch.py")
    return path.read_text(encoding="utf-8")


def enable_script(*, wait_sec: int = 90, restart: bool = True) -> str:
    """Install the Serve drain patch on the head, restarting the controller if it lacks it.

    Without *restart* the files are only put in place: a controller started
    afterwards loads them, and one already running is left as it is.
    """
    return _prelude() + (
        _ENABLE.replace("__MODULE__", repr(json.dumps(patch_source())))
        .replace("__PTH__", repr(json.dumps(PTH_LINE)))
        .replace("__WAIT__", str(int(wait_sec)))
        .replace("__RESTART__", "True" if restart else "False")
        .replace("__MODULE_NAME__", repr(MODULE_NAME))
        .replace("__PTH_NAME__", repr(PTH_NAME))
    )


def drain_script() -> str:
    """Drain the machine this runs on, in GCS, without a deadline."""
    return _prelude() + _DRAIN.replace("__MESSAGE__", repr(DRAIN_MESSAGE))


def placement_script() -> str:
    """Machines and Serve copies, with where each copy's resources are held."""
    return _prelude() + _PLACEMENT


def stop_script(actor_names: list[str]) -> str:
    """Stop the Serve copies named *actor_names*."""
    return _prelude() + _STOP.replace("__NAMES__", repr(json.dumps(list(actor_names))))


def parse(stdout: str) -> dict[str, Any] | None:
    """The script's document from its output (Ray prints lines of its own)."""
    text = stdout or ""
    start, end = text.rfind(BEGIN), text.rfind(END)
    if start < 0 or end < start:
        return None
    try:
        doc = json.loads(text[start + len(BEGIN):end])
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None
