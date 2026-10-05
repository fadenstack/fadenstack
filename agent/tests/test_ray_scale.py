"""Changing a model's copy count without restarting the copies that run.

The scripts run here against a stand-in ``ray`` package shaped after Ray 2.58
(``serve.RunTarget`` with ``external_scaler_enabled``, the controller's
``update_deployment_replicas``), and against two runtime helpers: a 0.1 one
like the images on the machines today, and the 0.2 one in ``runtime-image``.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from faden_agent.ray import scale

RUNTIME_IMAGE = Path(__file__).resolve().parents[2] / "runtime-image"

_FAKE_RAY = {
    "ray/__init__.py": """
        __version__ = "2.58.0"
        _INIT = [False]
        def is_initialized(): return _INIT[0]
        def init(**kw): _INIT[0] = True
        def get(value): return value
    """,
    "ray/serve/__init__.py": """
        class RunTarget:
            def __init__(self, target, name="default", route_prefix="/", logging_config=None,
                         external_scaler_enabled=False):
                self.target, self.name, self.route_prefix = target, name, route_prefix
                self.external_scaler_enabled = external_scaler_enabled
        def start(**kw): pass
        def run_many(targets, **kw):
            for t in targets:
                print("RUN %s scaler=%s" % (t.name, t.external_scaler_enabled))
    """,
    "ray/serve/llm.py": """
        def build_openai_app(args): return {"app": args}
    """,
    "ray/serve/context.py": """
        import json, os
        class _Remote:
            def remote(self, deployment_id, count):
                if os.environ.get("FAKE_REFUSE") == "1":
                    raise RuntimeError("The external scaling API can only be used when "
                                       "'external_scaler_enabled' is set to true")
                print("SCALE %s %s %s" % (deployment_id.app_name, deployment_id.name, count))
                return None
        class _Controller:
            update_deployment_replicas = _Remote()
        class _Client:
            _controller = _Controller()
            def get_serve_details(self):
                return {"applications": json.loads(os.environ.get("FAKE_APPS", "{}"))}
        def _get_global_client(raise_if_no_controller_running=True):
            return _Client() if os.environ.get("FAKE_SERVE", "1") == "1" else None
    """,
    "ray/serve/_private/__init__.py": "",
    "ray/serve/_private/common.py": """
        class DeploymentID:
            def __init__(self, name, app_name):
                self.name, self.app_name = name, app_name
    """,
}

#: The helper as the images on the machines carry it: no FEATURES, no scaler.
_OLD_HELPER = {
    "old_helper/llm_port_ray_runtime/__init__.py": '__version__ = "0.1.0"\n',
    "old_helper/llm_port_ray_runtime/serve.py": """
        class RayServeClient:
            def run_app(self, app_name, llm_serving_args, http_options=None, route_prefix="/"):
                import ray
                ray.init(address="auto")
                from ray import serve
                from ray.serve.llm import build_openai_app
                app = build_openai_app(llm_serving_args)
                serve.run_many([serve.RunTarget(app, name=app_name, route_prefix=route_prefix)],
                               wait_for_applications_running=False)
                return {"deployed": True, "app_name": app_name, "route_prefix": route_prefix}
    """,
}

FIXED = {"llm_configs": [{"model_loading_config": {"model_id": "q"}, "deployment_config": {"num_replicas": 2}}]}
AUTOSCALED = {"llm_configs": [{"model_loading_config": {"model_id": "q"},
                               "deployment_config": {"autoscaling_config": {"min_replicas": 1, "max_replicas": 3}}}]}
APPS = {
    "faden-a": {"external_scaler_enabled": True,
                "deployments": {"LLMServer:q": {}, "OpenAiIngress": {}}},
    "faden-b": {"external_scaler_enabled": False, "deployments": {"LLMServer:q": {}}},
}


@pytest.fixture()
def stand_in(tmp_path: Path) -> Path:
    for rel, body in {**_FAKE_RAY, **_OLD_HELPER}.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body), encoding="utf-8")
    return tmp_path


def _run(stand_in: Path, script: str, *, helper: str = "old", **env: str) -> tuple[dict[str, Any], str]:
    helper_path = stand_in / "old_helper" if helper == "old" else RUNTIME_IMAGE
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-"], input=script, capture_output=True, text=True,
        env={
            "PYTHONPATH": f"{stand_in}{';' if sys.platform == 'win32' else ':'}{helper_path}",
            "SYSTEMROOT": "C:\\Windows", "PATH": "", "FAKE_APPS": json.dumps(APPS), **env,
        },
        check=False, timeout=60,
    )
    doc = scale.parse(proc.stdout)
    assert doc is not None, proc.stdout + proc.stderr
    return doc, proc.stdout


# --------------------------------------------------------------------------
# The scripts, for helpers that predate the feature
# --------------------------------------------------------------------------


def test_an_old_helper_has_no_scale_feature(stand_in: Path) -> None:
    doc, _ = _run(stand_in, scale.FEATURES_SCRIPT)
    assert doc == {"version": "0.1.0", "features": []}


def test_the_new_helper_says_it_scales(stand_in: Path) -> None:
    doc, _ = _run(stand_in, scale.FEATURES_SCRIPT, helper="new")
    assert doc["version"] == "0.2.0"
    assert scale.FEATURE in doc["features"]


def test_a_fixed_copy_app_is_deployed_through_the_images_helper_with_the_external_scaler(stand_in: Path) -> None:
    doc, out = _run(stand_in, scale.deploy_script("faden-a", FIXED, route_prefix="/faden-a"))
    assert "RUN faden-a scaler=True" in out, "the helper's own run_app made the deploy, scaler on"
    assert doc["deployed"] is True
    assert doc["route_prefix"] == "/faden-a"
    assert doc["external_scaler"] is True


def test_an_autoscaled_app_keeps_serves_own_autoscaling(stand_in: Path) -> None:
    doc, out = _run(stand_in, scale.deploy_script("faden-a", AUTOSCALED))
    assert "RUN faden-a scaler=False" in out
    assert doc["external_scaler"] is False


def test_the_scaler_can_be_kept_off(stand_in: Path) -> None:
    doc, out = _run(stand_in, scale.deploy_script("faden-a", FIXED, external_scaler=False))
    assert "scaler=False" in out
    assert doc["external_scaler"] is False


def test_the_document_is_in_the_script_not_on_a_command_line() -> None:
    script = scale.deploy_script("faden-a", {"llm_configs": [{"runtime_env": {"env_vars": {"HF_TOKEN": "hf_x"}}}]})
    assert "hf_x" in script, "it travels on stdin with the script"


def test_scaling_sets_only_the_model_servers_count(stand_in: Path) -> None:
    doc, out = _run(stand_in, scale.scale_script("faden-a", 3))
    assert "SCALE faden-a LLMServer:q 3" in out
    assert "OpenAiIngress" not in out, "the ingress is left as it is"
    assert doc["scaled"] is True
    assert doc["deployments"] == ["LLMServer:q"]


def test_an_app_deployed_without_the_scaler_is_not_touched(stand_in: Path) -> None:
    doc, out = _run(stand_in, scale.scale_script("faden-b", 3))
    assert "SCALE faden" not in out
    assert doc["scaled"] is False
    assert doc["reason"] == "external_scaler_disabled"


def test_a_missing_app_says_so(stand_in: Path) -> None:
    doc, _ = _run(stand_in, scale.scale_script("faden-gone", 1))
    assert doc["reason"] == "app_not_found"


def test_serves_refusal_reads_as_deployed_without_the_scaler(stand_in: Path) -> None:
    doc, _ = _run(stand_in, scale.scale_script("faden-a", 1), FAKE_REFUSE="1")
    assert doc["scaled"] is False
    assert doc["reason"] == "external_scaler_disabled"


# --------------------------------------------------------------------------
# The 0.2 helper itself (runtime-image/llm_port_ray_runtime)
# --------------------------------------------------------------------------

def _helper_call(stand_in: Path, call: str, **env: str) -> tuple[dict[str, Any], str]:
    script = (
        "import json\n"
        "from llm_port_ray_runtime.serve import RayServeClient\n"
        f"out = {call}\n"
        'print("__SCALE_BEGIN__" + json.dumps(out) + "__SCALE_END__")\n'
    )
    return _run(stand_in, script, helper="new", **env)


def test_the_new_helper_deploys_fixed_copies_with_the_scaler(stand_in: Path) -> None:
    doc, out = _helper_call(stand_in, f"RayServeClient().run_app('faden-a', {FIXED!r}, route_prefix='/faden-a')")
    assert "RUN faden-a scaler=True" in out
    assert doc["external_scaler"] is True


def test_the_new_helper_leaves_autoscaled_apps_to_serve(stand_in: Path) -> None:
    doc, out = _helper_call(stand_in, f"RayServeClient().run_app('faden-a', {AUTOSCALED!r})")
    assert "RUN faden-a scaler=False" in out
    assert doc["external_scaler"] is False


def test_the_new_helper_scales_in_place(stand_in: Path) -> None:
    doc, out = _helper_call(stand_in, "RayServeClient().scale_app('faden-a', 2)")
    assert "SCALE faden-a LLMServer:q 2" in out
    assert doc["scaled"] is True


def test_the_new_helper_refuses_an_app_without_the_scaler(stand_in: Path) -> None:
    doc, out = _helper_call(stand_in, "RayServeClient().scale_app('faden-b', 2)")
    assert "SCALE faden" not in out
    assert doc["reason"] == "external_scaler_disabled"
