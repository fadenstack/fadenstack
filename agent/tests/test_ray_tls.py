"""Ray's mutual TLS between a cluster's machines, on the agent's side.

The runtime container runs Ray with ``RAY_USE_TLS`` when the backend says so;
its key and certificate are made just before the container is, because Ray
reads them only when it starts. Switching TLS either way recreates the
container; a command that does not say leaves it as it is.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from faden_agent.ray import ray_tls
from faden_agent.ray.container import RayContainerRuntime, RuntimeBundleSpec
from faden_agent.ray.manager import RayManager
from faden_agent.runtimes import ContainerRuntimeError
from tests.test_ray_container import _FakeRuntime, _payload


class _Material:
    def __init__(self, host_dir: str = "/var/lib/faden/ray-tls/env-1") -> None:
        self.host_dir = host_dir
        self.prepared = 0

    async def prepare(self) -> None:
        self.prepared += 1


def _existing(runtime: _FakeRuntime, spec: RuntimeBundleSpec, *, tls: bool) -> None:
    env = ["RAY_AUTH_TOKEN_PATH=/var/run/faden/ray/cluster.token"]
    if tls:
        env += [f"{k}={v}" for k, v in ray_tls.container_env().items()]
    runtime.containers[spec.name] = {
        "Image": spec.digest,
        "State": {"Running": True},
        "Config": {"Env": env},
        "Mounts": [{"Destination": "/models"}, {"Destination": "/var/run/faden/ray"}],
    }


def _container(runtime: _FakeRuntime) -> RayContainerRuntime:
    return RayContainerRuntime(runtime=runtime, token_path="/var/run/faden/ray/cluster.token")


# ── the container ────────────────────────────────────────────────────────


@pytest.mark.anyio()
async def test_a_new_container_runs_ray_with_tls_and_its_certificate_made_for_it() -> None:
    runtime = _FakeRuntime()
    spec = RuntimeBundleSpec.from_payload(_payload())
    material = _Material()

    result = await _container(runtime).ensure_container(spec, ray_tls=material)

    assert result["created"] is True and result["ray_tls"] is True
    assert material.prepared == 1
    run = runtime.runs[-1]
    assert run["env"]["RAY_USE_TLS"] == "1"
    assert run["env"]["RAY_TLS_CA_CERT"] == "/etc/faden-ray-tls/ca.pem"
    assert run["env"]["RAY_TLS_SERVER_CERT"] == "/etc/faden-ray-tls/node.crt"
    assert run["env"]["RAY_TLS_SERVER_KEY"] == "/etc/faden-ray-tls/node.key"
    assert "/var/lib/faden/ray-tls/env-1:/etc/faden-ray-tls:ro" in run["volumes"]


@pytest.mark.anyio()
async def test_a_running_tls_container_is_kept_and_its_certificate_not_remade() -> None:
    runtime = _FakeRuntime()
    spec = RuntimeBundleSpec.from_payload(_payload())
    _existing(runtime, spec, tls=True)
    material = _Material()

    result = await _container(runtime).ensure_container(spec, ray_tls=material)

    assert result["created"] is False and result["ray_tls"] is True
    assert material.prepared == 0
    assert runtime.runs == []


@pytest.mark.anyio()
async def test_switching_tls_on_recreates_the_container() -> None:
    runtime = _FakeRuntime()
    spec = RuntimeBundleSpec.from_payload(_payload())
    _existing(runtime, spec, tls=False)
    material = _Material()

    result = await _container(runtime).ensure_container(spec, ray_tls=material)

    assert result["created"] is True
    assert material.prepared == 1
    assert runtime.runs[-1]["env"]["RAY_USE_TLS"] == "1"


@pytest.mark.anyio()
async def test_switching_tls_off_recreates_the_container_without_it() -> None:
    runtime = _FakeRuntime()
    spec = RuntimeBundleSpec.from_payload(_payload())
    _existing(runtime, spec, tls=True)

    result = await _container(runtime).ensure_container(spec, ray_tls=False)

    assert result["created"] is True and result["ray_tls"] is False
    run = runtime.runs[-1]
    assert "RAY_USE_TLS" not in run["env"]
    assert not any(v.endswith(":/etc/faden-ray-tls:ro") for v in run["volumes"])


@pytest.mark.anyio()
async def test_a_command_that_does_not_say_leaves_the_container_as_it_is() -> None:
    """An older backend, or a command that is not about the cluster's Ray."""
    for tls in (True, False):
        runtime = _FakeRuntime()
        spec = RuntimeBundleSpec.from_payload(_payload())
        _existing(runtime, spec, tls=tls)

        result = await _container(runtime).ensure_container(spec)

        assert result["created"] is False, tls
        assert "ray_tls" not in result, "nothing said, nothing claimed"
        assert runtime.runs == []


@pytest.mark.anyio()
async def test_settings_cannot_turn_auth_or_tls_off_in_the_container() -> None:
    """``node_env_vars`` reach the container as the bundle's env or as ``env``.

    The merge ran before ``setdefault``, so a cluster setting of
    ``RAY_AUTH_MODE=disabled`` won; the host path already stripped it.
    """
    hostile = {
        "RAY_AUTH_MODE": "disabled",
        "RAY_AUTH_TOKEN_PATH": "/tmp/mine",
        "RAY_USE_TLS": "1",
        "RAY_TLS_SERVER_CERT": "/tmp/cert",
        "CUDA_VISIBLE_DEVICES": "0,1",
        "NCCL_DEBUG": "INFO",
    }
    for how in ("bundle", "env"):
        runtime = _FakeRuntime()
        payload = _payload()
        if how == "bundle":
            payload["env"] = dict(hostile)
        spec = RuntimeBundleSpec.from_payload(payload)
        await _container(runtime).ensure_container(spec, env=dict(hostile) if how == "env" else None, ray_tls=False)
        env = runtime.runs[-1]["env"]
        assert env["RAY_AUTH_MODE"] == "token", how
        assert env["RAY_AUTH_TOKEN_PATH"] == "/var/run/faden/ray/cluster.token", how
        assert "RAY_USE_TLS" not in env and "RAY_TLS_SERVER_CERT" not in env, how
        assert "CUDA_VISIBLE_DEVICES" not in env, how
        assert env["NCCL_DEBUG"] == "INFO", "other tuning still passes"


