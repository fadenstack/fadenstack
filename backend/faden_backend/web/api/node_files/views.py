"""Serve model files to node agents for air-gapped deployments.

The manifest describes the full HuggingFace cache tree so the agent
can reconstruct the proper layout (blobs, refs, snapshot symlinks).

Endpoints authenticate via the same Bearer credential that agents
use for their WebSocket stream, so no additional secrets are needed.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from urllib.parse import urlsplit
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from starlette import status

from faden_backend.db.dao.llm_dao import ModelDAO
from faden_backend.db.dao.node_control_dao import NodeControlDAO
from faden_backend.db.models.node_control import InfraNode
from faden_backend.services.docker.client import DockerService
from faden_backend.services import edge_trust
from faden_backend.services.inference import image_cache
from faden_backend.services.nodes import NodeControlService
from faden_backend.settings import settings

log = logging.getLogger(__name__)

router = APIRouter(prefix="/node-files", tags=["node-files"])

_CHUNK_SIZE = 256 * 1024  # 256 KiB streaming chunks

# Blob filenames: hex hash, possibly with algo prefix separated by hyphen
_SAFE_BLOB_RE = re.compile(r"^[a-fA-F0-9][a-fA-F0-9_-]*$")


# ------------------------------------------------------------------
# Authentication dependency — validates node Bearer credential
# ------------------------------------------------------------------

async def _authenticate_node(request: Request) -> InfraNode:
    """Verify the caller is a registered node agent."""
    session_factory = getattr(request.app.state, "db_session_factory", None)
    if session_factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable.",
        )
    async with session_factory() as session:
        dao = NodeControlDAO(session)
        service = NodeControlService(
            dao=dao,
            pepper=settings.settings_master_key,
            enrollment_ttl_minutes=settings.node_enrollment_ttl_minutes,
            default_command_timeout_sec=settings.node_command_default_timeout_sec,
        )
        try:
            node, _credential = await service.authenticate_agent(
                authorization=request.headers.get("authorization"),
            )
        except PermissionError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid node credential.",
            ) from exc
        return node


# ------------------------------------------------------------------
# Helpers (delegated to services.llm.artifacts)
# ------------------------------------------------------------------

from faden_backend.services.llm.artifacts import (
    build_cache_manifest as _build_cache_manifest,
    model_cache_dir as _model_cache_dir,
    resolve_blob_hash as _resolve_blob_hash,
)


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

@router.get("/whoami", name="node_whoami")
async def node_whoami(node: InfraNode = Depends(_authenticate_node)) -> dict[str, Any]:
    """Which machine this credential belongs to -- and that it still works.

    Lets an already-enrolled agent tell "I am a member here" from "I need to
    ask to join" without changing anything. Re-running the install line is
    how an operator upgrades, and it used to file a fresh join request for a
    machine that was already in the fleet.
    """
    return {"node_id": str(node.id), "agent_id": node.agent_id, "host": node.host}


def agent_log_sink(loki_base_url: str, agent_loki_url: str, request_host: str | None) -> str | None:
    """The Loki address a node agent should push to.

    The backend's own address for Loki is usually one only it can use: a
    loopback address in development, the container name ``faden-loki`` in
    the deployment. Neither means anything on a machine across the network.
    The machine does know one address that works -- the one it reached this
    backend on -- and in both setups Loki is published on that same host, so
    keep Loki's port and swap the host.
    """
    if agent_loki_url.strip():
        return agent_loki_url.strip()
    parts = urlsplit(loki_base_url.strip())
    host = parts.hostname
    if not host:
        return None
    local_only = (
        host in ("localhost", "::1")
        or host.startswith("127.")
        or "." not in host  # a container or compose service name
    )
    if not local_only:
        return loki_base_url.strip().rstrip("/")
    if not request_host:
        return None
    port = parts.port or (443 if parts.scheme == "https" else 3100)
    netloc = f"[{request_host}]" if ":" in request_host else request_host
    return f"{parts.scheme or 'http'}://{netloc}:{port}"


#: Where an agent that asks ``via=backend`` pushes its logs, relative to the
#: backend address it already uses (so over HTTPS when the front door is).
LOG_PUSH_BASE = "/api/node-files/logs"
#: A push larger than this is refused (an agent batches every few seconds).
_MAX_LOG_PUSH = 8 * 1024 * 1024


@router.get("/log-sink", name="node_log_sink")
async def node_log_sink(
    request: Request,
    via: str | None = None,
    _node: InfraNode = Depends(_authenticate_node),
) -> dict[str, Any]:
    """Where this machine should send its logs.

    The one-line install writes no Loki address, and an agent without one
    forwards nothing: machines installed that way disappeared from the logs
    page without a word. The agent asks here instead when it has none.

    An agent that can (``via=backend``, 0.1.17 and later) is sent to the
    backend itself: its pushes go over its authenticated connection, HTTPS
    when the front door is, and Loki stays on the server's loopback address.
    Pushed straight to Loki they were plain HTTP to a Loki without
    authentication -- and failed wherever Loki was not published at all.
    """
    if via == "backend" and not settings.agent_loki_url.strip() and settings.loki_base_url.strip():
        return {"loki_url": LOG_PUSH_BASE, "via": "backend"}
    return {
        "loki_url": agent_log_sink(
            settings.loki_base_url, settings.agent_loki_url, request.url.hostname,
        ),
    }


_loki_http: Any = None


def _loki_client() -> Any:
    global _loki_http  # noqa: PLW0603
    import httpx  # noqa: PLC0415

    if _loki_http is None:
        from faden_backend.services.tls import default_httpx_verify  # noqa: PLC0415

        _loki_http = httpx.AsyncClient(timeout=15, verify=default_httpx_verify())
    return _loki_http


def stamp_streams(payload: Any, host: str) -> dict[str, Any]:
    """The push as Loki takes it, with every stream's ``host`` set to the machine that sent it.

    ``host`` is what the logs page selects a machine's logs by; taken from
    the machine's credential, one machine cannot write into another's.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("streams"), list):
        raise ValueError("expected Loki's JSON push format: {\"streams\": [...]}")
    streams = []
    for stream in payload["streams"]:
        if not isinstance(stream, dict) or not isinstance(stream.get("values"), list):
            raise ValueError("every stream needs values")
        labels = stream.get("stream") if isinstance(stream.get("stream"), dict) else {}
        clean = {str(k)[:64]: str(v)[:256] for k, v in labels.items() if k != "host"}
        clean["host"] = host
        streams.append({"stream": clean, "values": stream["values"]})
    return {"streams": streams}


