"""What in a container's settings gives it root on the host.

A container that binds the Docker socket, the host's root or one of its system
folders, shares the host's network, or runs privileged, is root on the host for
whoever decides what runs in it. The console makes or changes such a container
only in Root Mode. The check sits here, in front of the console's create and
edit, and not in the Docker client: the backend makes its own containers
(runtimes, MCP servers) through that client, and they are not the console's.

Paths are judged as written and as the backend resolves them; a symlink on the
host that leads elsewhere is not seen from here.
"""

from __future__ import annotations

import posixpath
from collections.abc import Iterable
from typing import Any

from faden_backend.services.docker.client import resolve_binds

#: Host folders a container must not get without Root Mode, nor any folder they are in.
_SYSTEM_FOLDERS = (
    "/etc", "/root", "/proc", "/sys", "/dev", "/boot", "/var/lib/docker", "/run", "/var/run",
    "/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32",
)
#: Files under them that tell only the time zone: allowed read-only.
_READ_ONLY_HARMLESS = frozenset({"/etc/localtime", "/etc/timezone", "/usr/share/zoneinfo"})
#: Container engines' sockets, wherever they are bound from.
_ENGINE_SOCKETS = frozenset({
    "docker.sock", "docker.raw.sock", "dockershim.sock", "docker_engine",
    "containerd.sock", "podman.sock", "crio.sock", "cri-dockerd.sock",
})


def _named_volume(source: str) -> bool:
    """A Docker volume's name rather than a host path (as :func:`resolve_binds` tells them apart)."""
    return bool(source) and not any(c in source for c in "/\\") and source not in {".", "..", "~"}


def _split(bind: str) -> tuple[str, list[str]]:
    """The host side of a ``host:container[:options]`` bind and its options."""
    drive = len(bind) > 2 and bind[0].isalpha() and bind[1] == ":" and bind[2] in "/\\"
    parts = bind.split(":")
    host, rest = (f"{parts[0]}:{parts[1]}", parts[2:]) if drive else (parts[0], parts[1:])
    options = rest[1].split(",") if len(rest) > 1 else []
    return host, options


def _normal(path: str) -> str:
    r"""*path* as an absolute POSIX path: ``//var/run/x`` and ``C:\x`` too."""
    path = path.replace("\\", "/")
    if len(path) >= 2 and path[0].isalpha() and path[1] == ":":
        path = path[2:]  # Docker Desktop: a folder of that Windows drive
    return posixpath.normpath("/" + path.lstrip("/"))


def _engine_socket(name: str) -> bool:
    """Whether a file named *name* is a container engine's socket."""
    return name in _ENGINE_SOCKETS or (
        name.endswith(".sock") and any(engine in name for engine in ("docker", "containerd", "podman"))
    )


def _path_reason(path: str, *, read_only: bool) -> str | None:
    """Why a container that binds *path* from the host is root on it, if it is."""
    reason: str | None = None
    parts = path.split("/")
    if path == "/":
        reason = "binds / (the host's root)"
    elif read_only and path in _READ_ONLY_HARMLESS:
        reason = None
    elif _engine_socket(parts[-1]):
        reason = f"binds {path} (the container engine's socket)"
    elif ".docker" in parts:
        reason = f"binds {path} (Docker's own folder)"
    else:
        for folder in _SYSTEM_FOLDERS:
            if path == folder or path.startswith(folder + "/"):
                reason = f"binds {path} (the host's {folder})"
            elif folder.startswith(path + "/"):
                reason = f"binds {path} (it holds the host's {folder})"
            if reason:
                break
    return reason


def bind_reasons(volumes: Iterable[str]) -> list[str]:
    """Why the host binds *volumes* (``host:container[:options]``) give a container root on the host."""
    reasons: list[str] = []
    for bind in volumes:
        host, options = _split(bind)
        if not host or _named_volume(host):
            continue
        read_only = "ro" in options
        # As written, and as the backend hands it to Docker: a relative path
        # is resolved against the backend's working directory.
        resolved_host, _ = _split(next(iter(resolve_binds([bind])), bind))
        found = (_path_reason(p, read_only=read_only) for p in (_normal(host), _normal(resolved_host)))
        reason = next((r for r in found if r), None)
        if reason and reason not in reasons:
            reasons.append(reason)
    return reasons


def network_reason(network: str | None) -> str | None:
    """Why the network mode *network* gives a container root on the host, if it does."""
    mode = (network or "").strip()
    if mode.lower() == "host":
        return "uses the host's network"
    if mode.lower().startswith("container:"):
        return f"joins the network of {mode}"
    return None


def kept_reasons(host_config: dict[str, Any]) -> list[str]:
    """What a replaced container keeps from the old one's HostConfig that is root on the host.

    The form edits binds and the network; the rest of the HostConfig goes to
    the new container as it was (``services/docker/recreate.py``).
    """
    reasons: list[str] = []
    if host_config.get("Privileged"):
        reasons.append("runs privileged")
    if str(host_config.get("PidMode") or "").lower() == "host":
        reasons.append("shares the host's processes")
    if str(host_config.get("UsernsMode") or "").lower() == "host":
        reasons.append("uses the host's user namespace")
    if caps := [str(c) for c in host_config.get("CapAdd") or []]:
        reasons.append(f"adds the capabilities {', '.join(caps)}")
    for mount in host_config.get("Mounts") or []:
        bind = isinstance(mount, dict) and str(mount.get("Type") or "").lower() == "bind"
        if not bind or not mount.get("Source"):
            continue
        reason = _path_reason(_normal(str(mount["Source"])), read_only=bool(mount.get("ReadOnly")))
        if reason and reason not in reasons:
            reasons.append(reason)
    return reasons


def host_access(
    volumes: Iterable[str], network: str | None, host_config: dict[str, Any] | None = None,
) -> list[str]:
    """Everything in these settings that gives a container root on the host; empty when nothing does."""
    reasons = bind_reasons(volumes)
    if reason := network_reason(network):
        reasons.append(reason)
    for reason in kept_reasons(host_config or {}):
        if reason not in reasons:
            reasons.append(reason)
    return reasons


def refusal(reasons: list[str], *, doing: str) -> str:
    """What the console says when a container like this needs Root Mode."""
    return (
        f"Root on the host: this container {'; '.join(reasons)}. "
        f"Activate Root Mode to {doing} a container like this."
    )
