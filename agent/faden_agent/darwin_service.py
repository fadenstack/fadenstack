"""The agent as a macOS system service: its account, its files and its launchd job.

A Mac node runs the agent as a system LaunchDaemon under a hidden, non-admin
account of its own (``_fadenagent``), so it runs from boot with nobody logged in
(Gate 0b/0d) and holds nothing of the person who installed it. The installer
(root) owns the executable, the configuration and the job definition; the
service owns its private state directory -- the pending join and the
enrollment credential -- and its log file.

Everything that changes the machine (``dscl``, ``dseditgroup``, ``launchctl``,
``chown``, ``pkill``) goes through a :class:`Runner`, so the tests drive these
functions with a stand-in and never touch a real account or job. Paths come
from a :class:`Layout`, which the tests point at a temporary directory.

Order matters: ``KeepAlive`` starts a job as soon as it is loaded, so the job
is bootstrapped only after the join was approved. The account and the
directories are prepared before the join is filed, and the join itself runs
as the service account (the CLI re-runs it as ``_fadenagent``), so the state
it writes is the service's from the start.

The rule that keeps the service from turning root against the machine: root
never writes into, chowns or chmods anything inside a directory the service
can write. The service's directories are created fresh (``mkdir`` as root, then
a non-recursive ``chown -h`` of the directory itself) or, when they exist,
checked with ``lstat`` -- a real directory, the service's, 0700 -- and refused
otherwise. macOS lets any user hardlink a root-owned file on the same volume,
so a recursive ``chown`` over the service's tree would hand it that file.
Root's own directories are checked the same way: root-owned, not a symlink,
not writable by group or others.
"""

from __future__ import annotations

import hashlib
import os
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

LABEL = "com.fadenstack.agent"
SERVICE_USER = "_fadenagent"
#: UID = GID for the service account: the first number free in this range. Apple
#: reserves no range for third parties; 400-499 is the conventional system-account
#: space and parts of it are taken on stock Macs (441 ``_oahd``, GID 400), so the
#: top half is used. A fleet that reserves other numbers sets
#: ``FADEN_NODE_AGENT_MAC_ID_RANGE=lo-hi``.
DEFAULT_ID_RANGE = (450, 499)
ID_RANGE_ENV = "FADEN_NODE_AGENT_MAC_ID_RANGE"
#: Groups the service account must never be in.
FORBIDDEN_GROUPS = ("admin", "staff", "wheel", "com.apple.access_ssh", "com.apple.access_screensharing")
#: How long a new job must keep the same process before it counts as started.
HEALTHY_FOR_SECONDS = 20.0
#: How long to wait for launchd to give the job a process at all.
FIRST_PID_SECONDS = 30.0
#: Builds kept in /Library/Fadenstack/agent: the running one and the one before.
KEEP_BUILDS = 2

DSCL = "/usr/bin/dscl"
DSEDITGROUP = "/usr/sbin/dseditgroup"
LAUNCHCTL = "/bin/launchctl"
CHOWN = "/usr/sbin/chown"
PKILL = "/usr/bin/pkill"
PGREP = "/usr/bin/pgrep"
RM = "/bin/rm"
ID = "/usr/bin/id"
TEST = "/bin/test"
TOUCH = "/usr/bin/touch"


class DarwinServiceError(RuntimeError):
    """A step that must not be half done: the message says what to do instead."""


# ── running commands ──────────────────────────────────────────────


@dataclass
class Result:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class Runner:
    """Runs a program, optionally as another account. Replaced in tests."""

    def run(self, argv: Sequence[str], *, uid: int | None = None, gid: int | None = None) -> Result:
        kwargs: dict = {}
        if uid is not None:
            kwargs.update(user=uid, group=gid, extra_groups=[])
        proc = subprocess.run(  # noqa: S603 - fixed absolute programs, no shell
            list(argv), capture_output=True, text=True, check=False, **kwargs,
        )
        return Result(proc.returncode, proc.stdout, proc.stderr)

    def call(self, argv: Sequence[str], *, uid: int, gid: int, env: dict[str, str], cwd: str) -> int:
        """Run *argv* as *uid*:*gid* with the terminal attached (the join shows its code and waits)."""
        return subprocess.run(  # noqa: S603 - our own staged binary, no shell
            list(argv), check=False, user=uid, group=gid, extra_groups=[], env=env, cwd=cwd,
        ).returncode


