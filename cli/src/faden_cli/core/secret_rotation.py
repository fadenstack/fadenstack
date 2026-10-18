"""Rotating the keys that seal an install's stored data: ``faden secrets rotate``.

An install keeps secrets in its databases (the Hugging Face token, provider API keys, the TLS and machine CA
keys, MCP servers' headers and environment, the chats), each sealed with a key from ``.env``. The key may be
a development default published with the source code, or it may have leaked (a copied ``.env``, an
administrator leaving). Rotating replaces it without losing a single value:

1. a backup (the old ``.env`` is in it);
2. ``.env`` gets the new key, and the old one as ``..._PREVIOUS``; the services learn both and open what
   either sealed (``MultiFernet``);
3. the services restart, behind the maintenance page;
4. each service's job re-seals its values with the new key in one transaction per database, and checks that
   each opens with the new key alone before it commits (``faden_backend.tools.rotate_keys`` and the
   gateway's and MCP's equivalents, run as one-off containers);
5. only when every job has succeeded does ``.env`` lose the previous key (and ``FADEN_ALLOW_PUBLISHED_KEYS``,
   when no published key is left), and the services restart once more.

``.env`` is the record of how far it got: a previous key still in it means the rotation is unfinished, and
running the command again picks up from there. A failure at any step leaves every service running with both
keys, and every stored value openable.

The machines' credential hashes are keyed with ``FADEN_BACKEND_CREDENTIAL_PEPPER``, never rotated here: the
machines stay connected.

The provenance engine's keys seal nothing, so nothing is re-sealed and no previous key is kept: its signer
gets a new key and certificate from the install's authority (the old certificate travels inside every
manifest it signed), and its hash keys a new version after the others, which all stay
(core/provenance_keys.py). The engine restarts with them.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from faden_cli.core import provenance_keys
from faden_cli.core.env_gen import (
    ALLOW_PUBLISHED_KEYS,
    CREDENTIAL_PEPPER,
    PUBLISHED_DATA_KEYS,
    SETTINGS_MASTER_KEY,
    _random_secret,
    read_env_file,
    rewrite_env,
)
from faden_cli.core.tls import TlsError


class RotationError(RuntimeError):
    """The rotation stopped; the message says what happened and what to do (never a key)."""


@dataclass(frozen=True)
class Job:
    """One re-sealing job: a module of a service's image, run as a one-off container."""

    service: str
    module: str
    args: tuple[str, ...] = ()
    #: Where the job's results are shown.
    label: str = ""

    def command(self, *, skip_unreadable: bool) -> list[str]:
        return ["python", "-m", self.module, *self.args, *(["--skip-unreadable"] if skip_unreadable else [])]


@dataclass(frozen=True)
class Scope:
    """One key that seals stored data: what it is, who uses it, and how its values are re-sealed."""

    name: str
    #: The ``.env`` variable holding the key, and the one holding the key it replaced while a rotation is
    #: unfinished (empty for keys that seal nothing, which keep no previous key).
    key: str
    previous: str
    #: What the key seals, in the console's words.
    seals: str
    #: Containers that read the key (and the previous one); they restart so they learn both.
    services: tuple[str, ...]
    jobs: tuple[Job, ...]
    #: Values the source code publishes for this key.
    published: frozenset[str]
    #: A compose profile that must be on for the key to matter.
    profile: str | None = None
    #: For keys that seal nothing: the ``.env`` values a rotation writes, from the install folder and its
    #: ``.env``, instead of a new key beside the previous one.
    renew: Callable[[Path, dict[str, str]], dict[str, str]] | None = None


