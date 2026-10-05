"""Ray Serve Python SDK probe and status normalization."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from llm_port_ray_runtime.core import RayCoreClient
from llm_port_ray_runtime.models import (
    RayApplicationStatus,
    RayDeploymentStatus,
    RayServeClusterStatus,
)

log = logging.getLogger(__name__)

#: Ray Serve LLM names a model's server deployment ``LLMServer:<model>``.
_SERVER_PREFIX = "LLMServer"


def fixed_copies(llm_serving_args: Dict[str, Any]) -> bool:
    """Whether every model in *llm_serving_args* runs a fixed number of copies.

    Only such an app can take Ray's external scaler: Serve refuses it next to
    its own autoscaling, which is what a model without ``num_replicas`` gets.
    """
    configs = llm_serving_args.get("llm_configs") or []
    if not configs:
        return False
    for config in configs:
        if not isinstance(config, dict):
            return False
        deployment = config.get("deployment_config") or {}
        if deployment.get("autoscaling_config") is not None:
            return False
        count = deployment.get("num_replicas")
        if not isinstance(count, int) or isinstance(count, bool):
            return False
    return True


class RayServeClient:
    """Probe Serve controller and applications using the Ray Serve Python SDK."""

    def __init__(self, core: Optional[RayCoreClient] = None) -> None:
        self.core = core or RayCoreClient()

    def run_app(
        self,
        app_name: str,
        llm_serving_args: Dict[str, Any],
        http_options: Optional[Dict[str, Any]] = None,
        route_prefix: str = "/",
        external_scaler: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Deploy a named Serve LLM application.

        Lives here rather than in the host Node Agent on purpose: this package
        ships *inside* the certified image, so the Serve API call is made with
        the exact Ray version the cluster runs and is covered by the image's
        certification.  The host agent only invokes this through the CLI's JSON
        contract and never imports Ray itself (4B host/runtime boundary).

        Idempotent per application name: re-running replaces only this
        application and leaves other named apps alone.

        **Submits; does not wait for the application to serve.** ``serve.run``
        looks like the obvious call and is the wrong one: it forwards
        ``_blocking=True`` to Ray's internal ``_run``, which calls
        ``_wait_for_application_running(name, timeout_s=-1)`` -- a negative
        timeout means wait forever. So it returns only once the app is RUNNING
        or raises once it is DEPLOY_FAILED.

        A failing application takes a long time to reach that verdict: Serve
        restarts a replica three times, and each attempt carries the engine's
        own start timeout. The whole control path above this is sized for a
        call that returns in seconds -- a 300s exec budget in the agent and a
        300s command budget in the backend -- so both expired long before Ray
        had an answer, the command stayed in flight, and the deployment row
        went on reporting whatever it last knew. The reason for the failure
        existed the entire time, in the RuntimeError this call would
        eventually have raised, with nobody left listening for it.

        ``run_many`` is the public API that lets us say so: the application is
        handed to the controller and convergence is read afterwards from the
        status tier, which is what the agent and backend already do.

        **External scaler on for fixed copy counts** (*external_scaler*
        ``None`` decides by :func:`fixed_copies`; ``False`` keeps it off).
        Deploying an application again replaces every copy of its model: these
        deploys carry no version (Ray 2.58 removed ``version`` from
        ``@serve.deployment``), so each one gets a random code version and
        Serve takes it for new code. Changing the copy count that way stopped
        both copies of a 27B model on the DGX pair for eight minutes (2 -> 1).
        With the external scaler on, :meth:`scale_app` changes the count alone.
        """
        self.core.ensure_attached()

        from ray import serve
        from ray.serve.llm import build_openai_app

        opts = dict(http_options or {})
        start_kwargs: Dict[str, Any] = {
            "host": opts.get("host", "0.0.0.0"),
            "port": int(opts.get("port", 8000)),
        }
        if opts.get("location"):
            start_kwargs["proxy_location"] = opts["location"]

        try:
            # No-op when Serve is already running; http options only apply to
            # the first start, which Ray itself enforces.
            serve.start(http_options={"host": start_kwargs["host"], "port": start_kwargs["port"]})
        except Exception as exc:  # pragma: no cover - Serve already running
            log.info("serve.start skipped (%s)", exc)

        app = build_openai_app(llm_serving_args)
        scaler = fixed_copies(llm_serving_args) and external_scaler is not False
        # ``wait_for_ingress_deployment_creation`` stays on: it is a bounded
        # controller round-trip that confirms the application was accepted, so
        # "submitted" still means something. Only the open-ended wait for it to
        # become healthy is dropped.
        serve.run_many(
            [serve.RunTarget(app, name=app_name, route_prefix=route_prefix, external_scaler_enabled=scaler)],
            wait_for_applications_running=False,
        )
        return {
            "deployed": True,
            "app_name": app_name,
            "route_prefix": route_prefix,
            "http_options": start_kwargs,
            "external_scaler": scaler,
        }

    def scale_app(self, app_name: str, replicas: int, deployment: Optional[str] = None) -> Dict[str, Any]:
        """Run *replicas* copies of an application's model, leaving the running ones be.

        Asks Serve's controller for the count alone (``update_deployment_replicas``,
        the call behind the dashboard's ``.../deployments/{name}/scale``): the
        deployment keeps its version, so new copies start and surplus ones
        finish their requests and stop while the rest go on serving. Only for
        an application deployed with the external scaler on (:meth:`run_app`);
        for any other the answer says so (``reason: external_scaler_disabled``)
        and the caller deploys it again instead.

        *deployment* defaults to the app's model server deployments
        (``LLMServer:...``), not its ingress.
        """
        base: Dict[str, Any] = {"scaled": False, "app_name": app_name, "replicas": int(replicas)}
        try:
            self.core.ensure_attached()
        except Exception as exc:
            return {**base, "reason": "attach", "error": f"Cannot attach to Ray: {exc}"}

        try:
            import ray
            from ray.serve._private.common import DeploymentID
            from ray.serve.context import _get_global_client

            client = _get_global_client(raise_if_no_controller_running=False)
            if client is None:
                return {**base, "reason": "app_not_found", "error": "Serve is not running"}
            app = (client.get_serve_details().get("applications") or {}).get(app_name)
            if app is None:
                return {**base, "reason": "app_not_found", "error": f"no application {app_name}"}
            if not app.get("external_scaler_enabled"):
                return {**base, "reason": "external_scaler_disabled",
                        "error": f"{app_name} was deployed without the external scaler"}
            names = [deployment] if deployment else [
                name for name in (app.get("deployments") or {}) if name.startswith(_SERVER_PREFIX)
            ]
            if not names:
                return {**base, "reason": "app_not_found", "error": f"{app_name} has no model server deployment"}
            for name in names:
                ray.get(client._controller.update_deployment_replicas.remote(
                    DeploymentID(name=name, app_name=app_name), int(replicas),
                ))
        except Exception as exc:
            message = str(exc)
            reason = "external_scaler_disabled" if "external_scaler_enabled" in message else "error"
            return {**base, "reason": reason, "error": message}
        return {**base, "scaled": True, "deployments": names}

    def delete_app(self, app_name: str) -> Dict[str, Any]:
        """Delete a named Serve application.

        Deleting an application that was never deployed is reported as a
        success: the desired absence already holds, and the caller re-runs this
        on every teardown pass.
        """
        try:
            self.core.ensure_attached()
        except Exception as exc:
            return {"deleted": False, "app_name": app_name, "error": f"Cannot attach to Ray: {exc}"}

        try:
            from ray import serve

            serve.delete(app_name)
            return {"deleted": True, "app_name": app_name}
        except KeyError:
            return {"deleted": True, "app_name": app_name, "detail": "application not present"}
        except Exception as exc:
            message = str(exc)
            if "does not exist" in message or "not found" in message.lower():
                return {"deleted": True, "app_name": app_name, "detail": message}
            return {"deleted": False, "app_name": app_name, "error": message}

    def status(self) -> RayServeClusterStatus:
        """Query Serve status via ray.serve.status()."""
        try:
            self.core.ensure_attached()
        except Exception as exc:
            return RayServeClusterStatus(available=False, error=f"Cannot attach to Ray: {exc}")

        try:
            from ray import serve

            serve_status = serve.status()
        except ImportError as exc:
            return RayServeClusterStatus(available=False, error=f"Serve not installed: {exc}")
        except Exception as exc:
            return RayServeClusterStatus(available=False, error=f"Serve controller error: {exc}")

        apps: Dict[str, RayApplicationStatus] = {}
        for app_name, app_overview in serve_status.applications.items():
            deps: Dict[str, RayDeploymentStatus] = {}
            for dep_name, dep_overview in getattr(app_overview, "deployments", {}).items():
                num_ready = 0
                num_pending = 0
                for r_state, count in getattr(dep_overview, "replica_states", {}).items():
                    state_str = str(getattr(r_state, "name", r_state))
                    if state_str in ("RUNNING", "READY"):
                        num_ready += count
                    else:
                        num_pending += count

                deps[dep_name] = RayDeploymentStatus(
                    name=dep_name,
                    status=str(getattr(dep_overview.status, "name", dep_overview.status)),
                    status_trigger=getattr(dep_overview, "status_trigger", None),
                    message=getattr(dep_overview, "message", "") or "",
                    num_replicas_ready=num_ready,
                    num_replicas_pending=num_pending,
                )

            status_str = str(getattr(app_overview.status, "name", app_overview.status))
            is_healthy = status_str in ("RUNNING", "HEALTHY")
            apps[app_name] = RayApplicationStatus(
                name=app_name,
                status=status_str,
                message=getattr(app_overview, "message", "") or "",
                last_deployed_time_s=getattr(app_overview, "last_deployed_time_s", None),
                healthy=is_healthy,
                deployments=deps,
            )

        return RayServeClusterStatus(
            available=True,
            controller_alive=True,
            applications=apps,
        )