@router.post(LOG_PUSH_BASE.removeprefix("/api/node-files") + "/loki/api/v1/push", name="node_log_push")
async def node_log_push(
    request: Request,
    node: InfraNode = Depends(_authenticate_node),
) -> Response:
    """A machine's log push, forwarded to Loki on the server."""
    import json  # noqa: PLC0415

    body = await request.body()
    if len(body) > _MAX_LOG_PUSH:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Log push too large.")
    try:
        payload = stamp_streams(json.loads(body or b"{}"), str(node.host or node.agent_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    base = settings.loki_base_url.rstrip("/")
    try:
        answer = await _loki_client().post(f"{base}/loki/api/v1/push", json=payload)
    except Exception as exc:  # noqa: BLE001 - the agent retries on its next flush
        log.warning("Forwarding logs from %s to Loki failed: %s", node.host, exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Loki is not reachable.") from exc
    if answer.status_code >= 300:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Loki answered {answer.status_code}.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/server-trust", name="node_server_trust")
async def node_server_trust(_node: InfraNode = Depends(_authenticate_node)) -> dict[str, Any]:
    """The authority this backend's certificate comes from, for the machine to keep.

    Offered while HTTPS is still off as well: the machine has to hold it
    before the switch, since afterwards the plain-HTTP address only
    redirects to a certificate it could not otherwise check.
    """
    pem = edge_trust.ca_pem()
    return {
        "https": edge_trust.https_on(),
        "ca_pem": pem.decode("utf-8") if pem is not None else None,
        "ca_sha256": edge_trust.ca_sha256(),
    }


@router.get("/hf-token", name="node_hf_token")
async def node_hf_token(
    request: Request,
    node: InfraNode = Depends(_authenticate_node),
) -> dict[str, Any]:
    """This server's Hugging Face token, for a machine downloading a model itself.

    Only for a machine of a cluster whose machines download models themselves
    (``services/inference/machine_downloads.py``); any other machine gets its
    models from this server and has no use for the token. The agent asks for
    each download and keeps it only for that download.
    """
    from faden_backend.services.inference import machine_downloads  # noqa: PLC0415
    from faden_backend.services.llm import hf_token  # noqa: PLC0415

    async with request.app.state.db_session_factory() as session:
        if not await machine_downloads.node_may_download(session, node.id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This machine's cluster does not download models itself: it copies them from the server.",
            )
        token, source = await hf_token.resolve(session)
    log.info("Handed the Hugging Face token (%s) to %s for a download", source or "none", node.host)
    return {"token": token}


@router.get("/machine-tls", name="node_machine_tls")
async def node_machine_tls(
    request: Request,
    node: InfraNode = Depends(_authenticate_node),
) -> dict[str, Any]:
    """What this machine's model-TLS proxy needs to know, asked every minute.

    ``trust_pem`` is every machine CA to trust (the gateway's certificate
    comes from one of them); ``issuer_key_id`` says which one signs now and
    ``names`` what a certificate for this machine names -- when either differs
    from the certificate the machine holds, it asks for a new one.
    """
    from faden_backend.services.machine_tls import authority, store  # noqa: PLC0415

    if not settings.machine_tls_enabled:
        return {"enabled": False}
    async with request.app.state.db_session_factory() as session:
        issuer = await store.issuer_key_id(session)
        if issuer is None:
            await store.maintain(session, settings.settings_master_key)
            await session.commit()
            issuer = await store.issuer_key_id(session)
        trust = await store.trust_bundle(session)
    return {
        "enabled": True,
        "trust_pem": trust,
        "trust_sha256": authority.bundle_sha256(trust),
        "issuer_key_id": issuer,
        "names": store.machine_names(node.host),
        "client_name": authority.GATEWAY_CLIENT,
        "renew_before_sec": int(authority.RENEW_BEFORE.total_seconds()),
    }


@router.post("/machine-tls/certificate", name="node_machine_tls_certificate")
async def node_machine_tls_certificate(
    request: Request,
    node: InfraNode = Depends(_authenticate_node),
) -> dict[str, Any]:
    """Sign this machine's certificate request for its model-TLS proxy.

    The key never leaves the machine: the request carries only the public
    half. The certificate names what the backend records for the machine,
    whatever the request names.
    """
    from faden_backend.services.machine_tls import authority, store  # noqa: PLC0415

    if not settings.machine_tls_enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Model traffic TLS is switched off.")
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Expected a JSON body.") from exc
    csr_pem = body.get("csr_pem") if isinstance(body, dict) else None
    if not isinstance(csr_pem, str) or len(csr_pem) > 16 * 1024:
        raise HTTPException(status_code=400, detail="csr_pem is required.")
    async with request.app.state.db_session_factory() as session:
        try:
            issued = await store.issue_for_node(
                session, settings.settings_master_key, node_id=node.id, host=node.host, csr_pem=csr_pem,
            )
        except store.TooSoonError as exc:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="A certificate was issued to this machine less than a minute ago.",
            ) from exc
        except authority.AuthorityError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()
    log.info("Model-TLS certificate issued to machine %s for %s", node.id, ", ".join(issued.names))
    return {
        "certificate_pem": issued.cert_pem,
        "trust_pem": issued.trust_pem,
        "trust_sha256": authority.bundle_sha256(issued.trust_pem),
        "not_after": issued.not_after.isoformat(),
        "names": issued.names,
        "issuer_key_id": issued.issuer_key_id,
    }


@router.post("/ray-tls/certificate", name="node_ray_tls_certificate")
async def node_ray_tls_certificate(
    request: Request,
    node: InfraNode = Depends(_authenticate_node),
) -> dict[str, Any]:
    """Sign this machine's certificate for its Ray cluster's mutual TLS (``RAY_USE_TLS``).

    Only for a member of a cluster that has Ray TLS switched on. The names
    are the backend's: the fabric address the cluster runs on, the machine's
    own, loopback -- not whatever the request names.
    """
    from sqlalchemy import select  # noqa: PLC0415

    from faden_backend.db.models.inference import (  # noqa: PLC0415
        InferenceEnvironment,
        InferenceEnvironmentNode,
    )
    from faden_backend.services.inference.drivers.ray.schemas import RayEnvironmentConfig  # noqa: PLC0415
    from faden_backend.services.machine_tls import authority, ray_ca  # noqa: PLC0415

    try:
        body = await request.json()
        environment_id = uuid.UUID(str(body.get("environment_id")))
        csr_pem = str(body["csr_pem"])
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail="environment_id and csr_pem are required.") from exc
    if len(csr_pem) > 16 * 1024:
        raise HTTPException(status_code=400, detail="csr_pem is too large.")
    async with request.app.state.db_session_factory() as session:
        environment = await session.get(InferenceEnvironment, environment_id)
        member = (await session.execute(
            select(InferenceEnvironmentNode).where(
                InferenceEnvironmentNode.environment_id == environment_id,
                InferenceEnvironmentNode.node_id == node.id,
            )
        )).scalars().first()
        if environment is None or member is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of that cluster.")
        config = RayEnvironmentConfig.model_validate(environment.config_json or {})
        if not config.ray_tls:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Ray TLS is off for that cluster.",
            )
        binding = (((environment.observed_status_json or {}).get("resolved_fabric") or {})
                   .get("node_bindings") or {}).get(str(node.id)) or {}
        names = [
            str(binding.get("ip") or ""),
            *store_names(node),
            str((node.capabilities_json or {}).get("hostname") or ""),
            "127.0.0.1",
            "localhost",
        ]
        try:
            issued = await ray_ca.issue(
                session, settings.settings_master_key,
                environment_id=environment_id, csr_pem=csr_pem, names=names,
            )
        except authority.AuthorityError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()
    log.info("Ray TLS certificate issued to machine %s for %s", node.id, ", ".join(issued.names))
    return {
        "certificate_pem": issued.cert_pem,
        "ca_pem": issued.ca_pem,
        "not_after": issued.not_after.isoformat(),
        "names": issued.names,
    }