def _must(runner: Runner, argv: Sequence[str]) -> Result:
    result = runner.run(argv)
    if result.returncode != 0:
        raise DarwinServiceError(
            f"'{' '.join(argv)}' failed ({result.returncode}): {(result.stderr or result.stdout).strip()}"
        )
    return result


# ── where everything lives ────────────────────────────────────────


@dataclass(frozen=True)
class Layout:
    """Every path the Mac service uses, under *root* (``/`` on a real Mac)."""

    root: Path = Path("/")
    #: Who owns root's directories (0 on a Mac; the tests' own UID under a temporary root).
    root_uid: int = 0

    def _p(self, path: str) -> Path:
        return self.root / path.lstrip("/")

    @property
    def library(self) -> Path:
        return self._p("/Library/Fadenstack")

    @property
    def builds(self) -> Path:
        return self.library / "agent"

    @property
    def current(self) -> Path:
        return self.builds / "current"

    @property
    def current_binary(self) -> Path:
        return self.current / "faden-agent"

    @property
    def env_file(self) -> Path:
        return self.library / "agent.env"

    @property
    def cli_link(self) -> Path:
        return self._p("/usr/local/bin/faden-agent")

    @property
    def state_dir(self) -> Path:
        return self._p("/var/db/fadenstack/agent")

    @property
    def state_file(self) -> Path:
        return self.state_dir / "state.json"

    @property
    def models(self) -> Path:
        return self.state_dir / "models"

    @property
    def marker(self) -> Path:
        # Root-owned and outside the service's directory, so the service cannot alter it.
        return self._p("/var/db/fadenstack/agent.marker")

    @property
    def log_dir(self) -> Path:
        return self._p("/var/log/fadenstack")

    @property
    def log_file(self) -> Path:
        return self.log_dir / "agent.log"

    @property
    def launchd_log(self) -> Path:
        return self.log_dir / "launchd.log"

    @property
    def plist(self) -> Path:
        return self._p(f"/Library/LaunchDaemons/{LABEL}.plist")

    @property
    def newsyslog(self) -> Path:
        return self._p("/etc/newsyslog.d/fadenstack.conf")

    def real(self, path: Path) -> str:
        """*path* as the machine sees it (what goes into the plist and the log config)."""
        return "/" + str(path.relative_to(self.root)).lstrip("/")


# ── the account ───────────────────────────────────────────────────


@dataclass
class Identity:
    name: str
    uid: int
    gid: int
    groups: list[str] = field(default_factory=list)


def id_range(environ: dict[str, str] | None = None) -> tuple[int, int]:
    raw = (environ if environ is not None else os.environ).get(ID_RANGE_ENV, "").strip()
    if not raw:
        return DEFAULT_ID_RANGE
    match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", raw)
    if not match:
        raise DarwinServiceError(f"{ID_RANGE_ENV}={raw!r} is not a range like 450-499.")
    lo, hi = int(match.group(1)), int(match.group(2))
    if not 200 <= lo <= hi <= 499:
        raise DarwinServiceError(f"{ID_RANGE_ENV}={raw!r} must lie within 200-499 (system accounts).")
    return lo, hi


def _read_attr(runner: Runner, collection: str, name: str, attr: str) -> str | None:
    """One attribute of a record anywhere on the search path, or None when there is no record."""
    result = runner.run([DSCL, "/Search", "-read", f"/{collection}/{name}", attr])
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        key, _, value = line.partition(":")
        if key.strip() == attr:
            value = value.strip()
            return value if value else None
    return ""


def _record_exists(runner: Runner, collection: str, name: str) -> bool:
    return runner.run([DSCL, "/Search", "-read", f"/{collection}/{name}", "RecordName"]).returncode == 0