#: The keys, in the order they are rotated.
SCOPES: tuple[Scope, ...] = (
    Scope(
        name="settings",
        key=SETTINGS_MASTER_KEY,
        previous="FADEN_BACKEND_SETTINGS_MASTER_KEY_PREVIOUS",
        seals=(
            "the console's stored secrets (Hugging Face token, TLS keys, RAG secrets, the sign-in secret, "
            "cluster tokens), provider API keys, the machine CA keys"
        ),
        services=("faden-backend", "faden-backend-worker", "faden-gateway"),
        jobs=(
            Job("faden-backend", "faden_backend.tools.rotate_keys", label="backend database"),
            Job("faden-gateway", "faden_gateway.tools.rotate_keys", ("--scope", "settings"), label="gateway database (provider keys)"),
        ),
        published=frozenset({PUBLISHED_DATA_KEYS[SETTINGS_MASTER_KEY]}),
    ),
    Scope(
        name="mcp",
        key="FADEN_MCP_ENCRYPTION_KEY",
        previous="FADEN_MCP_ENCRYPTION_KEY_PREVIOUS",
        seals="the headers and environment of MCP servers (bearer tokens, API keys)",
        services=("faden-mcp",),
        jobs=(Job("faden-mcp", "faden_mcp.tools.rotate_keys", label="MCP database"),),
        published=frozenset({PUBLISHED_DATA_KEYS["FADEN_MCP_ENCRYPTION_KEY"]}),
        profile="mcp",
    ),
    Scope(
        name="chat",
        key="FADEN_GATEWAY_ENCRYPTION_KEY",
        previous="FADEN_GATEWAY_ENCRYPTION_KEY_PREVIOUS",
        seals="chat messages, titles, memory and attachment notes, and the gateway's outbound TLS material",
        services=("faden-gateway",),
        jobs=(Job("faden-gateway", "faden_gateway.tools.rotate_keys", ("--scope", "chat"), label="gateway database (chats)"),),
        published=frozenset({"dev-encryption-key-change-me"}),
    ),
    Scope(
        name="provenance",
        key=provenance_keys.SIGNING_KEY,
        previous="",
        seals=(
            "the C2PA signatures on generated pictures (a new key and certificate), with "
            f"{provenance_keys.HASH_KEYS} for the provenance records' hashes and "
            f"{provenance_keys.WATERMARK_KEYS} for the text watermark "
            "(a new version of each; every earlier one is kept, and a model takes the new watermark key "
            "when it next starts)"
        ),
        services=("faden-provenance", "faden-backend", "faden-backend-worker"),
        jobs=(),
        published=frozenset(),
        profile="provenance",
        renew=provenance_keys.rotated,
    ),
)

SCOPE_NAMES = tuple(scope.name for scope in SCOPES)

#: Keys the Langfuse service seals its own data with. Published defaults still show in the status; the
#: rotation does not cover them (Langfuse re-encrypts nothing when its key changes).
OTHER_PUBLISHED = {
    "LANGFUSE_SALT": PUBLISHED_DATA_KEYS["LANGFUSE_SALT"],
    "LANGFUSE_ENCRYPTION_KEY": PUBLISHED_DATA_KEYS["LANGFUSE_ENCRYPTION_KEY"],
}

#: Services that must be healthy before a rotation starts, whatever it rotates.
ALWAYS_HEALTHY = ("postgres", "faden-backend", "faden-gateway")


# ── What .env says ────────────────────────────────────────────────


@dataclass(frozen=True)
class KeyState:
    """One key's situation, for ``faden secrets status`` and the plan."""

    scope: Scope
    present: bool
    published: bool
    unfinished: bool
    applicable: bool


def key_states(env: dict[str, str], profiles: list[str] | tuple[str, ...]) -> list[KeyState]:
    states: list[KeyState] = []
    for scope in SCOPES:
        value = env.get(scope.key, "")
        applicable = (scope.profile is None or scope.profile in profiles) and bool(value)
        states.append(
            KeyState(
                scope=scope,
                present=bool(value),
                published=value in scope.published,
                unfinished=bool(scope.previous and env.get(scope.previous)),
                applicable=applicable,
            )
        )
    return states