def store_names(node: InfraNode) -> list[str]:
    """The names the machine's own certificate carries: the address the backend reaches it by."""
    from faden_backend.services.machine_tls import store  # noqa: PLC0415

    return store.machine_names(node.host)


@router.get(
    "/models/{model_id}/manifest",
    name="node_model_manifest",
)
async def model_manifest(
    model_id: uuid.UUID,
    request: Request,
    _node: InfraNode = Depends(_authenticate_node),
) -> dict[str, Any]:
    """Return the HF-cache-aware manifest for a model.

    The manifest describes blobs (actual content), refs (branch →
    commit), and snapshot symlink trees so the node agent can
    faithfully reconstruct the HF cache layout.
    """
    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        dao = ModelDAO(session)
        model = await dao.get(model_id)
        if model is None:
            raise HTTPException(status_code=404, detail="Model not found.")
        if not model.hf_repo_id:
            raise HTTPException(
                status_code=400,
                detail="Model has no HF repo ID — cannot serve files.",
            )

    model_dir = _model_cache_dir(model.hf_repo_id)
    if model_dir is None:
        raise HTTPException(
            status_code=404,
            detail=f"No cached model found for {model.hf_repo_id}.",
        )

    manifest = _build_cache_manifest(model_dir)
    manifest["hf_repo_id"] = model.hf_repo_id
    return manifest


