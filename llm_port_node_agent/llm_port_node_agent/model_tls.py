"""The machine's model-TLS proxy: the gateway reaches the models here only over mutual TLS.

A model served on this machine -- Ray Serve's proxy on a cluster's head, or a
standalone vLLM container -- listens on the loopback address only. In front of
it, on the address the backend publishes, runs nginx (``nginx:1.31.6-alpine``,
pinned per architecture, the same product that fronts the server) with:

- a server certificate for this machine's address, signed by the machine CA
  the backend keeps; the key is made here and never leaves this machine --
  the backend signs a request carrying only the public half;
- ``ssl_verify_client on`` against that CA, and only the gateway's name let
  through. The gateway's certificate is for client use only and every
  machine's for server use only, so no machine's key opens another's models.

The TLS is nginx's; this module makes the key, asks for the certificate,
renews it (the trust loop asks every minute whether the backend wants a new
one: a new CA, a new address, ten days before the end), writes nginx's
configuration and keeps the container running. nginx runs in its own
container with a restart policy, so the proxy outlives agent restarts and
upgrades.

The image comes the way the runtime image does, and is checked the same way
(its layers against the pin): from this machine if present, else from the
server's copy (``/api/node-files/images/save``), else from the registry by
digest -- so an air-gapped machine needs only the server to hold it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import platform
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

CONTAINER = "llm-port-model-tls"
INFO_PATH = "/api/node-files/machine-tls"
CERTIFICATE_PATH = "/api/node-files/machine-tls/certificate"

NGINX_VERSION = "1.31.6-alpine"
#: The multi-architecture index ``nginx:1.31.6-alpine`` pointed to when pinned.
NGINX_INDEX = "sha256:df221db836e1754089190208cee7eeda94f233197056426eda74a43ab1abeac2"
#: Content identity per architecture: sha256 over the ordered layer diff IDs
#: (``ray.container.compute_rootfs_digest``; the backend computes the same).
NGINX_ROOTFS = {
    "x86_64": "sha256:268c8b4b4baa285e4082ade32c9f2f69a013617ea0b7d0ff25f711bdb10750e7",
    "aarch64": "sha256:2f89fd28b672e74bd0d11deb485485881ca606ccc04a6a14b05cca1272f0459a",
}
#: The name the server holds each architecture's copy under (``llmport`` CLI prepares them).
SERVER_IMAGE = "llmport/model-tls-nginx"

#: Inside the container.
_MOUNT = "/etc/llmport-model-tls"
KEY_FILE = "machine.key"
CERT_FILE = "machine.crt"
TRUST_FILE = "trust.pem"
CONF_FILE = "nginx.conf"
ROUTES_FILE = "routes.json"


class ModelTlsError(RuntimeError):
    """The proxy cannot be put in front of a model; the message says why."""


def machine_arch() -> str:
    machine = platform.machine().lower()
    return {"amd64": "x86_64", "arm64": "aarch64"}.get(machine, machine)


def server_image_tag(arch: str | None = None) -> str:
    return f"{SERVER_IMAGE}:{NGINX_VERSION}-{arch or machine_arch()}"


def rootfs_digest(layers: list[str]) -> str:
    return "sha256:" + hashlib.sha256("\n".join(layers).encode()).hexdigest()


def render_conf(routes: dict[int, int], *, client_name: str, ipv6: bool) -> str:
    """nginx's configuration: one TLS listener per published port, in front of its loopback port."""
    servers = []
    for public, internal in sorted(routes.items()):
        listen = f"        listen {public} ssl;\n"
        if ipv6:
            listen += f"        listen [::]:{public} ssl;\n"
        servers.append(
            "    server {\n"
            + listen
            + "        if ($llmport_client_ok = 0) { return 403; }\n"
            "        location / {\n"
            f"            proxy_pass http://127.0.0.1:{internal};\n"
            "            proxy_set_header Host $http_host;\n"
            "            proxy_set_header X-Forwarded-Proto https;\n"
            "            proxy_set_header Connection \"\";\n"
            "        }\n"
            "    }\n"
        )
    name = client_name.replace('"', "")
    return (
        "# Written by the node agent (model_tls.py); rewritten on every change.\n"
        "worker_processes auto;\n"
        "pid /tmp/llmport-model-tls.pid;\n"
        "error_log stderr notice;\n"
        "events { worker_connections 4096; }\n"
        "http {\n"
        "    access_log off;\n"
        "    server_tokens off;\n"
        "    client_max_body_size 0;\n"
        "    proxy_http_version 1.1;\n"
        "    proxy_buffering off;\n"
        "    proxy_request_buffering off;\n"
        "    proxy_read_timeout 3600s;\n"
        "    proxy_send_timeout 3600s;\n"
        "    ssl_protocols TLSv1.2 TLSv1.3;\n"
        f"    ssl_certificate {_MOUNT}/{CERT_FILE};\n"
        f"    ssl_certificate_key {_MOUNT}/{KEY_FILE};\n"
        f"    ssl_client_certificate {_MOUNT}/{TRUST_FILE};\n"
        "    ssl_verify_client on;\n"
        "    ssl_verify_depth 1;\n"
        "    ssl_session_cache shared:modeltls:1m;\n"
        "    map $ssl_client_s_dn $llmport_client_ok {\n"
        "        default 0;\n"
        f'        "~(^|,)CN={name}(,|$)" 1;\n'
        "    }\n"
        + "".join(servers)
        + "}\n"
    )