def _holders(runner: Runner, collection: str, attr: str, number: int) -> list[str]:
    """Names of the records on the search path whose *attr* is *number*."""
    result = runner.run([DSCL, "/Search", "-search", f"/{collection}", attr, str(number)])
    if result.returncode != 0:
        # No match is exit 0 with no output; anything else must not read as "free".
        raise DarwinServiceError(
            f"Could not search the directory for {attr} {number}: {(result.stderr or result.stdout).strip()}"
        )
    # Each hit reads "<name>\t\t<attr> = (", then the values, then ")".
    names = []
    for line in result.stdout.splitlines():
        match = re.match(rf"(\S+)\s+{re.escape(attr)} = \(", line)
        if match:
            names.append(match.group(1))
    return names


def read_marker(layout: Layout) -> dict[str, str]:
    try:
        text = layout.marker.read_text(encoding="utf-8")
    except OSError:
        return {}
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def _write_marker(layout: Layout, number: int, version: str) -> None:
    layout.marker.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(layout.marker.parent, 0o755)
    layout.marker.write_text(
        f"name={SERVICE_USER}\nid={number}\ncreated={time.strftime('%Y-%m-%dT%H:%M:%S%z')}\nversion={version}\n",
        encoding="utf-8",
    )
    os.chmod(layout.marker, 0o644)


def allocate_id(runner: Runner, lo: int, hi: int) -> int:
    for number in range(lo, hi + 1):
        if not (
            _holders(runner, "Users", "UniqueID", number)
            or _holders(runner, "Groups", "PrimaryGroupID", number)
            # a user whose primary group is a number with no group record
            or _holders(runner, "Users", "PrimaryGroupID", number)
        ):
            return number
    raise DarwinServiceError(f"No UID/GID free in {lo}-{hi}; set {ID_RANGE_ENV} to a free range.")


def _verify_existing(runner: Runner, layout: Layout) -> Identity:
    """An account that already exists is reused only if this installer made it and it still matches."""
    marker = read_marker(layout)
    uid = _read_attr(runner, "Users", SERVICE_USER, "UniqueID")
    gid = _read_attr(runner, "Groups", SERVICE_USER, "PrimaryGroupID")
    if not marker.get("id"):
        raise DarwinServiceError(
            f"An account or group named {SERVICE_USER} exists, but Fadenstack did not create it "
            f"(no {layout.real(layout.marker)}). Refusing to use or change it."
        )
    want = marker["id"]
    if uid != want or gid != want:
        raise DarwinServiceError(
            f"{SERVICE_USER} has UID {uid} and GID {gid}, but Fadenstack created it as {want}. "
            "Refusing to continue: something else changed the account."
        )
    number = int(want)
    # A later macOS may have claimed the number for one of its own accounts.
    others = [n for n in _holders(runner, "Users", "UniqueID", number) if n != SERVICE_USER]
    others += [n for n in _holders(runner, "Groups", "PrimaryGroupID", number) if n != SERVICE_USER]
    if others:
        raise DarwinServiceError(
            f"ID {number} of {SERVICE_USER} is now also used by {', '.join(sorted(set(others)))}. "
            "Uninstall (sudo faden-agent uninstall) and install again to get a new ID."
        )
    expected = {
        "UserShell": "/usr/bin/false",
        "IsHidden": "1",
        "PrimaryGroupID": want,
        "NFSHomeDirectory": layout.real(layout.state_dir),
    }
    for attr, value in expected.items():
        found = _read_attr(runner, "Users", SERVICE_USER, attr)
        if found != value:
            raise DarwinServiceError(f"{SERVICE_USER} has {attr} {found!r}, expected {value!r}. Refusing to continue.")
    return Identity(SERVICE_USER, number, number)