@router.get(
    "/models/{model_id}/blob/{blob_hash}",
    name="node_model_blob",
)
async def model_blob(
    model_id: uuid.UUID,
    blob_hash: str,
    request: Request,
    _node: InfraNode = Depends(_authenticate_node),
) -> StreamingResponse:
    """Stream a single blob from the model's HF cache ``blobs/`` dir.

    ``blob_hash`` is the filename under ``blobs/`` (as returned
    by the ``/manifest`` endpoint).
    Supports ``Range`` header for resumable downloads.
    """
    if not _SAFE_BLOB_RE.match(blob_hash):
        raise HTTPException(status_code=400, detail="Invalid blob hash.")

    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        dao = ModelDAO(session)
        model = await dao.get(model_id)
        if model is None:
            raise HTTPException(status_code=404, detail="Model not found.")
        if not model.hf_repo_id:
            raise HTTPException(status_code=400, detail="Model has no HF repo ID.")

    model_dir = _model_cache_dir(model.hf_repo_id)
    if model_dir is None:
        raise HTTPException(status_code=404, detail="Model cache not found.")

    # Resolve and enforce blobs/ containment
    blobs_dir = (model_dir / "blobs").resolve()
    target = (model_dir / "blobs" / blob_hash).resolve()
    if not str(target).startswith(str(blobs_dir)):
        raise HTTPException(status_code=400, detail="Invalid blob hash.")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Blob not found.")

    file_size = target.stat().st_size

    # Parse Range header for resumable downloads
    range_header = request.headers.get("range")
    start = 0
    end = file_size - 1
    status_code = 200
    headers: dict[str, str] = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": f'attachment; filename="{blob_hash}"',
    }

    if range_header and range_header.startswith("bytes="):
        range_spec = range_header[6:]
        parts = range_spec.split("-", 1)
        if parts[0]:
            start = int(parts[0])
        if len(parts) > 1 and parts[1]:
            end = int(parts[1])
        if start > end or start >= file_size:
            raise HTTPException(status_code=416, detail="Range not satisfiable.")
        end = min(end, file_size - 1)
        status_code = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"

    content_length = end - start + 1
    headers["Content-Length"] = str(content_length)

    def _stream():
        with open(target, "rb") as fh:
            fh.seek(start)
            remaining = content_length
            while remaining > 0:
                chunk = fh.read(min(_CHUNK_SIZE, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(
        _stream(),
        status_code=status_code,
        media_type="application/octet-stream",
        headers=headers,
    )


# ------------------------------------------------------------------
# Image transfer — stream `docker save` output for air-gapped nodes
# ------------------------------------------------------------------

# Validate image references: allow registry/repo:tag but reject injection.
_SAFE_IMAGE_RE = re.compile(
    r"^[a-zA-Z0-9][a-zA-Z0-9._/-]*(?::[a-zA-Z0-9._-]+)?$"
)


@router.get(
    "/images/save",
    name="node_image_save",
)
async def image_save(
    request: Request,
    image: str,
    tag: str = "latest",
    expect_id: str | None = None,
    expect_rootfs: str | None = None,
    _node: InfraNode = Depends(_authenticate_node),
) -> Response:
    """Hand a node the runtime image as a tar it can ``docker load``.

    Served from a one-time export when there is room for one, so the
    transfer has a real length and can be resumed with ``Range``; otherwise
    streamed live as before. ``expect_id``/``expect_rootfs`` are the pin the
    node was told to run: when this server holds a different build it says so
    with a 409 instead of sending twelve gigabytes the node will refuse.
    """
    ref = f"{image}:{tag}"
    if not _SAFE_IMAGE_RE.match(ref):
        raise HTTPException(status_code=400, detail="Invalid image reference.")

    docker: DockerService = request.app.state.docker

    # Verify the image exists locally before starting the stream.
    try:
        info = await docker.client.images.inspect(ref)
    except Exception:  # aiodocker raises DockerError(404, …)
        raise HTTPException(
            status_code=404,
            detail=f"Image {ref} not found on this host.",
        )

    try:
        image_cache.verify_expected(info, expect_id=expect_id, expect_rootfs=expect_rootfs)
    except image_cache.ImageMismatch as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    image_id = str(info.get("Id") or "")
    unpacked_size = int(info.get("Size") or info.get("VirtualSize") or 0)
    if image_id:
        try:
            exported = await image_cache.ensure_export(docker, ref, image_id, unpacked_size)
        except Exception:  # never let the cache stand between a node and its image
            log.exception("Could not export %s; streaming it instead", ref)
            exported = None
        if exported is not None:
            return FileResponse(
                exported,
                media_type="application/x-tar",
                filename=ref.replace("/", "_").replace(":", "_") + ".tar",
                headers={"X-Image-Id": image_id},
            )

    # No export: stream live. Not resumable, and the only size known up front
    # is the unpacked one, which overstates the wire size about threefold.
    image_size = unpacked_size

    async def _stream_docker_save():
        async with docker.client.images.export_image(ref) as stream:
            while True:
                chunk = await stream.read(_CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk

    safe_filename = ref.replace("/", "_").replace(":", "_") + ".tar"
    resp_headers: dict[str, str] = {
        "Content-Disposition": f'attachment; filename="{safe_filename}"',
    }
    if image_size:
        resp_headers["X-Image-Size"] = str(image_size)
    return StreamingResponse(
        _stream_docker_save(),
        media_type="application/x-tar",
        headers=resp_headers,
    )
