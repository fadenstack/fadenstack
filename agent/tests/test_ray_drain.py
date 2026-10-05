"""Draining a machine out of its Ray cluster: the Serve patch and the in-container scripts.

The scripts run here against a stand-in ``ray`` package (the real one lives
only in the runtime image), shaped after Ray 2.58: GCS answers the cluster
resource state as a serialized ``GetClusterResourceStateReply``, Serve's
node cache answers "nothing drains" until patched, and a copy's placement
group is named after its actor.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import types
from pathlib import Path
from typing import Any

import pytest

from faden_agent.ray import drain, serve_drain_patch
from faden_agent.ray.container import RayContainerRuntime

HEAD, WORKER = "aa" * 14, "bb" * 14

_FAKE_RAY = {
    "ray/__init__.py": """
        import json, os
        __version__ = "2.58.0"
        from ray import _private, util, core, serve
        KILLED = []
        def init(**kw): pass
        class _Ctx:
            def get_node_id(self): return os.environ.get("FAKE_NODE", "__WORKER__")
        def get_runtime_context(): return _Ctx()
        class _Handle:
            def __init__(self, name): self.name = name; self._actor_id = type("A", (), {"hex": lambda s: "c0ffee"})()
        def get_actor(name, namespace=None):
            if name == "SERVE_CONTROLLER_ACTOR":
                if os.environ.get("FAKE_SERVE") != "1": raise ValueError("no controller")
                return _Handle(name)
            if name.startswith("gone"): raise ValueError("Failed to look up actor")
            return _Handle(name)
        def kill(handle, no_restart=True):
            print("KILLED %s %s" % (handle.name, no_restart))
    """,
    "ray/_private/__init__.py": "from ray._private import worker, state",
    "ray/_private/worker.py": """
        import json, os
        class _Gcs:
            def internal_kv_get(self, key, namespace):
                return os.environ.get("FAKE_MARKER", "").encode() or None
            def get_cluster_resource_state(self, timeout_s=None):
                return json.dumps({"nodes": [
                    {"id": "__HEAD__", "ip": "10.100.0.2", "status": "RUNNING",
                     "total": {"GPU": 1.0, "CPU": 20.0}, "available": {"GPU": 0.2, "CPU": 18.0}},
                    {"id": "__WORKER__", "ip": "10.100.0.1", "status": "DRAINING",
                     "total": {"GPU": 1.0, "CPU": 20.0}, "available": {"GPU": 0.5, "CPU": 19.0}},
                ]}).encode()
            def drain_node(self, node_id, reason, message, deadline):
                print("DRAIN %s %s %s" % (node_id, reason, deadline))
                return (os.environ.get("FAKE_REFUSE") != "1", "head nodes cannot be drained")
        class _W: gcs_client = _Gcs()
        global_worker = _W()
    """,
    "ray/_private/state.py": """
        import os
        def actors(actor_id=None):
            return {"State": "ALIVE", "Pid": int(os.environ.get("FAKE_PID", "4242"))}
    """,
    "ray/util/__init__.py": """
        def placement_group_table():
            return {
                "pg1": {"name": "SERVE_REPLICA::faden-x#LLMServer:q#a1", "state": "CREATED", "strategy": "PACK",
                        "bundles": {0: {"CPU": 1.0}, 1: {"GPU": 0.5}},
                        "bundles_to_node_id": {0: "__WORKER__", 1: "__WORKER__"}},
                "pg2": {"name": "old", "state": "REMOVED", "bundles": {}, "bundles_to_node_id": {}},
            }
    """,
    "ray/core/__init__.py": "",
    "ray/core/generated/__init__.py": "",
    "ray/core/generated/autoscaler_pb2.py": """
        import json, types
        class _Enum:
            def __init__(self, names): self._names = names
            def Value(self, name): return self._names.index(name)
            def Name(self, number): return self._names[number]
        NodeStatus = _Enum(["UNSPECIFIED", "RUNNING", "DEAD", "IDLE", "DRAINING"])
        DrainNodeReason = _Enum(["DRAIN_NODE_REASON_UNSPECIFIED", "DRAIN_NODE_REASON_IDLE_TERMINATION",
                                 "DRAIN_NODE_REASON_PREEMPTION"])
        class GetClusterResourceStateReply:
            def ParseFromString(self, raw):
                nodes = json.loads(raw)["nodes"]
                self.cluster_resource_state = types.SimpleNamespace(node_states=[
                    types.SimpleNamespace(node_id=bytes.fromhex(n["id"]), node_ip_address=n["ip"],
                                          status=NodeStatus.Value(n["status"]),
                                          total_resources=n["total"], available_resources=n["available"])
                    for n in nodes])
    """,
    "ray/serve/__init__.py": "",
    "ray/serve/context.py": """
        import os
        class _Client:
            def get_serve_details(self):
                return {"applications": {"faden-x": {"deployments": {"LLMServer:q": {"replicas": [
                    {"replica_id": "a1", "actor_name": "SERVE_REPLICA::faden-x#LLMServer:q#a1",
                     "state": "RUNNING", "node_id": "__WORKER__"}]}}}}}
        def _get_global_client(raise_if_no_controller_running=True):
            return _Client() if os.environ.get("FAKE_SERVE") == "1" else None
    """,
    "ray/serve/_private/__init__.py": "",
    "ray/serve/_private/cluster_node_info_cache.py": """
        class ClusterNodeInfoCache:
            def __init__(self, gcs_client):
                self._gcs_client = gcs_client
                self._alive_node_id_set = frozenset()
            def update(self):
                self.updated = getattr(self, "updated", 0) + 1
        class DefaultClusterNodeInfoCache(ClusterNodeInfoCache):
            def get_draining_nodes(self):
                return dict()
    """,
}


@pytest.fixture()
def fake_ray(tmp_path: Path) -> Path:
    for rel, body in _FAKE_RAY.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        text = textwrap.dedent(body).replace("__HEAD__", HEAD).replace("__WORKER__", WORKER)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def _run(fake_ray: Path, script: str, **env: str) -> tuple[dict[str, Any], str]:
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-"], input=script, capture_output=True, text=True,
        env={"PYTHONPATH": str(fake_ray), "SYSTEMROOT": "C:\\Windows", "PATH": "", **env},
        check=False, timeout=60,
    )
    doc = drain.parse(proc.stdout)
    assert doc is not None, proc.stdout + proc.stderr
    return doc, proc.stdout


# --------------------------------------------------------------------------
# The patch
# --------------------------------------------------------------------------


def _cache_module(*, fixed_upstream: bool = False) -> types.ModuleType:
    module = types.ModuleType("cache")

    class ClusterNodeInfoCache:
        def __init__(self, gcs: Any) -> None:
            self._gcs_client = gcs
            self._alive_node_id_set = frozenset({HEAD, WORKER})

        def update(self) -> None:
            self.updated = getattr(self, "updated", 0) + 1

        if fixed_upstream:
            def get_draining_nodes(self) -> dict[str, int]:
                return {"upstream": 1}

    if fixed_upstream:
        class DefaultClusterNodeInfoCache(ClusterNodeInfoCache):
            pass
    else:
        class DefaultClusterNodeInfoCache(ClusterNodeInfoCache):  # type: ignore[no-redef]
            def get_draining_nodes(self) -> dict[str, int]:
                return {}

    module.ClusterNodeInfoCache = ClusterNodeInfoCache
    module.DefaultClusterNodeInfoCache = DefaultClusterNodeInfoCache
    return module


class _Gcs:
    def __init__(self) -> None:
        self.kv: dict[bytes, bytes] = {}

    def internal_kv_put(self, key: bytes, value: bytes, overwrite: bool, namespace: bytes) -> None:
        self.kv[key] = value


def test_the_patched_cache_reports_what_gcs_says_drains(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _cache_module()
    assert serve_drain_patch.apply(module) is True
    calls: list[Any] = []

    def read(gcs: Any, alive: Any) -> dict[str, int]:
        calls.append(alive)
        return {WORKER: serve_drain_patch.NO_DEADLINE}

    monkeypatch.setattr(serve_drain_patch, "read_draining", read)
    gcs = _Gcs()
    cache = module.DefaultClusterNodeInfoCache(gcs)
    assert cache.get_draining_nodes() == {}, "nothing known before the first update"
    cache.update()
    assert cache.updated == 1, "Ray's own update still runs"
    assert cache.get_draining_nodes() == {WORKER: serve_drain_patch.NO_DEADLINE}
    marker = json.loads(gcs.kv[serve_drain_patch.KV_KEY])
    assert marker["version"] == serve_drain_patch.VERSION and isinstance(marker["pid"], int)

    cache.update()
    assert len(calls) == 1, "GCS is asked at most every REFRESH_SEC, not on every controller loop"


def test_a_failed_read_keeps_the_last_answer_for_machines_still_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _cache_module()
    serve_drain_patch.apply(module)
    cache = module.DefaultClusterNodeInfoCache(_Gcs())
    cache._faden_draining = {WORKER: 1, "gone": 1}

    def fail(gcs: Any, alive: Any) -> dict[str, int]:
        raise TimeoutError("gcs")

    monkeypatch.setattr(serve_drain_patch, "read_draining", fail)
    cache.update()
    assert cache.get_draining_nodes() == {WORKER: 1}


def test_a_ray_with_the_upstream_fix_is_left_alone() -> None:
    module = _cache_module(fixed_upstream=True)
    assert serve_drain_patch.apply(module) is False
    assert module.DefaultClusterNodeInfoCache(_Gcs()).get_draining_nodes() == {"upstream": 1}
    assert serve_drain_patch.apply(_cache_module()) is True
    patched = _cache_module()
    serve_drain_patch.apply(patched)
    assert serve_drain_patch.apply(patched) is False, "applied once"


def test_the_import_hook_patches_serves_cache_when_it_is_imported(fake_ray: Path) -> None:
    (fake_ray / drain.MODULE_NAME).write_text(drain.patch_source(), encoding="utf-8")
    script = textwrap.dedent(f"""
        {drain.PTH_LINE.strip()}
        import sys
        assert "ray.serve._private.cluster_node_info_cache" not in sys.modules
        from ray.serve._private import cluster_node_info_cache as m
        from ray._private.worker import global_worker
        cache = m.DefaultClusterNodeInfoCache(global_worker.gcs_client)
        cache._alive_node_id_set = frozenset({{"{HEAD}", "{WORKER}"}})
        cache._gcs_client.internal_kv_put = lambda *a: None
        cache.update()
        print("__DRAIN_BEGIN__" + __import__("json").dumps(cache.get_draining_nodes()) + "__DRAIN_END__")
    """)
    doc, _ = _run(fake_ray, script)
    assert doc == {WORKER: serve_drain_patch.NO_DEADLINE}, "only the machine GCS reports as draining"


# --------------------------------------------------------------------------
# The scripts
# --------------------------------------------------------------------------


def test_the_binary_carries_the_patch_source() -> None:
    # A one-file build holds bytecode only; without the source shipped as
    # data, enabling the drain failed on the DGX pair with "No such file".
    spec = (Path(__file__).parents[1] / "node_agent.spec").read_text(encoding="utf-8")
    assert '"serve_drain_patch.py"), "faden_agent/ray")' in spec
    assert "_SOURCE_DATAS" in spec.split("datas=")[1].splitlines()[0]


def test_every_script_is_python() -> None:
    for script in (drain.enable_script(), drain.enable_script(restart=False), drain.drain_script(),
                   drain.placement_script(), drain.stop_script(["a", "b"])):
        compile(script, "<script>", "exec")


def test_enable_puts_the_patch_beside_ray_and_waits_for_no_serve(fake_ray: Path) -> None:
    doc, _ = _run(fake_ray, drain.enable_script(wait_sec=1))
    assert doc["installed"] is True and doc["changed"] is True
    assert doc["serve_running"] is False and doc["active"] is None
    assert (fake_ray / drain.MODULE_NAME).read_text(encoding="utf-8") == drain.patch_source()
    assert (fake_ray / drain.PTH_NAME).read_text(encoding="utf-8") == drain.PTH_LINE
    again, _ = _run(fake_ray, drain.enable_script(wait_sec=1))
    assert again["changed"] is False


def test_enable_leaves_a_patched_controller_alone(fake_ray: Path) -> None:
    marker = json.dumps({"version": serve_drain_patch.VERSION, "pid": 4242})
    doc, out = _run(fake_ray, drain.enable_script(wait_sec=1), FAKE_SERVE="1", FAKE_MARKER=marker, FAKE_PID="4242")
    assert doc["active"] is True and "restarted" not in doc
    assert "KILLED" not in out


def test_enable_restarts_a_controller_that_started_without_the_patch(fake_ray: Path) -> None:
    marker = json.dumps({"version": serve_drain_patch.VERSION, "pid": 1})  # an earlier controller's
    doc, out = _run(fake_ray, drain.enable_script(wait_sec=1), FAKE_SERVE="1", FAKE_MARKER=marker, FAKE_PID="4242")
    assert "KILLED SERVE_CONTROLLER_ACTOR False" in out, "restarted, not killed for good"
    assert doc["restarted"] is True
    assert doc["active"] is False, "the stand-in never comes back patched"
    assert "did not come back" in doc["error"]


def test_enable_without_restart_only_puts_the_files_in_place(fake_ray: Path) -> None:
    doc, out = _run(fake_ray, drain.enable_script(wait_sec=1, restart=False), FAKE_SERVE="1")
    assert doc["installed"] is True and doc["active"] is False
    assert "KILLED" not in out


def test_drain_drains_the_machine_it_runs_on_without_a_deadline(fake_ray: Path) -> None:
    doc, out = _run(fake_ray, drain.drain_script())
    assert doc["accepted"] is True and doc["node_id"] == WORKER
    assert f"DRAIN {WORKER} 2 0" in out, "PREEMPTION, no deadline"
    refused, _ = _run(fake_ray, drain.drain_script(), FAKE_REFUSE="1")
    assert refused["accepted"] is False and "head nodes cannot be drained" in refused["error"]


def test_placement_says_where_each_copys_resources_are(fake_ray: Path) -> None:
    doc, _ = _run(fake_ray, drain.placement_script(), FAKE_SERVE="1")
    assert doc["errors"] == []
    assert {n["node_id"]: n["status"] for n in doc["nodes"]} == {HEAD: "RUNNING", WORKER: "DRAINING"}
    assert doc["nodes"][0]["available"]["GPU"] == pytest.approx(0.2)
    (copy,) = doc["replicas"]
    assert copy["app"] == "faden-x" and copy["deployment"] == "LLMServer:q" and copy["state"] == "RUNNING"
    assert copy["strategy"] == "PACK"
    assert copy["bundles"] == [
        {"node_id": WORKER, "resources": {"CPU": 1.0}},
        {"node_id": WORKER, "resources": {"GPU": 0.5}},
    ]


def test_stop_stops_the_named_copies_for_good(fake_ray: Path) -> None:
    doc, out = _run(fake_ray, drain.stop_script(["SERVE_REPLICA::faden-x#LLMServer:q#a1", "gone-1"]))
    assert doc["stopped"] == ["SERVE_REPLICA::faden-x#LLMServer:q#a1"] and doc["missing"] == ["gone-1"]
    assert "KILLED SERVE_REPLICA::faden-x#LLMServer:q#a1 True" in out


def test_the_document_is_found_among_rays_own_lines() -> None:
    noisy = "INFO worker.py:1 -- Connecting\n__DRAIN_BEGIN__{\"accepted\": true}__DRAIN_END__\n(log)"
    assert drain.parse(noisy) == {"accepted": True}
    assert drain.parse("nothing") is None


# --------------------------------------------------------------------------
# The commands
# --------------------------------------------------------------------------


class _Runtime:
    """A container runtime whose Ray container's Python prints *stdout*."""

    def __init__(self, stdout: str, running: bool = True) -> None:
        self.stdout, self.running = stdout, running

    @property
    def name(self) -> str:
        return "docker"

    async def exists(self, name: str) -> bool:
        return True

    async def inspect(self, name: str, **_: Any) -> dict[str, Any]:
        return {"State": {"Running": self.running}, "Config": {"Image": "faden/ray"}, "Image": "sha256:a"}

    async def exec_(self, name: str, command: list[str], *, stdin: str | None = None, **_: Any) -> tuple[int, str, str]:
        return 0, self.stdout, ""