def _create(runner: Runner, layout: Layout, version: str, environ: dict[str, str] | None) -> Identity:
    for collection in ("Users", "Groups"):
        if runner.run([DSCL, "/Search", "-search", f"/{collection}", "RecordName", SERVICE_USER]).stdout.strip():
            raise DarwinServiceError(f"A directory account named {SERVICE_USER} exists. Refusing to create a local one.")
    lo, hi = id_range(environ)
    number = allocate_id(runner, lo, hi)
    user, group = f"/Users/{SERVICE_USER}", f"/Groups/{SERVICE_USER}"
    _write_marker(layout, number, version)
    created_group = created_user = False
    try:
        for argv in (
            [DSCL, ".", "-create", group],
            [DSCL, ".", "-create", group, "PrimaryGroupID", str(number)],
            [DSCL, ".", "-create", group, "RealName", "Fadenstack Node Agent"],
            [DSCL, ".", "-create", group, "Password", "*"],
        ):
            _must(runner, argv)
            created_group = True
        for argv in (
            [DSCL, ".", "-create", user],
            [DSCL, ".", "-create", user, "UniqueID", str(number)],
            [DSCL, ".", "-create", user, "PrimaryGroupID", str(number)],
            [DSCL, ".", "-create", user, "RealName", "Fadenstack Node Agent"],
            [DSCL, ".", "-create", user, "IsHidden", "1"],
            [DSCL, ".", "-create", user, "UserShell", "/usr/bin/false"],
            [DSCL, ".", "-create", user, "NFSHomeDirectory", layout.real(layout.state_dir)],
            [DSCL, ".", "-create", user, "Password", "*"],
        ):
            _must(runner, argv)
            created_user = True
    except DarwinServiceError:
        # An account half made is worse than none: take back what this run created.
        if created_user:
            runner.run([DSCL, ".", "-delete", user])
        if created_group:
            runner.run([DSCL, ".", "-delete", group])
        layout.marker.unlink(missing_ok=True)
        raise
    return Identity(SERVICE_USER, number, number)


def ensure_account(runner: Runner, layout: Layout, version: str, environ: dict[str, str] | None = None) -> Identity:
    """The service account: verified if it exists, created if not."""
    if _record_exists(runner, "Users", SERVICE_USER) or _record_exists(runner, "Groups", SERVICE_USER):
        identity = _verify_existing(runner, layout)
    else:
        if layout.marker.exists():
            layout.marker.unlink()  # a marker for an account that is gone (an interrupted uninstall)
        identity = _create(runner, layout, version, environ)
    for group in FORBIDDEN_GROUPS:
        if runner.run([DSEDITGROUP, "-o", "checkmember", "-m", SERVICE_USER, group]).returncode == 0:
            raise DarwinServiceError(f"{SERVICE_USER} is a member of {group}; it must not be. Refusing to continue.")
    # Recorded, not refused: every local account inherits these through the
    # nested 'everyone'/'localaccounts' groups (Gate 0d saw
    # com.apple.sharepoint.group.1 and _lpoperator).
    groups = runner.run([ID, "-Gn", SERVICE_USER])
    identity.groups = groups.stdout.split() if groups.returncode == 0 else []
    return identity


# ── files ─────────────────────────────────────────────────────────


def _chown(runner: Runner, owner: str, *paths: Path) -> None:
    """Change the owner of *paths* themselves: never recursive, never through a symlink."""
    _must(runner, [CHOWN, "-h", owner, *map(str, paths)])


def newsyslog_conf(layout: Layout) -> str:
    """Rotation for the logs: launchd never rotates them.

    The agent writes agent.log itself and reopens it after a rotation, so no
    signal is needed (N). launchd.log only receives what escapes the agent's
    logging -- a crash's traceback -- and is reopened at the job's next start.
    """
    owner = f"{SERVICE_USER}:{SERVICE_USER}"
    return (
        "# logfilename                    [owner:group]              mode count size(KB) when flags\n"
        f"{layout.real(layout.log_file)}  {owner}  640  7  10240  *  NZ\n"
        f"{layout.real(layout.launchd_log)}  {owner}  640  3  1024  *  NZ\n"
    )


def _root_dir(layout: Layout, directory: Path) -> None:
    """A directory of root's: made 0755 if missing, else checked to be root's alone."""
    try:
        st = os.lstat(directory)
    except FileNotFoundError:
        directory.parent.mkdir(parents=True, exist_ok=True)
        os.mkdir(directory, 0o755)
        os.chmod(directory, 0o755)  # the umask may have taken bits away
        st = os.lstat(directory)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != layout.root_uid or st.st_mode & 0o022:
        raise DarwinServiceError(
            f"{layout.real(directory)} exists but is not a directory owned by root and writable only by it "
            f"(owner {st.st_uid}, mode {oct(st.st_mode & 0o7777)}). Refusing to install into it."
        )