def test_the_host_and_container_paths_refuse_the_same_names() -> None:
    from faden_agent.ray.env_policy import RESERVED_ENV_KEYS  # noqa: PLC0415

    assert RayManager._RESERVED_ENV_KEYS is RESERVED_ENV_KEYS
    host = object.__new__(RayManager)
    host._token_file = Path("/t")
    env = host.start_env({key: "x" for key in RESERVED_ENV_KEYS} | {"NCCL_DEBUG": "INFO"})
    assert env["RAY_AUTH_MODE"] == "token"
    assert env["NCCL_DEBUG"] == "INFO"
    assert not (set(env) & RESERVED_ENV_KEYS) - {"RAY_AUTH_MODE", "RAY_AUTH_TOKEN_PATH"}


# ── what the manager makes of the command ────────────────────────────────


def _manager(tls: Any = None) -> RayManager:
    manager = object.__new__(RayManager)
    manager.ray_tls = tls
    return manager


def test_the_command_says_on_off_or_nothing(tmp_path: Path) -> None:
    issuer = ray_tls.RayTls(base_dir=tmp_path, http=httpx.AsyncClient(), credential=lambda: "c")
    manager = _manager(issuer)
    assert manager._ray_tls_for({}) is None
    assert manager._ray_tls_for({"ray_tls": False}) is False
    material = manager._ray_tls_for({"ray_tls": {"environment_id": "env-1"}})
    assert material.host_dir == str(tmp_path / "env-1")
    with pytest.raises(ContainerRuntimeError, match="TLS"):
        _manager(None)._ray_tls_for({"ray_tls": {"environment_id": "env-1"}})


# ── the certificate ──────────────────────────────────────────────────────


def _backend(seen: list[httpx.Request], *, status: int = 200) -> httpx.MockTransport:
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test Ray CA")])

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if status != 200:
            return httpx.Response(status, text="Not a member of that cluster.")
        csr = x509.load_pem_x509_csr(json.loads(request.content)["csr_pem"].encode())
        import datetime as dt  # noqa: PLC0415

        now = dt.datetime.now(dt.UTC)
        cert = (
            x509.CertificateBuilder().subject_name(csr.subject).issuer_name(ca_name)
            .public_key(csr.public_key()).serial_number(1)
            .not_valid_before(now).not_valid_after(now + dt.timedelta(days=1))
            .sign(ca_key, hashes.SHA256())
        )
        return httpx.Response(200, json={
            "certificate_pem": cert.public_bytes(serialization.Encoding.PEM).decode(),
            "ca_pem": "-----BEGIN CERTIFICATE-----\nca\n-----END CERTIFICATE-----\n",
            "names": ["10.0.0.1"],
        })

    return httpx.MockTransport(handler)


@pytest.mark.anyio()
async def test_the_key_is_made_here_and_only_the_request_leaves(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    async with httpx.AsyncClient(transport=_backend(seen), base_url="http://backend") as http:
        issuer = ray_tls.RayTls(base_dir=tmp_path, http=http, credential=lambda: "node-cred")
        await issuer.material("env-1").prepare()

    (request,) = seen
    assert request.url.path == "/api/node-files/ray-tls/certificate"
    assert request.headers["Authorization"] == "Bearer node-cred"
    body = json.loads(request.content)
    assert body["environment_id"] == "env-1"
    assert "PRIVATE KEY" not in request.content.decode()

    directory = tmp_path / "env-1"
    key = serialization.load_pem_private_key((directory / "node.key").read_bytes(), password=None)
    cert = x509.load_pem_x509_certificate((directory / "node.crt").read_bytes())
    assert cert.public_key().public_numbers() == key.public_key().public_numbers()
    assert (directory / "ca.pem").read_text().startswith("-----BEGIN CERTIFICATE-----")
    if sys.platform != "win32":
        assert os.stat(directory / "node.key").st_mode & 0o777 == 0o600
    assert not list(directory.glob("*.tmp"))


@pytest.mark.anyio()
async def test_a_refusal_stops_the_container_from_being_made(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    async with httpx.AsyncClient(transport=_backend(seen, status=403), base_url="http://backend") as http:
        issuer = ray_tls.RayTls(base_dir=tmp_path, http=http, credential=lambda: "node-cred")
        runtime = _FakeRuntime()
        spec = RuntimeBundleSpec.from_payload(_payload())
        with pytest.raises(RuntimeError, match="403"):
            await _container(runtime).ensure_container(spec, ray_tls=issuer.material("env-1"))
    assert runtime.runs == []
    assert not (tmp_path / "env-1" / "node.key").exists()


@pytest.mark.anyio()
async def test_no_credential_no_certificate(tmp_path: Path) -> None:
    issuer = ray_tls.RayTls(base_dir=tmp_path, http=httpx.AsyncClient(), credential=lambda: None)
    with pytest.raises(RuntimeError, match="credential"):
        await issuer.issue("env-1")
