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
import logging
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
        """Run *argv* as *uid*:*gid*, its output on this terminal (the join shows its code and waits).

        In a session of its own (setsid): with no controlling terminal, a
        process of the service's cannot push keystrokes (TIOCSTI) into the
        administrator's shell. Ctrl+C reaches this process, which stops it.
        """
        return subprocess.run(  # noqa: S603 - our own staged binary, no shell
            list(argv), check=False, user=uid, group=gid, extra_groups=[], env=env, cwd=cwd,
            stdin=subprocess.DEVNULL, start_new_session=True,
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


def _read_record(
    runner: Runner, collection: str, name: str, attrs: Sequence[str] = ()
) -> dict[str, list[str]] | None:
    """A record's attributes (all, or *attrs*), by their bare names; None when there is no record.

    Read as a plist: dscl's text form names some attributes with a prefix
    (``dsAttrTypeNative:IsHidden: 1``) and puts a multi-word value on the next
    line (``RealName:`` then `` Fadenstack Node Agent``). In the plist every
    key is ``dsAttrTypeStandard:X`` or ``dsAttrTypeNative:X`` and every value
    an array of strings. An attribute the record lacks is simply absent.
    """
    result = runner.run([DSCL, "-plist", "/Search", "-read", f"/{collection}/{name}", *attrs])
    if result.returncode != 0:
        return None
    try:
        data = plistlib.loads(result.stdout.encode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - any parse failure is the same refusal
        raise DarwinServiceError(f"Could not read the directory record /{collection}/{name}: {exc}") from exc
    if not isinstance(data, dict):
        raise DarwinServiceError(f"Could not read the directory record /{collection}/{name}: not a dictionary")
    record: dict[str, list[str]] = {}
    for key, value in data.items():
        values = value if isinstance(value, list) else [value]
        record[str(key).rsplit(":", 1)[-1]] = [str(v) for v in values]
    return record


def _read_attr(runner: Runner, collection: str, name: str, attr: str) -> str | None:
    """One attribute of a record anywhere on the search path.

    None when there is no record, "" when the record lacks the attribute.
    """
    record = _read_record(runner, collection, name, [attr])
    if record is None:
        return None
    values = record.get(attr) or [""]
    return values[0]


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
    _root_dir(layout, layout.marker.parent)
    _atomic_write(
        layout.marker,
        f"name={SERVICE_USER}\nid={number}\ncreated={time.strftime('%Y-%m-%dT%H:%M:%S%z')}\nversion={version}\n".encode(),
        0o644,
    )


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
    user = _read_record(runner, "Users", SERVICE_USER) or {}
    group = _read_record(runner, "Groups", SERVICE_USER) or {}
    uid = (user.get("UniqueID") or [None])[0]
    gid = (group.get("PrimaryGroupID") or [None])[0]
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
        "Password": "*",
        "UserShell": "/usr/bin/false",
        "IsHidden": "1",
        "PrimaryGroupID": want,
        "NFSHomeDirectory": layout.real(layout.state_dir),
    }
    for attr, value in expected.items():
        found = (user.get(attr) or [""])[0]
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


def _unfinished_hand_over(layout: Layout, st: os.stat_result, identity: Identity) -> bool:
    """A state directory still root's because a run stopped between mkdir and chown.

    Finished only while nothing is in it but an empty models directory of
    root's: nobody but root could have put anything there, and there is none.
    """
    if st.st_uid == identity.uid or st.st_uid != layout.root_uid or not stat.S_ISDIR(st.st_mode):
        return False
    entries = os.listdir(layout.state_dir)
    if entries == []:
        return True
    if entries == ["models"]:
        models = os.lstat(layout.models)
        return (
            stat.S_ISDIR(models.st_mode) and models.st_uid == layout.root_uid and os.listdir(layout.models) == []
        )
    return False


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
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_uid not in (identity.uid, layout.root_uid):
        raise DarwinServiceError(
            f"{layout.real(log)} is not a plain log file of {SERVICE_USER}'s. Refusing to use it."
        )
    if st.st_uid == layout.root_uid and st.st_uid != identity.uid:
        # launchd or newsyslog made it as root. Root's directory, so nobody else
        # can swap it; still opened without following links and checked again.
        fd = os.open(log, os.O_WRONLY | os.O_NOFOLLOW)
        try:
            again = os.fstat(fd)
            if not stat.S_ISREG(again.st_mode) or again.st_nlink != 1 or again.st_ino != st.st_ino:
                raise DarwinServiceError(f"{layout.real(log)} changed while being checked. Refusing to use it.")
            os.fchmod(fd, 0o640)
        finally:
            os.close(fd)
        _chown(runner, f"{identity.uid}:{identity.gid}", log)


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
        st = os.lstat(layout.state_dir)
    except FileNotFoundError:
        os.mkdir(layout.state_dir, 0o700)
        st = None
    if st is None or _unfinished_hand_over(layout, st, identity):
        for directory in (layout.state_dir, layout.models):
            if not os.path.lexists(directory):
                os.mkdir(directory, 0o700)
            os.chmod(directory, 0o700)
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
    bootout(runner, sleep=sleep)
    load_error: Exception | None = None
    try:
        select_build(layout, name)
        link_cli(layout)
        bootstrap(runner, layout)
    except (DarwinServiceError, OSError) as exc:
        load_error = exc
        if not previous or previous == name:
            # Nothing else to go back to: try loading once more (a transient launchctl error).
            sleep(2.0)
            try:
                bootstrap(runner, layout)
                load_error = None
            except (DarwinServiceError, OSError) as again:
                load_error = again
    if load_error is not None:
        ok, why = False, f"it could not be loaded ({load_error})"
    else:
        say("  Waiting for the service to settle...")
        ok, why = wait_healthy(runner, sleep=sleep, clock=clock)
    if ok:
        # Only a change of build prunes (an upgrade, or a first install that finds
        # a build an interrupted run staged), and it keeps this build and the one
        # before it. A restart of the same build (stop, then start) keeps every
        # build, so the rollback target an upgrade left behind survives it.
        if previous != name:
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
    if job_state(runner).loaded:
        detail = "It stays loaded and launchd keeps restarting it."
    else:
        detail = "It is not running. Once the cause is fixed, start it with: sudo faden-agent start"
    raise DarwinServiceError(
        f"The service did not start: {why}. {detail} "
        f"See {layout.real(layout.log_file)} and {layout.real(layout.launchd_log)}."
    )


def stop(runner: Runner, layout: Layout, *, say: Callable[[str], None] = print) -> bool:
    """Stop the job and remove its definition (as ``stop`` removes the unit on Linux).

    Its native workers (vLLM) go too: an agent restart leaves them running
    for the next agent to adopt, but after ``stop`` nothing would, and an
    orphaned vLLM holds tens of GB with no proxy in front of it. ``activate``
    (install, upgrade) only boots the job out, and keeps them.
    """
    was = job_state(runner).loaded
    bootout(runner)
    layout.plist.unlink(missing_ok=True)
    uid = _read_attr(runner, "Users", SERVICE_USER, "UniqueID")
    if uid and uid.isdigit():
        try:
            stop_native_workers(layout, int(uid), say=say)
        except Exception as exc:  # noqa: BLE001 - stop and uninstall go on; pkill -U remains the backstop
            say(f"  Could not stop the model workers: {exc}")
    return was


def stop_native_workers(
    layout: Layout, uid: int, *, say: Callable[[str], None] = print, grace_s: float = 30.0
) -> list[str]:
    """End the service's vLLM workers, as root, reading their records only.

    Root never writes in the service's directory. Each directory on the way
    is opened without following a symlink and must belong to the service
    user; each record is read the same way and must be a regular file with
    one link, owned by the service user, and valid field by field. A
    record's process must still be the recorded one (pid, creation time,
    command) and run as the service user; only such processes (and its
    verified descendants) are signalled, one by one -- never by user
    (``distnoted``/``cfprefsd`` run as it too). A bad record is reported
    and skipped: it never stops the rest, nor the uninstall after it.
    Returns the deployments with processes left.
    """
    from faden_agent.native import proc as native_proc  # noqa: PLC0415

    left: list[str] = []
    records, known = _worker_records(layout, uid, say=say)
    for name, record in records:
        shown = name if _SAFE_NAME.fullmatch(name) else repr(name)[:80]
        try:
            native_proc.validate(record)
            if record.get("state") not in ("starting", "ready"):
                continue
            leader = native_proc.matching(record, uid=uid)
            survivors = native_proc.stop_worker(record, leader, grace_s=grace_s, uid=uid)
        except Exception as exc:  # noqa: BLE001 - one bad record must not stop the rest
            say(f"  Skipped the model worker record {shown}: {exc}")
            continue
        if survivors:
            left.append(name)
            say(f"  The model worker {shown} still runs (pids {survivors}).")
        elif leader is not None:
            say(f"  Stopped the model worker {shown}.")
    # Whatever a record did not name (a corrupt record, a helper that detached):
    # every process of the service user carrying the marker of a deployment
    # this agent has a record file for -- this agent's workers, no one else's.
    try:
        rest = native_proc.marked(uid=uid, deployments=known) if known else []
        if rest:
            survivors = native_proc.stop_processes(rest, grace_s=grace_s, uid=uid)
            say(f"  Stopped {len(rest) - len(survivors)} more model worker process(es) found by their marker.")
            if survivors:
                left.append("marker")
                say(f"  Model worker processes still run (pids {survivors}).")
    except Exception as exc:  # noqa: BLE001 - stop and uninstall go on
        say(f"  Could not look for unrecorded model worker processes: {exc}")
    return left


_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_RECORDS = 64


def _open_dir(name: str, uid: int, *, dir_fd: int | None = None) -> int | None:
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(name, flags, dir_fd=dir_fd)
    except OSError:
        return None
    if os.fstat(fd).st_uid != uid:
        os.close(fd)
        return None
    return fd


def _worker_records(
    layout: Layout, uid: int, *, say: Callable[[str], None]
) -> tuple[list[tuple[str, dict]], set[str]]:
    """``(name, record)`` for each readable worker record, and every deployment with a
    record file (readable, corrupt or set aside); nothing raises."""
    from faden_agent.native.common import read_json_nofollow  # noqa: PLC0415

    out: list[tuple[str, dict]] = []
    known: set[str] = set()
    fds: list[int] = []
    try:
        fd = _open_dir(str(layout.state_dir), uid)
        for part in ("native", "workers"):
            if fd is None:
                return out, known
            fds.append(fd)
            fd = _open_dir(part, uid, dir_fd=fd)
        if fd is None:
            return out, known
        fds.append(fd)
        listing = os.listdir(fd)
        known = {n.split(".json", 1)[0] for n in listing if ".json" in n and not n.startswith(".")}
        names = sorted(n for n in listing if n.endswith(".json"))
        if len(names) > _MAX_RECORDS:
            say(f"  {len(names)} model worker records; reading the first {_MAX_RECORDS}.")
        for entry in names[:_MAX_RECORDS]:
            record = read_json_nofollow(Path(entry), owner=uid, dir_fd=fd)
            if record is None:
                say(f"  Skipped the model worker record {repr(entry)[:80]}: not a readable record of {SERVICE_USER}.")
                continue
            out.append((entry[:-5], record))
    except OSError as exc:
        say(f"  Could not read the model worker records: {exc}")
    finally:
        for opened in fds:
            os.close(opened)
    return out, known


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
    stop(runner, layout, say=say)
    number = read_marker(layout).get("id")
    uid = _read_attr(runner, "Users", SERVICE_USER, "UniqueID")
    gid = _read_attr(runner, "Groups", SERVICE_USER, "PrimaryGroupID")
    # Each record goes when its own number is the marker's: a run that deleted
    # the user and then failed leaves a group the next run still removes.
    user_ours = bool(number) and uid == number
    group_ours = bool(number) and gid == number
    ours = user_ours or group_ours
    if user_ours:
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
        say(f"  Deleted the account {SERVICE_USER} ({number}).")
    if group_ours:
        _must(runner, [DSCL, ".", "-delete", f"/Groups/{SERVICE_USER}"])
        say(f"  Deleted the group {SERVICE_USER} ({number}).")
    for kind, found, mine in (("account", uid, user_ours), ("group", gid, group_ours)):
        if found is not None and not mine:
            why = (
                "Fadenstack did not create it (no marker)" if not number
                else f"its number {found} is not the {number} Fadenstack created"
            )
            stopping = " and not stopping its processes" if kind == "account" else ""
            say(f"  Not deleting the {kind} {SERVICE_USER}{stopping}: {why}.")

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


def _limit_text(value: int, infinity: int) -> str:
    return "unlimited" if value == infinity else str(value)


def raise_open_file_limit(target: int = 65536) -> tuple[int, str]:
    """launchd gives a job soft 256 / hard unlimited; raise the soft limit here, not in the plist.

    Returns the log level and the line to log once logging is set up, because
    ``launchctl procinfo`` does not show another process's limits: the agent's
    own log is where an admin sees what it runs with.
    """
    try:
        import resource  # noqa: PLC0415 - POSIX only
    except ImportError as exc:
        return logging.WARNING, f"Open-file limit not raised: {exc}"
    inf = resource.RLIM_INFINITY
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    except (ValueError, OSError) as exc:
        return logging.WARNING, f"Open-file limit not raised: could not read it ({exc})"
    want = target if hard == inf else min(target, hard)
    if soft >= want:
        return logging.INFO, f"Open-file limit: soft {_limit_text(soft, inf)}, hard {_limit_text(hard, inf)}"
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (want, hard))
    except (ValueError, OSError) as exc:
        return logging.WARNING, (
            f"Open-file limit not raised: soft {_limit_text(soft, inf)} to {want} failed ({exc}); "
            f"hard {_limit_text(hard, inf)}"
        )
    return logging.INFO, (
        f"Open-file limit: soft {want} (raised from {_limit_text(soft, inf)}), hard {_limit_text(hard, inf)}"
    )