def _write(path: Path, data: bytes, *, mode: int) -> None:
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), mode)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    try:
        os.chmod(tmp, mode)
    except OSError:  # pragma: no cover - Windows has no such bits
        pass
    os.replace(tmp, path)


@dataclass
class _Held:
    """What the certificate on disk says."""

    not_after: datetime
    names: list[str]
    issuer_key_id: str | None


def _read_cert(pem: bytes) -> _Held | None:
    from cryptography import x509  # noqa: PLC0415

    try:
        cert = x509.load_pem_x509_certificate(pem)
    except ValueError:
        return None
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        names = [str(v.value) for v in san]
    except x509.ExtensionNotFound:
        names = []
    try:
        aki = cert.extensions.get_extension_for_class(x509.AuthorityKeyIdentifier).value.key_identifier
        issuer = aki.hex() if aki else None
    except x509.ExtensionNotFound:
        issuer = None
    return _Held(not_after=cert.not_valid_after_utc, names=names, issuer_key_id=issuer)


class ModelTls:
    """This machine's certificate and its model-TLS proxy."""

    def __init__(
        self,
        *,
        runtime: Any,
        directory: Path,
        host_directory: str | None = None,
        image_loader: Callable[..., Awaitable[Any]] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._runtime = runtime
        self._dir = Path(directory)
        #: The same directory as the container runtime's host sees it (the agent may itself run in a container).
        self._host_dir = host_directory or str(self._dir)
        self._image_loader = image_loader
        self._now = now or (lambda: datetime.now(UTC))
        self._lock = asyncio.Lock()
        self.enabled: bool | None = None  # None: the backend has not answered yet
        self.client_name = "fadenstack-gateway"
        self._renew_before = timedelta(days=10)
        self._image: str | None = None
        self.last_error: str | None = None
        #: Asks the backend now (the service's trust refresh); used when a
        #: route is wanted before the minute's round has fetched a certificate.
        self.refresher: Callable[[], Awaitable[Any]] | None = None

    # ── files ───────────────────────────────────────────────────────

    def _path(self, name: str) -> Path:
        return self._dir / name

    def routes(self) -> dict[int, int]:
        try:
            raw = json.loads(self._path(ROUTES_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {int(k): int(v) for k, v in (raw or {}).items()}

    def _save_routes(self, routes: dict[int, int]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        _write(self._path(ROUTES_FILE), json.dumps({str(k): v for k, v in sorted(routes.items())}).encode(), mode=0o644)

    def has_certificate(self) -> bool:
        return all(self._path(n).is_file() for n in (KEY_FILE, CERT_FILE, TRUST_FILE))

    def _ensure_key(self) -> bytes:
        from cryptography.hazmat.primitives import serialization  # noqa: PLC0415
        from cryptography.hazmat.primitives.asymmetric import ec  # noqa: PLC0415

        path = self._path(KEY_FILE)
        if path.is_file():
            return path.read_bytes()
        self._dir.mkdir(parents=True, exist_ok=True)
        try:
            self._dir.chmod(0o755)
        except OSError:  # pragma: no cover
            pass
        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        # 0644 inside a 0755 directory would be readable by every local user;
        # the container reads it as root, so 0600 owned by the agent suffices.
        _write(path, pem, mode=0o600)
        log.info("Model-TLS key made in %s", path)
        return pem

    def _csr(self, key_pem: bytes, names: list[str]) -> str:
        from cryptography import x509  # noqa: PLC0415
        from cryptography.hazmat.primitives import hashes, serialization  # noqa: PLC0415
        from cryptography.x509.oid import NameOID  # noqa: PLC0415

        key = serialization.load_pem_private_key(key_pem, password=None)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, (names[0] if names else "machine")[:64])])
        csr = x509.CertificateSigningRequestBuilder().subject_name(subject).sign(key, hashes.SHA256())  # type: ignore[arg-type]
        return csr.public_bytes(serialization.Encoding.PEM).decode("ascii")

    # ── the certificate ─────────────────────────────────────────────

    def _needs_certificate(self, info: dict[str, Any]) -> str | None:
        """Why a new certificate is needed, or ``None``."""
        path = self._path(CERT_FILE)
        if not path.is_file() or not self._path(KEY_FILE).is_file():
            return "none held"
        held = _read_cert(path.read_bytes())
        if held is None:
            return "unreadable"
        if held.not_after - self._now() < self._renew_before:
            return "renewal due"
        if info.get("issuer_key_id") and held.issuer_key_id != info["issuer_key_id"]:
            return "another CA signs now"
        names = [str(n) for n in info.get("names") or []]
        if names and sorted(held.names) != sorted(names):
            return "the machine's address changed"
        return None

    async def refresh(self, http: httpx.AsyncClient, credential: str) -> bool:
        """Keep the certificate and trust current, and the proxy with them; ``True`` when anything changed."""
        headers = {"Authorization": f"Bearer {credential}"}
        try:
            response = await http.get(INFO_PATH, headers=headers)
        except httpx.HTTPError as exc:
            log.debug("Model TLS: backend not reachable (%s)", exc)
            return False
        if response.status_code == 404:  # a backend before the machine CA
            self.enabled = False
            return False
        response.raise_for_status()
        info = response.json()
        self.enabled = bool(info.get("enabled"))
        if not self.enabled:
            return False
        self.client_name = str(info.get("client_name") or self.client_name)
        if info.get("renew_before_sec"):
            self._renew_before = timedelta(seconds=int(info["renew_before_sec"]))
        async with self._lock:
            changed = False
            trust = str(info.get("trust_pem") or "").encode("ascii")
            if trust and (not self._path(TRUST_FILE).is_file() or self._path(TRUST_FILE).read_bytes() != trust):
                self._dir.mkdir(parents=True, exist_ok=True)
                _write(self._path(TRUST_FILE), trust, mode=0o644)
                changed = True
                log.info("Model TLS: machine CA trust updated")
            reason = self._needs_certificate(info)
            if reason is not None:
                key_pem = self._ensure_key()
                csr = self._csr(key_pem, [str(n) for n in info.get("names") or []])
                issued = await http.post(CERTIFICATE_PATH, headers=headers, json={"csr_pem": csr})
                if issued.status_code == 429:
                    log.debug("Model TLS: certificate asked for too recently; next round")
                else:
                    issued.raise_for_status()
                    body = issued.json()
                    _write(self._path(CERT_FILE), str(body["certificate_pem"]).encode("ascii"), mode=0o644)
                    if body.get("trust_pem"):
                        _write(self._path(TRUST_FILE), str(body["trust_pem"]).encode("ascii"), mode=0o644)
                    changed = True
                    log.info(
                        "Model TLS: certificate issued for %s (%s), valid until %s",
                        ", ".join(body.get("names") or []), reason, body.get("not_after"),
                    )
            if self.routes():
                await self._apply(force_reload=changed)
            return changed

    # ── the proxy ───────────────────────────────────────────────────

    async def ensure_route(self, public_port: int, internal_port: int) -> dict[str, Any]:
        """Put the proxy in front of ``127.0.0.1:internal_port`` on ``public_port``."""
        if not self.has_certificate() and self.refresher is not None:
            await self.refresher()
        if not self.has_certificate():
            raise ModelTlsError(
                "This machine has no model-TLS certificate yet; the agent asks the backend for one "
                "every minute (is the backend's machine CA on, and does it know this machine's address?)."
            )
        async with self._lock:
            routes = self.routes()
            if routes.get(public_port) != internal_port:
                routes[public_port] = internal_port
                self._save_routes(routes)
            await self._apply()
        return {"protected": True, "public_port": public_port, "internal_port": internal_port}

    async def remove_route(self, public_port: int) -> None:
        async with self._lock:
            routes = self.routes()
            if routes.pop(public_port, None) is None:
                return
            self._save_routes(routes)
            if routes:
                await self._apply()
            elif await self._runtime.exists(CONTAINER):
                await self._runtime.remove(CONTAINER)
                log.info("Model TLS: no model left behind the proxy; removed it")

    def _ipv6(self) -> bool:
        return Path("/proc/net/if_inet6").is_file()

    async def _apply(self, *, force_reload: bool = False) -> None:
        """Write the configuration and make the container serve it."""
        routes = self.routes()
        conf = render_conf(routes, client_name=self.client_name, ipv6=self._ipv6()).encode()
        conf_path = self._path(CONF_FILE)
        conf_changed = not conf_path.is_file() or conf_path.read_bytes() != conf
        if conf_changed:
            _write(conf_path, conf, mode=0o644)
        image = await self._ensure_image()
        if await self._runtime.exists(CONTAINER):
            info = await self._runtime.inspect(CONTAINER)
            record = (info[0] if isinstance(info, list) and info else info) or {}
            running = bool((record.get("State") or {}).get("Running"))
            if (record.get("Config") or {}).get("Image") != image:
                # Another build than the pinned one (an upgrade moved the pin):
                # made again from the pinned image, which reads everything anew.
                await self._runtime.remove(CONTAINER)
            elif not running:
                await self._runtime.start(CONTAINER)
                log.info("Model TLS: proxy started again")
                self.last_error = None
                return
        if not await self._runtime.exists(CONTAINER):
            await self._runtime.run(
                image=image,
                name=CONTAINER,
                volumes=[f"{self._host_dir}:{_MOUNT}:ro"],
                entrypoint="nginx",
                command=["-c", f"{_MOUNT}/{CONF_FILE}", "-g", "daemon off;"],
                extra_args=["--network", "host"],
                timeout_sec=60,
            )
            log.info("Model TLS: proxy started for ports %s", ", ".join(str(p) for p in sorted(routes)))
            self.last_error = None
            return
        if conf_changed or force_reload:
            code, _out, err = await self._runtime.exec_(
                CONTAINER, ["nginx", "-t", "-c", f"{_MOUNT}/{CONF_FILE}"], raise_on_error=False, timeout_sec=20,
            )
            if code != 0:
                self.last_error = err.strip()[-500:]
                raise ModelTlsError(f"nginx refused the model-TLS configuration: {self.last_error}")
            await self._runtime.exec_(
                CONTAINER, ["nginx", "-c", f"{_MOUNT}/{CONF_FILE}", "-s", "reload"], timeout_sec=20,
            )
            log.info("Model TLS: proxy reloaded (ports %s)", ", ".join(str(p) for p in sorted(routes)))
        self.last_error = None

    async def _verified(self, ref: str, pin: str) -> bool:
        identity = await self._runtime.image_identity(ref)
        layers = [str(x) for x in identity.get("rootfs_layers") or []]
        return bool(identity.get("present")) and bool(layers) and rootfs_digest(layers) == pin

    async def _ensure_image(self) -> str:
        """A local nginx image matching the pin: ``ref`` to run it by."""
        if self._image is not None:
            return self._image
        arch = machine_arch()
        pin = NGINX_ROOTFS.get(arch)
        if pin is None:
            raise ModelTlsError(f"No pinned model-TLS proxy image for {arch}.")
        by_digest = f"nginx@{NGINX_INDEX}"
        candidates = (server_image_tag(arch), by_digest, f"nginx:{NGINX_VERSION}")
        for ref in candidates:
            if await self._verified(ref, pin):
                self._image = ref
                return ref
        errors = []
        if self._image_loader is not None:
            try:
                await self._image_loader(image=server_image_tag(arch), expect_rootfs=pin)
                if await self._verified(server_image_tag(arch), pin):
                    self._image = server_image_tag(arch)
                    return self._image
                errors.append("the server's copy is not the pinned build")
            except Exception as exc:  # noqa: BLE001 - the registry is the next place to look
                errors.append(f"server: {exc}")
        try:
            await self._runtime.pull(by_digest, timeout_sec=600)
            if await self._verified(by_digest, pin):
                self._image = by_digest
                return by_digest
            errors.append("the registry's image is not the pinned build")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"registry: {exc}")
        raise ModelTlsError("The model-TLS proxy image (nginx " + NGINX_VERSION + ") is not available: " + "; ".join(errors))

    def status(self) -> dict[str, Any]:
        held = _read_cert(self._path(CERT_FILE).read_bytes()) if self._path(CERT_FILE).is_file() else None
        return {
            "enabled": self.enabled,
            "certificate_not_after": held.not_after.isoformat() if held else None,
            "names": held.names if held else [],
            "routes": {str(k): v for k, v in self.routes().items()},
            "error": self.last_error,
        }
