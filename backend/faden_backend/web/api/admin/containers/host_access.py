"""What in a container's settings gives it root on the host.

A container that binds a folder or file of the host, shares the host's network
or processes, runs privileged or uses the host's devices, is root on the host
for whoever decides what runs in it. Some binds are root plainly (the Docker
socket, ``/``, a system folder); the others are treated the same, because no
list of the host's dangerous folders is complete (cron's spool, a user's
``.ssh``, the install directory, a moved Docker data root, ...). A named volume
is not the host's: it needs nothing.

The console makes or changes such a container, runs commands in one, or
changes the class of one, only in Root Mode. The check sits here, in front of
the console's routes, and not in the Docker client: the backend makes its own
containers (runtimes, MCP servers) through that client, and they are not the
console's.

Paths are judged as written and as the backend resolves them; a symlink on the
host that leads elsewhere is not seen from here.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable
from typing import Any

from faden_backend.services.docker.client import resolve_binds

#: Host folders a container must not get without Root Mode, nor any folder they are in.
_SYSTEM_FOLDERS = (
    "/etc", "/root", "/proc", "/sys", "/dev", "/boot", "/var/lib/docker", "/run", "/var/run",
    "/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32",
)
#: Files that tell only the time zone: allowed read-only (the zoneinfo folder with all it holds).
_READ_ONLY_HARMLESS = frozenset({"/etc/localtime", "/etc/timezone"})
_ZONEINFO = "/usr/share/zoneinfo"
#: Container engines' sockets, wherever they are bound from.
_ENGINE_SOCKETS = frozenset({
    "docker.sock", "docker.raw.sock", "dockershim.sock", "docker_engine",
    "containerd.sock", "podman.sock", "crio.sock", "cri-dockerd.sock",
})
#: A Docker volume's name, as Docker's own check has it: never a path, ``.``, ``~`` or a drive.
_VOLUME_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
#: The host's namespaces a container may share, by their HostConfig key.
_SHARED_NAMESPACES = {
    "PidMode": "processes", "IpcMode": "IPC namespace (its shared memory)", "UsernsMode": "user namespace",
}


def _named_volume(source: str) -> bool:
    """A Docker volume's name rather than a host path (``data``, not ``./data``, ``~``, ``C:/data``)."""
    return bool(_VOLUME_NAME.fullmatch(source))


def _host_side(entry: str) -> tuple[str | None, bool]:
    """The host side of a ``host:container[:options]`` bind and whether it is read-only.

    None: a container path alone, an anonymous volume. A ``--mount`` style
    entry (``type=bind,source=/x,target=/y``) is not a bind Docker takes, but
    its source is judged all the same.
    """
    if not entry:
        return None, False
    if "=" in entry.split(":", 1)[0]:
        split = (field.partition("=") for field in entry.split(","))
        fields = {key: value if sep else "true" for key, sep, value in split}
        source = fields.get("source") or fields.get("src") or ""
        read_only = (fields.get("readonly") or fields.get("ro") or "false").lower() in {"true", "1"}
        return (source if source and not _named_volume(source) else entry), read_only
    drive = len(entry) > 1 and entry[0].isalpha() and entry[1] == ":"
    parts = entry.split(":")
    if drive:
        host, rest = ":".join(parts[:2]), parts[2:]
    elif len(parts) == 1:
        return (None, False) if entry.startswith("/") else (entry, False)
    else:
        host, rest = parts[0], parts[1:]
    options = rest[1].split(",") if len(rest) > 1 else []
    return host, "ro" in options


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


def _harmless(path: str, *, read_only: bool) -> bool:
    """Whether binding the host's *path* tells a container no more than the time zone."""
    zoneinfo = path == _ZONEINFO or path.startswith(_ZONEINFO + "/")
    return read_only and (path in _READ_ONLY_HARMLESS or zoneinfo)