def published_remaining(env: dict[str, str]) -> bool:
    """Whether a key the services refuse without ``FADEN_ALLOW_PUBLISHED_KEYS`` is still a published one."""
    return any(env.get(scope.key, "") in scope.published for scope in SCOPES if scope.name in ("settings", "mcp"))


# ── The things a rotation does to the install ─────────────────────


@dataclass
class JobResult:
    ok: bool
    error: str = ""
    columns: dict[str, dict[str, int]] = field(default_factory=dict)
    note: str = ""


class Operations(Protocol):
    """What a rotation needs from the install; ``ComposeOperations`` does it with Docker, tests with a fake."""

    env_path: Path

    def unhealthy(self, services: list[str]) -> list[str]: ...

    def backup(self) -> str: ...

    def maintenance(self) -> AbstractContextManager[Any]: ...

    def restart(self, services: list[str]) -> bool: ...

    def wait_until_healthy(self) -> bool: ...

    def run_job(self, job: Job, *, skip_unreadable: bool) -> JobResult: ...


def parse_job_output(stdout: str, returncode: int, stderr: str = "") -> JobResult:
    """The job's last line is JSON (``{"ok": ..., "columns": ...}``); anything else is a failure."""
    for line in reversed((stdout or "").strip().splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            doc = json.loads(line)
        except ValueError:
            continue
        if isinstance(doc, dict) and "ok" in doc:
            return JobResult(
                ok=bool(doc["ok"]) and returncode == 0,
                error=str(doc.get("error", "")),
                columns=doc.get("columns") or {},
                note=str(doc.get("note", "")),
            )
    tail = " | ".join((stderr or stdout or "").strip().splitlines()[-3:])
    return JobResult(ok=False, error=f"the job gave no result (exit code {returncode}){': ' + tail if tail else ''}")


# ── The rotation ──────────────────────────────────────────────────


@dataclass
class Plan:
    """What a run will do."""

    mode: str  # "fresh" or "resume"
    scopes: list[Scope]
    #: Scopes the user asked for that a resume leaves for the next run.
    deferred: list[Scope] = field(default_factory=list)


def make_plan(
    env: dict[str, str],
    profiles: list[str] | tuple[str, ...],
    only: list[str] | None = None,
    *,
    resume: bool = False,
) -> Plan:
    """Which keys to rotate. An unfinished rotation always comes first: it is finished before another starts."""
    states = key_states(env, profiles)
    unfinished = [s.scope for s in states if s.unfinished]
    wanted = [s for s in states if only is None or s.scope.name in only]
    if unfinished:
        return Plan("resume", unfinished, deferred=[s.scope for s in wanted if s.scope not in unfinished and s.applicable])
    if resume:
        raise RotationError("No rotation is unfinished: .env holds no previous key. Run faden secrets rotate without --resume.")
    not_applicable = [s.scope.name for s in wanted if not s.applicable]
    if only is not None and not_applicable:
        raise RotationError(
            "Nothing to rotate for: " + ", ".join(not_applicable)
            + ". (mcp needs the MCP module switched on; chat needs the chat key set; provenance needs the provenance "
            "module switched on and its keys in .env, which faden upgrade writes.)"
        )
    scopes = [s.scope for s in wanted if s.applicable]
    if not scopes:
        raise RotationError("There is no key to rotate.")
    return Plan("fresh", scopes)


def rotate(
    ops: Operations,
    plan: Plan,
    *,
    skip_unreadable: bool = False,
    new_key: Callable[[], str] = _random_secret,
    say: Callable[[str], None] = print,
) -> dict[str, dict[str, dict[str, int]]]:
    """Run *plan*; returns each job's counts. Raises ``RotationError`` after leaving ``.env`` resumable."""
    env_path = ops.env_path
    services = _services(plan.scopes)

    if plan.mode == "fresh":
        sick = ops.unhealthy(sorted({*ALWAYS_HEALTHY, *services}, key=_order))
        if sick:
            raise RotationError(
                "These services are not healthy, so nothing was changed: " + ", ".join(sick)
                + ".\n  Fix them (faden status, faden logs) and run this again."
            )

    say("Taking a backup first (it holds the current .env)...")
    try:
        where = ops.backup()
    except RotationError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RotationError(f"The backup failed ({exc}); nothing was changed.") from exc
    say(f"Backup: {where}")

    env = read_env_file(env_path)
    if plan.mode == "fresh":
        values: dict[str, str] = {}
        for scope in plan.scopes:
            if scope.renew is not None:
                try:
                    values.update(scope.renew(env_path.parent, env))
                except (provenance_keys.ProvenanceKeyError, TlsError, OSError) as exc:
                    raise RotationError(f"{scope.name}: {exc} Nothing was changed.") from exc
                continue
            values[scope.previous] = env[scope.key]
            values[scope.key] = new_key()
        # The pepper must hold what keyed the hashes so far, before the master key changes.
        if SETTINGS_MASTER_KEY in values and not env.get(CREDENTIAL_PEPPER):
            values[CREDENTIAL_PEPPER] = env[SETTINGS_MASTER_KEY]
        rewrite_env(env_path, set_values=values)
        keeping = any(scope.previous for scope in plan.scopes)
        say(
            "Wrote the new keys to .env"
            + (" (the old ones stay as previous keys until every value is re-sealed)." if keeping else ".")
        )
    else:
        # A resume keeps the new keys. A pepper that is missing is what the master key was before: the previous key.
        settings_scope = next((s for s in plan.scopes if s.name == "settings"), None)
        if settings_scope and not env.get(CREDENTIAL_PEPPER) and env.get(settings_scope.previous):
            rewrite_env(env_path, set_values={CREDENTIAL_PEPPER: env[settings_scope.previous]})
        say("Resuming an unfinished rotation: the keys in .env stay as they are.")

    results: dict[str, dict[str, dict[str, int]]] = {}
    with ops.maintenance():
        say("Restarting the services that use the keys, so they learn both...")
        if not ops.restart(services):
            raise RotationError(_resumable("The services did not restart."))
        if not ops.wait_until_healthy():
            raise RotationError(_resumable("The backend did not become healthy after the restart."))
        for scope in plan.scopes:
            for job in scope.jobs:
                say(f"Re-sealing: {job.label or job.module}...")
                result = ops.run_job(job, skip_unreadable=skip_unreadable)
                if not result.ok:
                    raise RotationError(_resumable(f"{job.label or job.module}: {result.error or 'failed'}"))
                results[job.label or job.module] = result.columns
                if result.note:
                    say(f"  {result.note}")

        # Every value opens with the new keys alone: the old ones can go.
        after = read_env_file(env_path)
        drop = tuple(scope.previous for scope in plan.scopes if scope.previous)
        after_keys = {k: v for k, v in after.items() if k not in drop}
        remove = list(drop)
        if after.get(ALLOW_PUBLISHED_KEYS) and not published_remaining(after_keys):
            remove.append(ALLOW_PUBLISHED_KEYS)
        rewrite_env(env_path, set_values={}, remove=tuple(remove))
        switch = ALLOW_PUBLISHED_KEYS in remove
        if drop:
            tail = " and the published-keys switch." if switch else "."
            say("Dropped the previous keys from .env" + tail)
        elif switch:
            say("Dropped the published-keys switch from .env.")
        if not ops.restart(services):
            raise RotationError(
                "Every value is re-sealed and the previous keys are gone from .env, but the services did not restart "
                "with that. Start them with: faden up"
            )
        if not ops.wait_until_healthy():
            raise RotationError(
                "Every value is re-sealed and the previous keys are gone from .env, but the backend is not healthy "
                "yet. Check: faden logs backend"
            )
    return results


def _resumable(what: str) -> str:
    return (
        f"{what}\n  The new keys are in .env next to the old ones, so the services still open everything. "
        "Finish the rotation by running the same command again (faden secrets rotate --resume)."
    )


def _services(scopes: list[Scope]) -> list[str]:
    seen: list[str] = []
    for scope in scopes:
        for name in scope.services:
            if name not in seen:
                seen.append(name)
    return seen


def _order(name: str) -> int:
    order = ["postgres", "faden-backend", "faden-backend-worker", "faden-gateway", "faden-mcp", "faden-provenance"]
    return order.index(name) if name in order else len(order)


# ── With Docker ───────────────────────────────────────────────────


class ComposeOperations:
    """The install's compose project: what a rotation runs, for real."""

    def __init__(self, cfg: Any) -> None:
        from faden_cli.core.compose import ComposeContext, has_nvidia_gpu  # noqa: PLC0415

        self.install_dir: Path = cfg.install_path
        self.env_path: Path = self.install_dir / ".env"
        compose_file = self.install_dir / cfg.compose_file
        files = [compose_file]
        overlay = self.install_dir / "docker-compose.gpu.yaml"
        if has_nvidia_gpu() and overlay.exists():
            files.append(overlay)
        self.ctx = ComposeContext(
            compose_files=files,
            env_file=self.env_path if self.env_path.exists() else None,
            project_dir=self.install_dir,
            profiles=list(cfg.profiles),
        )

    def unhealthy(self, services: list[str]) -> list[str]:
        from faden_cli.core.compose import ps  # noqa: PLC0415

        found = {svc.service: svc for svc in ps(self.ctx)}
        problems: list[str] = []
        for name in services:
            svc = found.get(name)
            if svc is None:
                problems.append(f"{name} (not created)")
            elif svc.state != "running":
                problems.append(f"{name} ({svc.state or 'not running'})")
            elif svc.health in ("unhealthy", "starting"):
                problems.append(f"{name} ({svc.health})")
        return problems

    def backup(self) -> str:
        from faden_cli.core.backup import create_backup  # noqa: PLC0415

        # A backup kind "rotate" is not in this release: the normal backup (databases, .env, certificates).
        result = create_backup(self.ctx, output_dir=self.install_dir / "backups")
        if not result.ok:
            raise RotationError("The backup failed, so nothing was changed: " + "; ".join(result.errors))
        return str(result.backup_dir)

    def maintenance(self) -> AbstractContextManager[Any]:
        from faden_cli.core import maintenance  # noqa: PLC0415

        return maintenance.during(
            self.install_dir, reason="rotate", message="Changing the keys that protect stored data."
        )

    def restart(self, services: list[str]) -> bool:
        from faden_cli.core.compose import up  # noqa: PLC0415

        return up(
            self.ctx, services=services, detach=True, no_deps=True, force_recreate=True, wait=True, timeout=180,
        ) == 0

    def wait_until_healthy(self) -> bool:
        from faden_cli.core.bootstrap import wait_for_backend  # noqa: PLC0415
        from faden_cli.core.edge import console_url  # noqa: PLC0415

        return wait_for_backend(console_url(self.install_dir, read_env_file(self.env_path)), timeout=120)

    def run_job(self, job: Job, *, skip_unreadable: bool) -> JobResult:
        from faden_cli.core.compose import run_cmd  # noqa: PLC0415

        proc = run_cmd(
            self.ctx, job.service, job.command(skip_unreadable=skip_unreadable),
            capture=True, no_deps=True, no_tty=True,
        )
        return parse_job_output(proc.stdout or "", proc.returncode, proc.stderr or "")


def summary_lines(results: dict[str, dict[str, dict[str, int]]]) -> Iterator[str]:
    """What the jobs did, per column: counts only."""
    for label, columns in results.items():
        total = sum(c.get("rotated", 0) for c in columns.values())
        yield f"{label}: {total} value(s) re-sealed"
        for column, counts in columns.items():
            if counts.get("unreadable"):
                yield f"  {column}: {counts['unreadable']} value(s) neither key opens (left as they are)"