def _manager(tmp_path: Path, runtime: _Runtime) -> Any:
    from faden_agent.event_buffer import EventBuffer
    from faden_agent.ray.manager import RayManager
    from faden_agent.state_store import StateStore

    manager = RayManager(state_store=StateStore(tmp_path / "state.json"), events=EventBuffer(),
                         ray_base_path=str(tmp_path / "ray-base"), token_dir=tmp_path / "ray-tokens")
    manager._container = RayContainerRuntime(runtime=runtime, token_path=str(tmp_path / "ray-tokens" / "t"))
    return manager


def _doc(**fields: Any) -> str:
    return "noise\n" + drain.BEGIN + json.dumps(fields) + drain.END


@pytest.mark.anyio()
async def test_the_commands_say_why_they_failed(tmp_path: Path) -> None:
    from faden_agent.runtimes import ContainerRuntimeError

    with pytest.raises(ContainerRuntimeError, match="refused"):
        await _manager(tmp_path, _Runtime(_doc(attached=True, accepted=False, error="Ray refused the drain: x"))
                       ).drain_ray_node({})
    with pytest.raises(ContainerRuntimeError, match="did not come back"):
        await _manager(tmp_path, _Runtime(_doc(attached=True, installed=True, active=False,
                                               error="did not come back"))).enable_serve_drain({})
    with pytest.raises(ContainerRuntimeError, match="no cluster"):
        await _manager(tmp_path, _Runtime(_doc(attached=False, error="attach: no cluster"))).describe_placement({})
    with pytest.raises(ContainerRuntimeError, match="not running"):
        await _manager(tmp_path, _Runtime("", running=False)).describe_placement({})
    with pytest.raises(ContainerRuntimeError, match="no result"):
        await _manager(tmp_path, _Runtime("Traceback: boom")).describe_placement({})


@pytest.mark.anyio()
async def test_enable_with_no_serve_yet_is_fine(tmp_path: Path) -> None:
    doc = await _manager(tmp_path, _Runtime(_doc(attached=True, installed=True, serve_running=False, active=None))
                         ).enable_serve_drain({})
    assert doc["installed"] is True


@pytest.mark.anyio()
async def test_stopping_no_copies_asks_nothing(tmp_path: Path) -> None:
    assert await _manager(tmp_path, _Runtime("unused")).stop_serve_replicas({"actor_names": []}) == {
        "stopped": [], "missing": []}