def _check_service_dir(layout: Layout, directory: Path, identity: Identity) -> None:
    st = os.lstat(directory)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != identity.uid or st.st_mode & 0o7777 != 0o700:
        raise DarwinServiceError(
            f"{layout.real(directory)} is not {SERVICE_USER}'s private directory "
            f"(owner {st.st_uid}, mode {oct(st.st_mode & 0o7777)}, symlink {stat.S_ISLNK(st.st_mode)}). "
            "Refusing to use it; remove it, or uninstall and install again."
        )


def _service_log(runner: Runner, layout: Layout, log: Path, identity: Identity) -> None:
    """A log file of the service's, in root's log directory: created once, else checked."""
    try:
        st = os.lstat(log)
    except FileNotFoundError:
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640)
        os.close(fd)
        os.chmod(log, 0o640)
        _chown(runner, f"{identity.uid}:{identity.gid}", log)
        return
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_uid != identity.uid:
        raise DarwinServiceError(
            f"{layout.real(log)} is not a plain log file of {SERVICE_USER}'s. Refusing to use it."
        )


def prepare_files(runner: Runner, layout: Layout, identity: Identity) -> None:
    """Directories and files, owned as the service contract says. Idempotent.

    Root never reaches into the service's directory: it is made fresh, with
    its models directory, while still root's, and then handed over by changing
    the owner of those two directories alone. An existing one is only checked.
    """
    for directory in (layout.library, layout.builds, layout.log_dir, layout.state_dir.parent):
        _root_dir(layout, directory)
    owner = f"{identity.uid}:{identity.gid}"
    try:
        os.lstat(layout.state_dir)
    except FileNotFoundError:
        os.mkdir(layout.state_dir, 0o700)
        os.mkdir(layout.models, 0o700)
        os.chmod(layout.state_dir, 0o700)
        os.chmod(layout.models, 0o700)
        _chown(runner, owner, layout.models)  # before its parent becomes the service's
        _chown(runner, owner, layout.state_dir)
    _check_service_dir(layout, layout.state_dir, identity)
    for log in (layout.log_file, layout.launchd_log):
        _service_log(runner, layout, log, identity)
    _root_dir(layout, layout.newsyslog.parent)
    _atomic_write(layout.newsyslog, newsyslog_conf(layout).encode(), 0o644)


def probe_state_dir(runner: Runner, layout: Layout, identity: Identity) -> None:
    """Prove the service account can write and read its state before a join is filed.

    A join whose approval cannot be stored is an approval lost: the backend
    hands out the credential once. The probe is made and removed by the
    service account; root does not touch its directory.
    """
    probe = str(layout.state_dir / ".write-probe")
    as_service = {"uid": identity.uid, "gid": identity.gid}
    written = runner.run([TOUCH, probe], **as_service)
    readable = runner.run([TEST, "-r", probe], **as_service)
    runner.run([RM, "-f", probe], **as_service)
    if written.returncode != 0 or readable.returncode != 0:
        raise DarwinServiceError(
            f"{SERVICE_USER} cannot write its state directory {layout.real(layout.state_dir)}. Nothing was filed."
        )


def write_env_file(runner: Runner, layout: Layout, content: str) -> Path:
    """The configuration: root's to change, the service's to read (root:_fadenagent 0640).

    Created 0600 and only opened to the group once it is the service's group:
    it can hold an enrollment token.
    """
    _root_dir(layout, layout.env_file.parent)
    gid = _read_attr(runner, "Groups", SERVICE_USER, "PrimaryGroupID")

    def share(tmp: Path) -> None:
        if gid:
            _chown(runner, f"0:{gid}", tmp)
            os.chmod(tmp, 0o640)

    _atomic_write(layout.env_file, content.encode("utf-8"), 0o600, before_replace=share)
    return layout.env_file