def _system_reason(path: str) -> str | None:
    """Why a container that binds *path* from the host is plainly root on it, if it is."""
    parts = path.split("/")
    if path == "/":
        return "binds / (the host's root)"
    if _engine_socket(parts[-1]):
        return f"binds {path} (the container engine's socket)"
    if ".docker" in parts:
        return f"binds {path} (Docker's own folder)"
    for folder in _SYSTEM_FOLDERS:
        if path == folder or path.startswith(folder + "/"):
            return f"binds {path} (the host's {folder})"
        if folder.startswith(path + "/"):
            return f"binds {path} (it holds the host's {folder})"
    return None


def _path_reason(written: str, *, read_only: bool) -> str | None:
    """Why binding the host's *written* needs Root Mode; None only for the time zone, read-only."""
    path = _normal(written)
    if _harmless(path, read_only=read_only):
        return None
    return _system_reason(path) or f"binds {written} (a host folder)"


def _bind_reason(bind: str) -> str | None:
    """Why the bind *bind* needs Root Mode; None for a volume, or the time zone read-only."""
    # As written, and as the backend hands it to Docker: a relative path is
    # resolved against the backend's working directory.
    try:
        resolved = next(iter(resolve_binds([bind])), bind)
    except OSError:  # a path Windows cannot resolve, such as a named pipe
        resolved = bind
    found: list[str] = []  # "" for a host folder with no plainer reason
    written: str | None = None
    for entry in dict.fromkeys((bind, resolved)):
        host, read_only = _host_side(entry)
        if host is None or _named_volume(host):
            continue
        path = _normal(host)
        if _harmless(path, read_only=read_only):
            continue
        written = written or host
        # A relative path is a folder of the backend's working directory: what
        # it is shows only once resolved.
        absolute = host[:1] in {"/", "\\"} or host[1:2] == ":"
        found.append((_system_reason(path) if absolute else None) or "")
    if not found:
        return None
    return next((reason for reason in found if reason), f"binds {written} (a host folder)")


def bind_reasons(volumes: Iterable[str]) -> list[str]:
    """Why the binds *volumes* (``host:container[:options]``) need Root Mode: every host path does."""
    reasons: list[str] = []
    for bind in volumes:
        reason = _bind_reason(bind)
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


def _mount_reason(mount: Any) -> str | None:
    """Why a mount (``HostConfig.Mounts``, or the ``Mounts`` of an inspect) needs Root Mode, if it does."""
    if not isinstance(mount, dict):
        return None
    kind = str(mount.get("Type") or "").lower()
    read_only = bool(mount.get("ReadOnly")) or mount.get("RW") is False
    if kind == "volume":
        # A volume the local driver makes as a bind of a host path: that path.
        options = ((mount.get("VolumeOptions") or {}).get("DriverConfig") or {}).get("Options") or {}
        if not ({"bind", "rbind"} & set(str(options.get("o") or "").split(",")) and options.get("device")):
            return None
        return _path_reason(str(options["device"]), read_only=read_only)
    source = str(mount.get("Source") or "")
    if kind in {"bind", "npipe"} and source:
        return _path_reason(source, read_only=read_only)
    return None


def _gpu(device: str) -> bool:
    """Whether the host's *device* is a GPU's (``/dev/dri/renderD128``, ``/dev/kfd``, ``/dev/nvidia0``)."""
    path = _normal(device)
    return path in {"/dev/dri", "/dev/kfd"} or path.startswith(("/dev/dri/", "/dev/nvidia"))


def _strings(host_config: dict[str, Any], key: str) -> list[str]:
    return [str(item) for item in host_config.get(key) or []]


def _namespace_reasons(host_config: dict[str, Any]) -> list[str]:
    """The host's namespaces the container shares, and another container's processes."""
    reasons: list[str] = []
    for key, what in _SHARED_NAMESPACES.items():
        mode = str(host_config.get(key) or "")
        if mode.lower() == "host":
            reasons.append(f"shares the host's {what}")
        elif key == "PidMode" and mode.lower().startswith("container:"):
            # Through /proc, that container's files too: its Docker socket.
            reasons.append(f"shares the processes of {mode}")
    return reasons


