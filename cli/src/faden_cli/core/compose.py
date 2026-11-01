"""Docker Compose subprocess wrapper.

Wraps ``docker compose`` (v2) as a thin Python layer.  Every function
shells out rather than using a Docker SDK so the CLI has zero runtime
dependency on Docker libraries and works on any system that has the
``docker`` binary.

Environment and profile arguments are built from the
:class:`~faden_cli.core.settings.FadenConfig` dataclass so callers
never need to assemble raw CLI flags.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from faden_cli.core.console import console, error, info, warning

if TYPE_CHECKING:
    from faden_cli.core.settings import FadenConfig


# ── GPU detection ─────────────────────────────────────────────────

_gpu_cache: bool | None = None


def has_nvidia_gpu() -> bool:
    """Return True if an NVIDIA GPU is actually usable by Docker.

    Checks two things:
      1. The Docker daemon has the ``nvidia`` runtime registered.
      2. ``nvidia-smi`` succeeds on the host, proving real GPU
         adapters are present (the runtime alone may be installed
         in WSL/CI without any physical GPU).
    """
    global _gpu_cache  # noqa: PLW0603
    if _gpu_cache is not None:
        return _gpu_cache

    docker = shutil.which("docker")
    if not docker:
        _gpu_cache = False
        return False

    # 1. Runtime check
    try:
        proc = subprocess.run(
            [docker, "info", "--format", "{{.Runtimes}}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
        )
        if "nvidia" not in (proc.stdout or "").lower():
            _gpu_cache = False
            return False
    except Exception:  # noqa: BLE001
        _gpu_cache = False
        return False

    # 2. Actual GPU adapter check
    smi = shutil.which("nvidia-smi")
    if not smi:
        _gpu_cache = False
        return False
    try:
        proc = subprocess.run(
            [smi, "-L"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
        )
        _gpu_cache = proc.returncode == 0 and "GPU" in (proc.stdout or "")
    except Exception:  # noqa: BLE001
        _gpu_cache = False
    return _gpu_cache


# ── Types ─────────────────────────────────────────────────────────


@dataclass
class ComposeService:
    """Parsed row from ``docker compose ps --format json``."""

    name: str = ""
    service: str = ""
    status: str = ""  # running, exited, created, …
    health: str = ""  # healthy, unhealthy, starting, ""
    ports: str = ""
    state: str = ""
    exit_code: int = 0


@dataclass
class ComposeContext:
    """Everything needed to run a compose command."""

    compose_files: list[Path] = field(default_factory=list)
    env_file: Path | None = None
    project_dir: Path | None = None
    profiles: list[str] = field(default_factory=list)

    def write_secret_files(self) -> list[str]:
        """Write the content keys' files from .env, for a compose file that mounts them; the required keys missing.

        Before every compose command: a file compose mounts that is not there becomes an empty folder Docker
        makes in its place (core/secret_files.py).
        """
        from faden_cli.core import secret_files  # noqa: PLC0415

        if self.project_dir is None or self.env_file is None:
            return []
        if not secret_files.uses_secret_files(self.compose_files):
            return []
        try:
            return secret_files.write(Path(str(self.project_dir)), Path(str(self.env_file)))
        except (secret_files.SecretFileError, OSError) as exc:
            error(f"The keys' files could not be written: {exc}")
            sys.exit(1)

    def extension_overlays(self) -> list[Path]:
        """The add-ons' compose files beside the main one that exist (``registry.extend_compose_files``)."""
        from faden_cli.core.registry import COMPOSE_OVERLAYS  # noqa: PLC0415

        if not self.compose_files:
            return []
        folder = Path(str(self.compose_files[0])).parent
        return [folder / name for name in COMPOSE_OVERLAYS if (folder / name).is_file()]

    def base_cmd(self) -> list[str]:
        """Build the ``docker compose`` prefix with all file/profile flags."""
        docker = shutil.which("docker")
        if not docker:
            error("docker not found on PATH")
            sys.exit(1)
        self.write_secret_files()

        cmd: list[str] = [docker, "compose"]
        from faden_cli.core.pii_runtime import gpu_overlay
        files = list(self.compose_files)
        overlay = gpu_overlay(self.env_file, self.project_dir)
        if overlay is not None and overlay not in files:
            files.append(overlay)
        files.extend(p for p in self.extension_overlays() if p not in files)
        for f in files:
            cmd.extend(["-f", str(f)])
        if self.env_file and Path(str(self.env_file)).exists():
            cmd.extend(["--env-file", str(self.env_file)])
        if self.project_dir:
            cmd.extend(["--project-directory", str(self.project_dir)])
        for p in self.profiles:
            cmd.extend(["--profile", p])
        return cmd