def _atomic_write(
    path: Path, data: bytes, mode: int, *, before_replace: Callable[[Path], None] | None = None
) -> None:
    """Write *path* by renaming a temporary file over it; the file never exists with a wider mode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        os.fchmod(fd, mode)  # whatever the umask
        os.write(fd, data)
    finally:
        os.close(fd)
    try:
        if before_replace:
            before_replace(tmp)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _atomic_symlink(link: Path, target: str) -> None:
    tmp = link.with_name(f".{link.name}.{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)
    tmp.symlink_to(target)
    os.replace(tmp, link)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stage_binary(runner: Runner, layout: Layout, source: Path, version: str) -> str:
    """Copy *source* into its own build directory; returns that directory's name.

    The name carries the version and the start of the digest, so two builds of
    one version (a developer's) are two directories and a rollback goes back to
    the bytes that ran before.
    """
    digest = _sha256(source)
    name = f"{version}-{digest[:12]}"
    target_dir = layout.builds / name
    target = target_dir / "faden-agent"
    if not (target.exists() and _sha256(target) == digest):
        if not target_dir.exists():
            os.mkdir(target_dir, 0o755)
        _root_dir(layout, target_dir)
        tmp = target_dir / ".faden-agent.tmp"
        tmp.unlink(missing_ok=True)
        shutil.copyfile(source, tmp)
        os.chmod(tmp, 0o755)
        if _sha256(tmp) != digest:
            tmp.unlink(missing_ok=True)
            raise DarwinServiceError(f"The copy of {source} in {layout.real(target_dir)} does not match it.")
        os.replace(tmp, target)
        _chown(runner, "0:0", target_dir, target)
    # Every time, a retry included: a build that failed this once must not pass by being there already.
    version_out = runner.run([str(target), "--version"])
    if version_out.returncode != 0 or version not in version_out.stdout:
        if current_build(layout) != name:
            shutil.rmtree(target_dir, ignore_errors=True)
        raise DarwinServiceError(
            f"{layout.real(target)} does not run as agent {version}: {(version_out.stderr or version_out.stdout).strip()}"
        )
    return name


def staged_binary(layout: Layout, name: str) -> Path:
    return layout.builds / name / "faden-agent"


def current_build(layout: Layout) -> str | None:
    try:
        return os.readlink(layout.current)
    except OSError:
        return None


def select_build(layout: Layout, name: str) -> None:
    """Point ``current`` at *name*, atomically."""
    _atomic_symlink(layout.current, name)


def link_cli(layout: Layout) -> None:
    """``/usr/local/bin/faden-agent`` is the service's build, not a second copy."""
    layout.cli_link.parent.mkdir(parents=True, exist_ok=True)
    _atomic_symlink(layout.cli_link, layout.real(layout.current_binary))


def prune_builds(layout: Layout, keep: Sequence[str]) -> None:
    for entry in layout.builds.iterdir():
        if entry.name == "current" or entry.name in keep or not entry.is_dir():
            continue
        shutil.rmtree(entry, ignore_errors=True)


def plist_content(layout: Layout, identity: Identity) -> bytes:
    """The job. Never ``NumberOfFiles``: in a system daemon it rewrites host-wide sysctls."""
    state = layout.real(layout.state_dir)
    job = {
        "Label": LABEL,
        "UserName": identity.name,
        "GroupName": identity.name,
        "ProgramArguments": [layout.real(layout.current_binary), "run"],
        "WorkingDirectory": state,
        "EnvironmentVariables": {
            "HOME": state,
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "FADEN_NODE_AGENT_LOG_FILE": layout.real(layout.log_file),
        },
        "StandardOutPath": layout.real(layout.launchd_log),
        "StandardErrorPath": layout.real(layout.launchd_log),
        "ProcessType": "Standard",
        "LowPriorityIO": False,
        "KeepAlive": True,
        "ThrottleInterval": 15,
        "ExitTimeOut": 45,
    }
    return plistlib.dumps(job, sort_keys=False)


# ── the job ───────────────────────────────────────────────────────


@dataclass
class JobState:
    loaded: bool
    pid: int | None = None
    runs: int | None = None
    last_exit: int | None = None


def job_state(runner: Runner) -> JobState:
    result = runner.run([LAUNCHCTL, "print", f"system/{LABEL}"])
    if result.returncode != 0:
        return JobState(loaded=False)
    state = JobState(loaded=True)
    for line in result.stdout.splitlines():
        key, sep, value = line.strip().partition(" = ")
        if not sep:
            continue
        value = value.strip()
        if key == "pid" and value.isdigit():
            state.pid = int(value)
        elif key == "runs" and value.isdigit():
            state.runs = int(value)
        elif key == "last exit code":
            match = re.match(r"-?\d+", value)
            state.last_exit = int(match.group()) if match else None
    return state


def bootstrap(runner: Runner, layout: Layout) -> None:
    runner.run([LAUNCHCTL, "enable", f"system/{LABEL}"])
    _must(runner, [LAUNCHCTL, "bootstrap", "system", layout.real(layout.plist)])


def bootout(runner: Runner, *, sleep: Callable[[float], None] = time.sleep) -> None:
    if not job_state(runner).loaded:
        return
    runner.run([LAUNCHCTL, "bootout", f"system/{LABEL}"])
    for _ in range(60):
        if not job_state(runner).loaded:
            return
        sleep(1.0)
    raise DarwinServiceError(f"launchd did not unload system/{LABEL}.")


def wait_healthy(
    runner: Runner,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    first_pid: float = FIRST_PID_SECONDS,
    healthy_for: float = HEALTHY_FOR_SECONDS,
) -> tuple[bool, str]:
    """Whether the job settled: one process, kept for *healthy_for*, never restarted, no failed exit."""
    deadline = clock() + first_pid
    state = job_state(runner)
    while state.pid is None:
        if clock() >= deadline:
            return False, f"launchd started no process within {int(first_pid)} s (last exit {state.last_exit})"
        sleep(1.0)
        state = job_state(runner)
    pid, runs = state.pid, state.runs
    until = clock() + healthy_for
    while clock() < until:
        sleep(1.0)
        state = job_state(runner)
        if state.pid != pid:
            return False, f"the process {pid} exited (last exit {state.last_exit})"
        if runs is not None and state.runs is not None and state.runs > runs:
            return False, f"launchd restarted it (runs {runs} -> {state.runs})"
    if state.last_exit not in (None, 0):
        return False, f"it exited with {state.last_exit} before"
    return True, f"running as PID {pid}"


def activate(
    runner: Runner,
    layout: Layout,
    identity: Identity,
    source: Path,
    version: str,
    *,
    say: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Install *source* as the service's build and (re)start the job; roll back if it does not settle.

    Only called once the machine is a member (approved, or enrolled by token).
    """
    name = stage_binary(runner, layout, source, version)
    _atomic_write(layout.plist, plist_content(layout, identity), 0o644)
    _chown(runner, "0:0", layout.plist)

    previous = current_build(layout)
    was_loaded = job_state(runner).loaded
    bootout(runner, sleep=sleep)
    try:
        select_build(layout, name)
        link_cli(layout)
        bootstrap(runner, layout)
    except (DarwinServiceError, OSError) as exc:
        ok, why = False, f"it could not be loaded ({exc})"
    else:
        say("  Waiting for the service to settle...")
        ok, why = wait_healthy(runner, sleep=sleep, clock=clock)
    if ok:
        prune_builds(layout, [name, previous] if previous else [name])
        say(f"  Service {LABEL} {why}.")
        return

    if previous and previous != name and staged_binary(layout, previous).exists():
        try:
            bootout(runner, sleep=sleep)
            select_build(layout, previous)
            bootstrap(runner, layout)
            back, back_why = wait_healthy(runner, sleep=sleep, clock=clock)
        except (DarwinServiceError, OSError) as exc:
            back, back_why = False, f"it could not be loaded either ({exc})"
        state = "running again" if back else f"not healthy either: {back_why}"
        raise DarwinServiceError(
            f"Build {name} did not start: {why}. Rolled back to {previous}, which is {state}. "
            f"See {layout.real(layout.log_file)} and {layout.real(layout.launchd_log)}."
        )
    detail = "" if was_loaded else " It stays loaded and launchd keeps retrying."
    raise DarwinServiceError(
        f"The service did not start: {why}.{detail} See {layout.real(layout.log_file)} and {layout.real(layout.launchd_log)}."
    )


def stop(runner: Runner, layout: Layout) -> bool:
    """Stop the job and remove its definition (as ``stop`` removes the unit on Linux)."""
    was = job_state(runner).loaded
    bootout(runner)
    layout.plist.unlink(missing_ok=True)
    return was


def uninstall(
    runner: Runner,
    layout: Layout,
    *,
    say: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Remove the job, the account (only if Fadenstack created it), the state, logs and builds.

    Returns whether everything is gone; what is left is reported through *say*.
    """
    stop(runner, layout)
    number = read_marker(layout).get("id")
    uid = _read_attr(runner, "Users", SERVICE_USER, "UniqueID")
    gid = _read_attr(runner, "Groups", SERVICE_USER, "PrimaryGroupID")
    ours = bool(number) and uid == number and gid == number
    if ours:
        # The per-user distnoted/cfprefsd agents launchd started for the account
        # outlive the job; an account is not deleted under running processes.
        # Only now that the number is known to be this account's.
        runner.run([PKILL, "-TERM", "-U", number])
        for _ in range(10):
            if runner.run([PGREP, "-U", number]).returncode != 0:
                break
            sleep(1.0)
        else:
            runner.run([PKILL, "-KILL", "-U", number])
        _must(runner, [DSCL, ".", "-delete", f"/Users/{SERVICE_USER}"])
        _must(runner, [DSCL, ".", "-delete", f"/Groups/{SERVICE_USER}"])
        say(f"  Deleted the account {SERVICE_USER} ({number}).")
    elif uid is not None or gid is not None:
        why = (
            "Fadenstack did not create it (no marker)" if not number
            else f"its UID {uid} and GID {gid} are not the {number} Fadenstack created"
        )
        say(f"  Not deleting {SERVICE_USER} and not stopping its processes: {why}.")

    left: list[str] = []
    try:
        if layout.cli_link.is_symlink() and os.readlink(layout.cli_link).startswith(layout.real(layout.library)):
            layout.cli_link.unlink()
    except OSError as exc:
        left.append(f"{layout.real(layout.cli_link)} ({exc.strerror})")

    def note(_func: object, path: str, info: object) -> None:
        exc = info[1] if isinstance(info, tuple) else info  # onerror (3.11) gives exc_info
        left.append(f"{path} ({getattr(exc, 'strerror', None) or exc})")

    # rmtree does not follow symlinks, and unlinking a hardlink only removes the entry.
    for path in (layout.state_dir, layout.log_dir, layout.library):
        if os.path.lexists(path):
            if sys.version_info >= (3, 12):
                shutil.rmtree(path, onexc=note)
            else:  # the agent still supports 3.11
                shutil.rmtree(path, onerror=note)
    try:
        layout.newsyslog.unlink(missing_ok=True)
    except OSError as exc:
        left.append(f"{layout.real(layout.newsyslog)} ({exc.strerror})")
    for item in left:
        say(f"  Could not remove {item}.")
    account_gone = not _record_exists(runner, "Users", SERVICE_USER) and not _record_exists(
        runner, "Groups", SERVICE_USER
    )
    if account_gone and not left:
        layout.marker.unlink(missing_ok=True)
        try:
            layout.marker.parent.rmdir()
        except OSError:
            pass
    return not left and (account_gone or not ours)


def running_binary() -> Path | None:
    """The built agent this process is, when it is one (a PyInstaller build)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return None


def raise_open_file_limit(target: int = 65536) -> None:
    """launchd gives a job soft 256 / hard unlimited; raise the soft limit here, not in the plist."""
    try:
        import resource  # noqa: PLC0415 - POSIX only

        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        want = target if hard == resource.RLIM_INFINITY else min(target, hard)
        if soft < want:
            resource.setrlimit(resource.RLIMIT_NOFILE, (want, hard))
    except (ImportError, ValueError, OSError):
        pass
