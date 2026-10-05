"""Changing how many copies of a model run, without restarting the ones that run.

Deploying a Serve application again replaces every copy of its model, even
when only the copy count changed: the runtime helper deploys with
``serve.run_many``, which carries no version (Ray 2.58 removed ``version``
from ``@serve.deployment``), so each deploy gets a random code version and
Serve takes it for new code. On the DGX pair that stopped both copies of the
27B for eight minutes going from two copies to one, and replaced both, one
after the other, going from one to two.

Ray 2.58 has the count-only path: an application deployed with
``external_scaler_enabled`` takes ``update_deployment_replicas`` from Serve's
controller (the call behind the dashboard's ``.../deployments/{name}/scale``),
which sets the target count and keeps the version -- new copies start, surplus
ones finish their requests and stop, the rest go on serving. The count is kept
in the controller's checkpoint, so it outlives a controller restart. Serve
refuses the external scaler next to its own autoscaling, so it is on only for
models with a fixed number of copies.

The runtime helper does both from ``llm_port_ray_runtime`` 0.2.0 on
(``FEATURES`` has ``serve_scale``): it deploys fixed-copy apps with the
external scaler on, and has a ``scale-serve-app`` verb. The images on the
machines today have the 0.1 helper, so for them these scripts run inside the
container instead (as ``inspect.py`` and ``drain.py`` do):

``FEATURES_SCRIPT``
    What the image's helper can do (``version``, ``features``).
:func:`deploy_script`
    Deploy through the image's own ``RayServeClient.run_app`` -- the same
    build and the same Serve call -- with the external scaler set on the
    ``RunTarget`` it builds when the app's copy count is fixed.
:func:`scale_script`
    Set the app's model server deployments (``LLMServer:...``) to a count.
"""

from __future__ import annotations

import json
from typing import Any

BEGIN, END = "__SCALE_BEGIN__", "__SCALE_END__"
#: The helper feature that makes these scripts unnecessary.
FEATURE = "serve_scale"

_PRINT = r'''
def done(out):
    print("__SCALE_BEGIN__" + json.dumps(out, default=str) + "__SCALE_END__")
    sys.exit(0)
'''

FEATURES_SCRIPT = r'''
import json, sys
''' + _PRINT + r'''
try:
    import llm_port_ray_runtime as rt
    done({"version": getattr(rt, "__version__", None), "features": list(getattr(rt, "FEATURES", ()))})
except Exception as exc:
    done({"version": None, "features": [], "error": str(exc)})
'''

_DEPLOY = r'''
import json, sys
''' + _PRINT + r'''
DOC = json.loads(__DOC__)
out = {"deployed": False, "app_name": DOC["app_name"]}

def fixed(config):
    # The helper's fixed_copies(): Serve refuses the external scaler next to
    # its own autoscaling, which a model without num_replicas gets.
    if not isinstance(config, dict):
        return False
    deployment = config.get("deployment_config") or {}
    count = deployment.get("num_replicas")
    return (deployment.get("autoscaling_config") is None
            and isinstance(count, int) and not isinstance(count, bool))

try:
    from ray import serve
    from llm_port_ray_runtime.serve import RayServeClient

    args = DOC["llm_serving_args"]
    configs = args.get("llm_configs") or []
    scaler = bool(configs) and all(fixed(c) for c in configs) and DOC.get("external_scaler") is not False
    run_target = serve.RunTarget

    def with_scaler(*a, **k):
        k.setdefault("external_scaler_enabled", scaler)
        return run_target(*a, **k)

    # run_app looks RunTarget up on the module when it deploys.
    serve.RunTarget = with_scaler
    out = RayServeClient().run_app(
        DOC["app_name"], args, http_options=DOC.get("http_options"), route_prefix=DOC["route_prefix"],
    )
    out["external_scaler"] = scaler
except Exception as exc:
    out["error"] = str(exc)
done(out)
'''

_SCALE = r'''
import json, sys, logging, warnings
warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)
''' + _PRINT + r'''
APP = __APP__
REPLICAS = __REPLICAS__
out = {"scaled": False, "app_name": APP, "replicas": REPLICAS}
try:
    import ray
    ray.init(address="auto", logging_level="ERROR", log_to_driver=False)
except Exception as exc:
    out.update(reason="attach", error="attach: %s" % exc)
    done(out)
try:
    from ray.serve._private.common import DeploymentID
    from ray.serve.context import _get_global_client

    client = _get_global_client(raise_if_no_controller_running=False)
    app = ((client.get_serve_details() if client is not None else {}).get("applications") or {}).get(APP)
    if app is None:
        out.update(reason="app_not_found", error="no application %s" % APP)
        done(out)
    if not app.get("external_scaler_enabled"):
        out.update(reason="external_scaler_disabled", error="%s was deployed without the external scaler" % APP)
        done(out)
    names = [name for name in (app.get("deployments") or {}) if name.startswith("LLMServer")]
    if not names:
        out.update(reason="app_not_found", error="%s has no model server deployment" % APP)
        done(out)
    for name in names:
        ray.get(client._controller.update_deployment_replicas.remote(
            DeploymentID(name=name, app_name=APP), REPLICAS))
    out.update(scaled=True, deployments=names)
except Exception as exc:
    message = str(exc)
    out.update(reason="external_scaler_disabled" if "external_scaler_enabled" in message else "error", error=message)
done(out)
'''


def deploy_script(
    app_name: str,
    llm_serving_args: dict[str, Any],
    *,
    http_options: dict[str, Any] | None = None,
    route_prefix: str = "/",
    external_scaler: bool | None = None,
) -> str:
    """Deploy *app_name* through the image's helper code, the external scaler on for fixed copy counts.

    The document is part of the script, which goes in on stdin: like the
    helper's own ``--config -``, it never reaches a process list.
    """
    doc = {
        "app_name": app_name,
        "llm_serving_args": llm_serving_args,
        "http_options": http_options or {},
        "route_prefix": route_prefix,
        "external_scaler": external_scaler,
    }
    return _DEPLOY.replace("__DOC__", repr(json.dumps(doc)))


def scale_script(app_name: str, replicas: int) -> str:
    """Run *replicas* copies of *app_name*'s model, the running ones left alone."""
    return _SCALE.replace("__APP__", repr(app_name)).replace("__REPLICAS__", str(int(replicas)))


def parse(stdout: str) -> dict[str, Any] | None:
    """The document a script printed, or ``None``."""
    text = stdout or ""
    start, end = text.rfind(BEGIN), text.rfind(END)
    if start < 0 or end < start:
        return None
    try:
        doc = json.loads(text[start + len(BEGIN):end])
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None