# ── Constants ─────────────────────────────────────────────────────

# Base images used in the platform base Dockerfile.  Pre-pulling
# puts them in the local store so subsequent builds hit cache.
_BASE_IMAGES = [
    "python:3.13-slim-bookworm",
    "ghcr.io/astral-sh/uv:0.9.12",
]


# ── Public API ────────────────────────────────────────────────────


def pull_base_images() -> None:
    """Pre-pull shared base images so BuildKit finds them locally."""
    docker = shutil.which("docker")
    if not docker:
        return
    for image in _BASE_IMAGES:
        subprocess.run(  # noqa: S603
            [docker, "pull", image],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def build_base_image(ctx: ComposeContext, *, no_cache: bool = False) -> int:
    """Build the shared platform base image via compose.

    Uses the ``faden-base`` service (gated behind the ``_build``
    profile) so the image lands in the same BuildKit context that
    subsequent service builds use.
    """
    base_ctx = ComposeContext(
        compose_files=ctx.compose_files,
        env_file=ctx.env_file,
        project_dir=ctx.project_dir,
        profiles=["_build"],
    )
    return build(base_ctx, services=["faden-base"], no_cache=no_cache)


def _run(
    cmd: list[str],
    *,
    capture: bool = False,
    check: bool = True,
    stream: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Execute a command, optionally streaming output to the console."""
    if stream:
        # Stream stdout/stderr to terminal in real time
        result = subprocess.run(cmd, check=False)  # noqa: S603
        if check and result.returncode != 0:
            error(f"Command failed with exit code {result.returncode}")
        return subprocess.CompletedProcess(cmd, result.returncode, "", "")

    return subprocess.run(  # noqa: S603
        cmd,
        capture_output=capture,
        text=True,
        # Decode explicitly as UTF-8 and never raise.  `text=True` alone
        # decodes with the locale codec, which on a Windows console is
        # cp1252: docker output containing any byte outside it (0x8d in a
        # container name or a progress glyph) killed the reader thread, left
        # `stdout` as None, and surfaced as `'NoneType' has no attribute
        # 'strip'` in the caller rather than as a decoding problem.
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def up(
    ctx: ComposeContext,
    *,
    services: list[str] | None = None,
    detach: bool = True,
    build: bool = False,
    pull: str = "",
    wait: bool = False,
    timeout: int = 0,
    force_recreate: bool = False,
    no_deps: bool = False,
) -> int:
    """Run ``docker compose up``.

    *pull* accepts ``"always"``, ``"missing"``, or ``"never"``.
    An empty string (default) leaves Docker Compose to decide.
    *wait* blocks until services are healthy (implies ``-d``).
    *timeout* sets the max wait time in seconds (requires *wait*).
    *force_recreate* recreates containers even if config hasn't changed.
    *no_deps* starts only *services*, not what they depend on.

    A key the services cannot start without, missing from .env, stops it before compose runs, as compose
    itself did while the keys were environment (``${NAME:?...}``).
    """
    if missing := ctx.write_secret_files():
        error(
            f"Not set in .env: {', '.join(missing)}. The services cannot start without them; `faden upgrade` "
            "writes the ones an install lacks (Help, Installing, Secrets in .env)."
        )
        return 1
    if "pii" in ctx.profiles and not build and (services is None or "faden-pii" in services):
        from faden_cli.core.pii_runtime import warm_candidate

        try:
            warm_candidate(ctx)
        except ValueError as exc:
            error(str(exc))
            return 1
    cmd = ctx.base_cmd() + ["up"]
    if detach:
        cmd.append("-d")
    if no_deps:
        cmd.append("--no-deps")
    if build:
        cmd.append("--build")
    if pull:
        cmd.append(f"--pull={pull}")
    if force_recreate:
        cmd.append("--force-recreate")
    if wait:
        cmd.append("--wait")
    if timeout > 0:
        cmd.append(f"--wait-timeout={timeout}")
    if services:
        cmd.extend(services)
    result = _run(cmd, stream=True)
    return result.returncode


def service_names(ctx: ComposeContext) -> list[str]:
    """The services this compose setup runs (its active profiles included); empty when compose cannot say."""
    result = _run(ctx.base_cmd() + ["config", "--services"], capture=True)
    if result.returncode != 0:
        return []
    return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]


def stop_inactive_modules(ctx: ComposeContext) -> list[tuple[str, list[str]]]:
    """Stop and remove the containers of modules that are switched off.

    ``compose up`` only starts the services of active profiles; it never stops
    those of a profile that was switched off, so ``module disable`` left them
    running. Only services whose profiles are all module profiles and none of
    them active are touched (core services have no profile). Volumes stay.
    Returns ``(module, container names)`` for each module it cleaned up.
    """
    from dataclasses import replace  # noqa: PLC0415

    from faden_cli.core.registry import MODULES  # noqa: PLC0415

    # "*" makes compose see every profile, so the services of inactive ones are listed too.
    everything = replace(ctx, profiles=["*"])
    config = _run(everything.base_cmd() + ["config", "--format", "json"], capture=True)
    if config.returncode != 0:
        return []
    try:
        spec = json.loads(config.stdout or "{}")
    except ValueError:
        return []
    by_profile = {m.profile: name for name, m in MODULES.items()}
    active = set(ctx.profiles)
    owner: dict[str, str] = {}
    for service, body in (spec.get("services") or {}).items():
        profiles = set(body.get("profiles") or []) if isinstance(body, dict) else set()
        if profiles and profiles <= set(by_profile) and not profiles & active:
            owner[service] = by_profile[sorted(profiles)[0]]
    if not owner:
        return []
    present = {row.service: row.name for row in ps(everything) if row.service in owner}
    if not present:
        return []
    names = sorted(present)
    failed = stop(everything, services=names)
    failed = failed or _run(everything.base_cmd() + ["rm", "-f", *names], stream=True).returncode
    if failed:
        # Not fatal for the command that called it: the active services run. Said, so it is not missed.
        containers = ", ".join(sorted(present.values()))
        warning(f"The containers of switched-off modules could not be stopped and removed: {containers}. "
                f"Run `faden up` again, or remove them with `docker rm -f {' '.join(sorted(present.values()))}`.")
        return []
    cleaned: dict[str, list[str]] = {}
    for service, container in sorted(present.items()):
        cleaned.setdefault(owner[service], []).append(container or service)
    return sorted(cleaned.items())


#: Services that start only when their profile is asked for, which ``faden`` never does, by the profile. An
#: earlier release started them with a module: up to 0.4.x ``faden-pii-worker`` was in profile ``pii``, since
#: 0.5.0 it is in ``pii-tasks``. Compose leaves a container of a service whose profile is off alone, so after
#: an upgrade from 0.4.x the old worker kept running on its 0.4.x image, through every restart and
#: ``faden down``.
ON_REQUEST_SERVICES: dict[str, str] = {"faden-pii-worker": "pii-tasks"}


def stop_unrequested_services(ctx: ComposeContext) -> list[str]:
    """Stop and remove the containers of :data:`ON_REQUEST_SERVICES` whose profile is off; their names.

    Volumes stay. Like :func:`stop_inactive_modules`, a failure is said and not fatal.
    """
    from dataclasses import replace  # noqa: PLC0415

    wanted = [name for name, profile in ON_REQUEST_SERVICES.items() if profile not in set(ctx.profiles)]
    if not wanted:
        return []
    everything = replace(ctx, profiles=["*"])
    present = {row.service: row.name for row in ps(everything) if row.service in wanted}
    if not present:
        return []
    names = sorted(present)
    failed = stop(everything, services=names)
    failed = failed or _run(everything.base_cmd() + ["rm", "-f", *names], stream=True).returncode
    if failed:
        containers = " ".join(sorted(present.values()))
        warning(f"Containers this release no longer runs could not be stopped and removed: {containers}. "
                f"Run `faden up` again, or remove them with `docker rm -f {containers}`.")
        return []
    return sorted(name or service for service, name in present.items())


def report_inactive_modules(ctx: ComposeContext) -> None:
    """Run :func:`stop_inactive_modules` and print one line per module it stopped.

    Then :func:`stop_unrequested_services`, with a line of its own: those containers are an earlier release's.
    """
    for module, containers in stop_inactive_modules(ctx):
        label = module.upper() if len(module) <= 3 else module.capitalize()
        info(f"{label} is switched off: stopped and removed {', '.join(containers)} (their data is kept).")
    for container in stop_unrequested_services(ctx):
        info(f"Stopped and removed {container}: an earlier release started it, this one does not.")


def down(
    ctx: ComposeContext,
    *,
    volumes: bool = False,
    remove_orphans: bool = False,
    rmi: str | None = None,
) -> int:
    """Run ``docker compose down``.

    Args:
        rmi: Remove images. ``"all"`` removes all images, ``"local"``
             removes only images without a custom tag.
    """
    cmd = ctx.base_cmd() + ["down"]
    if volumes:
        cmd.append("-v")
    if remove_orphans:
        cmd.append("--remove-orphans")
    if rmi:
        cmd.extend(["--rmi", rmi])
    result = _run(cmd, stream=True)
    return result.returncode


def stop(ctx: ComposeContext, *, services: list[str] | None = None) -> int:
    """Run ``docker compose stop`` for the given services (all of them when none is named).

    The containers stay, with their volumes and networks; ``down`` would remove them.
    """
    cmd = ctx.base_cmd() + ["stop"]
    if services:
        cmd.extend(services)
    result = _run(cmd, stream=True)
    return result.returncode


def restart(ctx: ComposeContext, *, services: list[str] | None = None) -> int:
    """Run ``docker compose restart`` for the given services."""
    cmd = ctx.base_cmd() + ["restart"]
    if services:
        cmd.extend(services)
    result = _run(cmd, stream=True)
    return result.returncode


def pull(ctx: ComposeContext, *, services: list[str] | None = None) -> int:
    """Run ``docker compose pull``."""
    cmd = ctx.base_cmd() + ["pull"]
    if services:
        cmd.extend(services)
    result = _run(cmd, stream=True)
    return result.returncode


def build(
    ctx: ComposeContext,
    *,
    services: list[str] | None = None,
    no_cache: bool = False,
    pull: bool = False,
) -> int:
    """Run ``docker compose build``."""
    cmd = ctx.base_cmd() + ["build"]
    if no_cache:
        cmd.append("--no-cache")
    if pull:
        cmd.append("--pull")
    if services:
        cmd.extend(services)
    result = _run(cmd, stream=True)
    return result.returncode


def logs(
    ctx: ComposeContext,
    *,
    services: list[str] | None = None,
    follow: bool = True,
    tail: int | None = 100,
    timestamps: bool = False,
) -> int:
    """Run ``docker compose logs``."""
    cmd = ctx.base_cmd() + ["logs"]
    if follow:
        cmd.append("-f")
    if tail is not None:
        cmd.extend(["--tail", str(tail)])
    if timestamps:
        cmd.append("--timestamps")
    if services:
        cmd.extend(services)
    result = _run(cmd, stream=True)
    return result.returncode


def ps(ctx: ComposeContext) -> list[ComposeService]:
    """Run ``docker compose ps`` and parse the output."""
    import json as _json

    cmd = ctx.base_cmd() + ["ps", "--format", "json", "-a"]
    result = _run(cmd, capture=True)
    if result.returncode != 0:
        return []

    services: list[ComposeService] = []
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = _json.loads(line)
            svc = ComposeService(
                name=obj.get("Name", ""),
                service=obj.get("Service", ""),
                status=obj.get("State", obj.get("Status", "")),
                health=obj.get("Health", ""),
                ports=obj.get("Publishers", obj.get("Ports", "")),
                state=obj.get("State", ""),
                exit_code=obj.get("ExitCode", 0),
            )
            # Format ports if it's a list of dicts
            if isinstance(svc.ports, list):
                port_strs = []
                for p in svc.ports:
                    if isinstance(p, dict) and p.get("PublishedPort"):
                        port_strs.append(f"{p.get('URL', '0.0.0.0')}:{p['PublishedPort']}→{p['TargetPort']}")
                svc.ports = ", ".join(port_strs) if port_strs else ""
            services.append(svc)
        except (ValueError, KeyError):
            continue
    return services


def foreign_containers(ctx: ComposeContext) -> list[tuple[str, str]]:
    """Containers holding a name this compose file gives, made by something else.

    Compose cannot replace a container it did not create: ``up`` stops with
    "The container name ... is already in use". An upgrade met one -- a
    Prometheus recreated by hand with ``docker run`` -- after building images
    for a quarter of an hour. Returns ``(name, made by)`` pairs, where *made
    by* is the other compose project, or ``""`` for a plain ``docker run``.
    """
    config = _run(ctx.base_cmd() + ["config", "--format", "json"], capture=True)
    if config.returncode != 0:
        return []
    try:
        spec = json.loads(config.stdout or "{}")
    except ValueError:
        return []
    project = spec.get("name", "")
    wanted = {
        s.get("container_name")
        for s in (spec.get("services") or {}).values()
        if isinstance(s, dict) and s.get("container_name")
    }
    docker = shutil.which("docker") or "docker"
    listed = _run(
        [docker, "ps", "-a", "--format", '{{.Names}}\t{{.Label "com.docker.compose.project"}}'],
        capture=True,
    )
    found = []
    for line in (listed.stdout or "").splitlines():
        name, _, owner = line.partition("\t")
        if name in wanted and owner != project:
            found.append((name, owner))
    return sorted(found)


def exec_cmd(
    ctx: ComposeContext,
    service: str,
    command: list[str],
    *,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run ``docker compose exec`` on a running service."""
    cmd = ctx.base_cmd() + ["exec", service, *command]
    return _run(cmd, capture=capture)


def run_cmd(
    ctx: ComposeContext,
    service: str,
    command: list[str],
    *,
    capture: bool = False,
    remove: bool = True,
    no_deps: bool = False,
    no_tty: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run ``docker compose run`` (one-off container).

    *no_deps* does not start what *service* depends on; *no_tty* is for output read by a program.
    """
    cmd = ctx.base_cmd() + ["run"]
    if remove:
        cmd.append("--rm")
    if no_deps:
        cmd.append("--no-deps")
    if no_tty:
        cmd.append("-T")
    cmd.extend([service, *command])
    return _run(cmd, capture=capture)


def _resolve_compose_path(cfg: "FadenConfig") -> Path:
    """Resolve the compose file, falling back to shared/."""
    primary = cfg.compose_path
    if primary.exists():
        return primary
    # Check inside shared relative to install path
    for name in ("docker-compose.yaml", "docker-compose.yml"):
        candidate = cfg.install_path / "shared" / name
        if candidate.exists():
            return candidate
    return primary


def build_context_from_config(cfg: "FadenConfig", *, gpu: bool | None = None) -> ComposeContext:
    """Build a ComposeContext from the current config.

    *gpu* controls whether the GPU overlay is included:
      - ``None`` (default): auto-detect via :func:`has_nvidia_gpu`
      - ``True``: force-include the GPU overlay
      - ``False``: never include the GPU overlay
    """
    compose_file = _resolve_compose_path(cfg)
    files = [compose_file]

    use_gpu = has_nvidia_gpu() if gpu is None else gpu
    if use_gpu:
        gpu_overlay = compose_file.parent / "docker-compose.gpu.yaml"
        if gpu_overlay.exists():
            files.append(gpu_overlay)

    return ComposeContext(
        compose_files=files,
        env_file=cfg.env_path if cfg.env_path.exists() else None,
        project_dir=compose_file.parent,
        profiles=list(cfg.profiles),
    )


def build_context_from_paths(
    compose_file: Path,
    *,
    env_file: Path | None = None,
    project_dir: Path | None = None,
    profiles: list[str] | None = None,
    dev_overlay: Path | None = None,
) -> ComposeContext:
    """Build a ComposeContext from explicit paths."""
    files = [compose_file]
    if dev_overlay and dev_overlay.exists():
        files.append(dev_overlay)
    return ComposeContext(
        compose_files=files,
        env_file=env_file,
        project_dir=project_dir or compose_file.parent,
        profiles=profiles or [],
    )
