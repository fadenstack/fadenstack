"""Operators cannot reach code execution on the GPU machines through model or cluster settings.

An operator (a role an administrator hands out, not a superuser) may create and
edit deployments and clusters. What they write reaches vLLM's keyword arguments
and Ray's start-up on the machines, so a few settings are a superuser's:
``engine.config`` keys that run code or read node files, a Serve host that
redirects every chat, environment variables that switch Ray's security off.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dao.inference_dao import ControlPlaneDAO
from faden_backend.db.dao.rbac_dao import RbacDAO
from faden_backend.db.models.users import User, current_active_user
from faden_backend.services.inference import engine_guard
from faden_backend.services.inference.drivers.ray import compiler
from faden_backend.services.inference.drivers.ray.schemas import (
    EnvironmentConfigRefused,
    RayEnvironmentConfig,
    check_environment_config,
)
from faden_backend.services.marketplace import recipes
from tests.test_inference_api import good_spec, make_model

API = "/api/inference"


def _spec(**engine: Any) -> dict:
    return good_spec(engine={"name": "vllm", "config": engine})


# ── the rules, without a database ───────────────────────────────────────


@pytest.mark.parametrize(
    "key,value",
    [
        ("trust_remote_code", True),
        ("trust-remote-code", True),  # dashes are the command-line spelling
        ("allowed_local_media_path", "/etc"),
        ("scheduler_cls", "evil.module.Scheduler"),
        ("logits_processors", ["evil.module.Processor"]),
        ("worker_extension_cls", "evil.Worker"),
        ("tool_parser_plugin", "/tmp/parser.py"),
        ("kv_transfer_config", {"kv_connector": "X", "kv_connector_module_path": "evil"}),
        ("hf_token", "hf_secret"),
        ("otlp_traces_endpoint", "http://attacker:4317"),
        ("ssl_keyfile", "/etc/ssl/private/x.key"),
        ("tokenizer", "/etc/passwd"),
        ("tokenizer", "https://attacker.example/tok"),
        ("hf_config_path", "../../etc"),
        ("download_dir", "/home/someone"),
        ("chat_template", "/etc/shadow"),
        ("generation_config", "C:\\Users\\x"),
        ("hf_overrides", {"auto_map": {"AutoModel": "mod.Model"}}),
        ("hf_overrides", '{"architectures": ["X"], "auto_map": {"AutoConfig": "c.C"}}'),
        ("speculative_config", {"method": "draft_model", "model": "/etc/secret"}),
        ("compilation_config", {"cache_dir": "/etc/x", "pass_config": {"x_module_path": "evil"}}),
    ],
)
def test_an_operator_may_not_set(key: str, value: Any) -> None:
    refused = engine_guard.guarded_settings({key: value})
    assert refused, f"{key}={value!r} should be refused"
    with pytest.raises(engine_guard.EngineSettingsRefused):
        engine_guard.check_spec(_spec(**{key: value}))


@pytest.mark.parametrize(
    "key,value",
    [
        ("trust_remote_code", False),  # off is always fine
        ("max_model_len", 32768),
        ("gpu_memory_utilization", 0.8),
        ("tool_call_parser", "hermes"),
        ("enable_auto_tool_choice", True),
        ("speculative_config", {"method": "mtp", "num_speculative_tokens": 3}),
        ("speculative_config", '{"method": "mtp"}'),
        ("compilation_config", {"custom_ops": ["+rms_norm"], "cudagraph_mode": "FULL"}),
        ("hf_overrides", {"max_position_embeddings": 131072}),
        ("tokenizer", "Qwen/Qwen3-8B"),  # a repository id is a model reference
        ("tokenizer", "/models/hub/some-tokenizer"),  # inside the model store
        ("chat_template", "{% for m in messages %}{{ m.content }}{% endfor %}"),
        ("chat_template", "/models/templates/chat.jinja"),
        ("served_model_name", "my-model"),
    ],
)
def test_an_operator_may_set(key: str, value: Any) -> None:
    assert engine_guard.guarded_settings({key: value}) == {}
    engine_guard.check_spec(_spec(**{key: value}))


def test_a_path_that_climbs_out_of_the_store_is_outside_it() -> None:
    assert engine_guard.in_model_store("/models/a/b")
    assert not engine_guard.in_model_store("/models/../etc/passwd")
    assert not engine_guard.in_model_store("/modelsX/a")
    assert not engine_guard.in_model_store("file:///models/a")


def test_a_setting_a_superuser_made_may_stay_when_an_operator_saves() -> None:
    """The console sends the whole spec back; refusing the unchanged part would lock the deployment."""
    before = _spec(trust_remote_code=True, max_model_len=4096)
    after = _spec(trust_remote_code=True, max_model_len=8192)
    engine_guard.check_spec(after, previous=before)
    with pytest.raises(engine_guard.EngineSettingsRefused):
        engine_guard.check_spec(_spec(trust_remote_code=True, scheduler_cls="x.Y"), previous=before)
    with pytest.raises(engine_guard.EngineSettingsRefused):
        engine_guard.check_spec(_spec(max_model_len=4096, trust_remote_code=True), previous=_spec(max_model_len=1))


def test_a_model_outside_the_store_is_refused_for_an_operator() -> None:
    spec = good_spec(artifacts={"source": "local_path", "root_path": "/srv/other/model"})
    with pytest.raises(engine_guard.EngineSettingsRefused, match="root_path"):
        engine_guard.check_spec(spec)
    engine_guard.check_spec(good_spec(artifacts={"source": "local_path", "root_path": "/models/hub/m"}))


def test_the_message_says_what_the_setting_does() -> None:
    with pytest.raises(engine_guard.EngineSettingsRefused) as caught:
        engine_guard.check_spec(_spec(trust_remote_code=True))
    assert "Python code" in str(caught.value)
    assert "superuser" in str(caught.value)


# ── a recipe never switches it on ───────────────────────────────────────


def test_a_recipe_cannot_turn_on_remote_code() -> None:
    config, dropped = recipes.args_to_config(
        ["--trust-remote-code", "--tool-call-parser", "hermes", "--logits-processors", "x.Y"]
    )
    assert "trust_remote_code" not in config
    assert "logits_processors" not in config
    assert config["tool_call_parser"] == "hermes"
    assert "--trust-remote-code" in dropped


# ── the API: operator against superuser ─────────────────────────────────


async def _operator(dbsession: AsyncSession, fastapi_app: FastAPI) -> User:
    rbac = RbacDAO(dbsession)
    await rbac.seed_defaults()
    user = User(
        email=f"operator-{uuid.uuid4().hex}@test.local", hashed_password="x",
        is_verified=True, is_active=True, is_superuser=False,
    )
    dbsession.add(user)
    await dbsession.flush()
    await rbac.assign_role(user.id, (await rbac.get_role_by_name("operator")).id)
    fastapi_app.dependency_overrides[current_active_user] = lambda: user
    return user


async def _cluster(dbsession: AsyncSession, name: str) -> Any:
    from faden_backend.db.dao.inference_dao import EnvironmentDAO  # noqa: PLC0415

    cp = await ControlPlaneDAO(dbsession).create(name=f"cp-{name}", driver="ray")
    env = await EnvironmentDAO(dbsession).create(control_plane_id=cp.id, name=name)
    await dbsession.flush()
    return env


async def test_an_operator_cannot_create_a_deployment_that_runs_repository_code(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession
) -> None:
    await _operator(dbsession, fastapi_app)
    env = await _cluster(dbsession, "guard-create")
    model_id = await make_model(dbsession)

    def body(**engine: Any) -> dict:
        return {"environment_id": str(env.id), "model_id": str(model_id),
                "name": f"dep-{uuid.uuid4().hex[:8]}", "spec": _spec(**engine)}

    refused = await client.post(f"{API}/deployments", json=body(trust_remote_code=True))
    assert refused.status_code == 403, refused.text
    assert "trust_remote_code" in refused.json()["detail"]

    assert (await client.post(f"{API}/deployments", json=body(max_model_len=4096))).status_code == 201


async def test_an_operator_cannot_add_it_by_editing(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession
) -> None:
    await _operator(dbsession, fastapi_app)
    env = await _cluster(dbsession, "guard-edit")
    model_id = await make_model(dbsession)
    created = await client.post(f"{API}/deployments", json={
        "environment_id": str(env.id), "model_id": str(model_id), "name": "dep-edit", "spec": _spec(max_model_len=1024),
    })
    assert created.status_code == 201, created.text
    dep_id = created.json()["id"]

    refused = await client.patch(f"{API}/deployments/{dep_id}", json={"spec": _spec(allowed_local_media_path="/")})
    assert refused.status_code == 403, refused.text
    # Nothing was written.
    assert "allowed_local_media_path" not in (await client.get(f"{API}/deployments/{dep_id}")).json()["spec"]["engine"]["config"]


async def test_a_superuser_may_set_it_and_an_operator_may_keep_editing_around_it(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession
) -> None:
    superuser = User(
        email=f"su-{uuid.uuid4().hex}@test.local", hashed_password="x",
        is_verified=True, is_active=True, is_superuser=True,
    )
    dbsession.add(superuser)
    await dbsession.flush()
    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser
    env = await _cluster(dbsession, "guard-super")
    model_id = await make_model(dbsession)
    created = await client.post(f"{API}/deployments", json={
        "environment_id": str(env.id), "model_id": str(model_id), "name": "dep-su",
        "spec": _spec(trust_remote_code=True, max_model_len=1024),
    })
    assert created.status_code == 201, created.text
    dep_id = created.json()["id"]

    await _operator(dbsession, fastapi_app)
    same = await client.patch(f"{API}/deployments/{dep_id}", json={"spec": _spec(trust_remote_code=True, max_model_len=2048)})
    assert same.status_code == 200, same.text
    other = await client.patch(f"{API}/deployments/{dep_id}", json={"spec": _spec(trust_remote_code=True, scheduler_cls="a.B")})
    assert other.status_code == 403


async def test_a_spec_env_var_that_moves_the_hub_is_refused_up_front(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession
) -> None:
    await _operator(dbsession, fastapi_app)
    env = await _cluster(dbsession, "guard-env")
    model_id = await make_model(dbsession)
    for name in ("HF_ENDPOINT", "HF_MODULES_CACHE", "VLLM_PLUGINS", "VLLM_ALLOWED_LOCAL_MEDIA_PATH", "HF_TOKEN"):
        spec = good_spec(extensions={"env_vars": {name: "x"}})
        r = await client.post(f"{API}/deployments", json={
            "environment_id": str(env.id), "model_id": str(model_id), "name": f"d-{name.lower().replace('_', '-')}", "spec": spec,
        })
        assert r.status_code == 409, (name, r.text)
    ok = good_spec(extensions={"env_vars": {"VLLM_USE_V1": "1"}})
    r = await client.post(f"{API}/deployments", json={
        "environment_id": str(env.id), "model_id": str(model_id), "name": "d-ok", "spec": ok,
    })
    assert r.status_code == 201, r.text


def test_the_compiler_refuses_the_same_env_vars() -> None:
    spec = compiler.parse_inference_deployment_spec(good_spec(extensions={"env_vars": {"HF_ENDPOINT": "http://x"}}))
    with pytest.raises(compiler.DeploymentValidationError):
        compiler.validate_spec(spec)


# ── cluster settings ────────────────────────────────────────────────────


MACHINES = ["10.0.0.5", "gpu-1.lan"]


def test_node_env_vars_cannot_name_rays_security_settings() -> None:
    for name in ("RAY_AUTH_MODE", "RAY_AUTH_TOKEN_PATH", "RAY_USE_TLS", "RAY_TLS_CA_CERT", "CUDA_VISIBLE_DEVICES"):
        with pytest.raises(EnvironmentConfigRefused, match=name):
            check_environment_config({"node_env_vars": {name: "x"}}, machine_addresses=MACHINES)
    check_environment_config({"node_env_vars": {"NCCL_DEBUG": "INFO"}}, machine_addresses=MACHINES)


def test_the_reserved_names_match_the_agent_s() -> None:
    """The agent is its own package, so the list is written twice: keep it one list."""
    import re  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    from faden_backend.services.inference.drivers.ray.schemas import RESERVED_ENV_KEYS  # noqa: PLC0415

    agent = Path(__file__).resolve().parents[2] / "agent" / "faden_agent" / "ray" / "env_policy.py"
    if not agent.exists():
        pytest.skip("the agent package is not next to the backend here")
    block = re.search(r"RESERVED_ENV_KEYS = frozenset\(\{(.*?)\}\)", agent.read_text(encoding="utf-8"), re.S)
    assert block is not None
    assert set(re.findall(r'"([A-Z_]+)"', block.group(1))) == set(RESERVED_ENV_KEYS)


@pytest.mark.parametrize("key", ["serve_http_host", "dashboard_host"])
def test_a_host_must_be_loopback_or_one_of_the_clusters_machines(key: str) -> None:
    for fine in ("127.0.0.1", "::1", "localhost", "10.0.0.5", "GPU-1.lan", "[::1]"):
        check_environment_config({key: fine}, machine_addresses=MACHINES)
    for bad in ("attacker.example", "203.0.113.9", "0.0.0.0", "", "10.0.0.50"):
        with pytest.raises(EnvironmentConfigRefused, match=key):
            check_environment_config({key: bad}, machine_addresses=MACHINES)


def test_a_host_the_cluster_already_has_is_left_alone() -> None:
    check_environment_config(
        {"serve_http_host": "203.0.113.9", "head_port": 7000},
        machine_addresses=MACHINES, previous={"serve_http_host": "203.0.113.9"},
    )


def test_an_old_config_with_extra_ray_start_args_still_loads() -> None:
    config = RayEnvironmentConfig.model_validate({"extra_ray_start_args": {"--x": "1"}, "head_port": 7000})
    assert config.head_port == 7000
    assert not hasattr(config, "extra_ray_start_args")


async def test_the_api_refuses_a_serve_host_that_is_not_the_clusters(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession
) -> None:
    await _operator(dbsession, fastapi_app)
    cp = await ControlPlaneDAO(dbsession).create(name="cp-host", driver="ray")
    await dbsession.flush()

    r = await client.post(f"{API}/environments", json={
        "control_plane_id": str(cp.id), "name": "e1", "config": {"serve_http_host": "attacker.example"},
    })
    assert r.status_code == 409, r.text
    assert "serve_http_host" in r.json()["detail"]

    r = await client.post(f"{API}/environments", json={
        "control_plane_id": str(cp.id), "name": "e2", "config": {"node_env_vars": {"RAY_AUTH_MODE": "disabled"}},
    })
    assert r.status_code == 409 and "RAY_AUTH_MODE" in r.json()["detail"]

    r = await client.post(f"{API}/environments", json={"control_plane_id": str(cp.id), "name": "e3"})
    assert r.status_code == 201, r.text
    env_id = r.json()["id"]
    r = await client.patch(f"{API}/environments/{env_id}", json={"config": {"dashboard_host": "0.0.0.0"}})
    assert r.status_code == 409, r.text
    r = await client.patch(f"{API}/environments/{env_id}", json={"config": {"dashboard_host": "127.0.0.1"}})
    assert r.status_code == 200, r.text


async def test_a_pinned_host_that_is_not_the_clusters_is_not_followed_at_deploy_time(
    dbsession: AsyncSession,
) -> None:
    """A cluster saved before the API refused it keeps the value; the driver stops using it."""
    from types import SimpleNamespace  # noqa: PLC0415

    from faden_backend.services.inference.drivers.ray import deployment as dep  # noqa: PLC0415

    env = await _cluster(dbsession, "guard-runtime")
    env.config_json = {"serve_http_host": "203.0.113.9"}
    await dbsession.flush()
    manager = dep.RayDeploymentManager()
    facts = SimpleNamespace(environment=env, serve_host_refused=False)

    await manager._check_pinned_serve_host(dbsession, facts)

    assert facts.serve_host_refused is True
    assert manager._environment_config(facts).serve_http_host is None
    assert manager._serve_options(facts)["http_options"]["host"] == dep._WILDCARD_BIND