def _device_reasons(host_config: dict[str, Any]) -> list[str]:
    """The host's devices the container may open, but its GPUs."""
    reasons: list[str] = []
    paths = [str(d.get("PathOnHost") or "") for d in host_config.get("Devices") or [] if isinstance(d, dict)]
    if devices := [path for path in paths if path and not _gpu(path)]:
        reasons.append(f"uses the host's devices {', '.join(devices)}")
    if rules := _strings(host_config, "DeviceCgroupRules"):
        reasons.append(f"may open the host's devices ({', '.join(rules)})")
    return reasons


def kept_reasons(host_config: dict[str, Any]) -> list[str]:
    """What in a HostConfig beside its binds and network gives a container root on the host.

    An edit takes the binds and the network from the form; the rest of the
    HostConfig goes to the new container as it was (``services/docker/recreate.py``).
    """
    found: list[str | None] = ["runs privileged" if host_config.get("Privileged") else None]
    found += _namespace_reasons(host_config)
    if caps := _strings(host_config, "CapAdd"):
        found.append(f"adds the capabilities {', '.join(caps)}")
    security = {option.replace(":", "=").lower() for option in _strings(host_config, "SecurityOpt")}
    if "systempaths=unconfined" in security:
        found.append("has the host's /proc and /sys paths unmasked")
    found += _device_reasons(host_config)
    if others := _strings(host_config, "VolumesFrom"):
        found.append(f"takes the volumes of {', '.join(others)}")
    found += [_mount_reason(mount) for mount in host_config.get("Mounts") or []]
    return list(dict.fromkeys(reason for reason in found if reason))


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


def container_reasons(raw: dict[str, Any]) -> list[str]:
    """Everything that gives the container *raw* (Docker's inspect of it) root on the host."""
    host_config = raw.get("HostConfig") or {}
    reasons = host_access(host_config.get("Binds") or [], host_config.get("NetworkMode"), host_config)
    for mount in raw.get("Mounts") or []:  # what it runs with, Binds and Mounts together
        reason = _mount_reason(mount)
        if reason and reason not in reasons:
            reasons.append(reason)
    return reasons


def _bind_volume(entry: str) -> str | None:
    """The named volume a bind (``name:/path``, ``type=volume,source=name,...``) mounts, if it mounts one."""
    if "=" in entry.split(":", 1)[0]:
        fields = dict(field.partition("=")[::2] for field in entry.split(","))
        source = fields.get("source") or fields.get("src") or ""
        return source if fields.get("type", "volume") == "volume" and _named_volume(source) else None
    host, _ = _host_side(entry)
    return host if host and _named_volume(host) else None


def named_volumes(
    volumes: Iterable[str], host_config: dict[str, Any] | None = None, mounts: Iterable[Any] = (),
) -> list[str]:
    """The named volumes in binds, a HostConfig's ``Mounts`` and an inspect's ``Mounts``, each once."""
    names = [name for bind in volumes if (name := _bind_volume(bind))]
    for mount in [*((host_config or {}).get("Mounts") or []), *mounts]:
        volume = isinstance(mount, dict) and str(mount.get("Type") or "").lower() == "volume"
        # A HostConfig names it Source, an inspect's Mounts Name.
        if volume and (name := str(mount.get("Name") or mount.get("Source") or "")):
            names.append(name)
    return list(dict.fromkeys(names))


def refusal(reasons: list[str], *, doing: str) -> str:
    """What the console says when a container like this needs Root Mode."""
    hint = (
        " A named volume of its own (a name instead of a host path) needs no Root Mode."
        if any(reason.startswith("binds ") for reason in reasons) and doing in {"create", "change"} else ""
    )
    return (
        f"Root on the host: this container {'; '.join(reasons)}. "
        f"Activate Root Mode to {doing} a container like this.{hint}"
    )
