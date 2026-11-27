"""Which processes are a worker's, and ending all of them.

A worker is found by what was recorded when it started (pid, the process's
creation time, a hash of its arguments), never by user: the per-user
``distnoted``/``cfprefsd`` agents run as the service account too, and are
not ours. The agent (as ``_fadenagent``) and ``faden-agent stop|uninstall``
(as root, reading the record only) both end workers through this module.

Only verified processes are signalled, one by one -- no ``killpg`` on a
number read from a file:

- the leader, when it is still the recorded process (``matching``);
- while that leader is alive: its descendants and the members of its own
  process group (the group is its own: ``start_new_session``);
- the descendants recorded while the worker ran (``family``: pid and
  creation time, refreshed by the agent), each only while it is still that
  same process -- so a helper that called ``setsid`` and outlived the
  leader is still found;
- processes carrying the worker's environment marker
  (``FADEN_NATIVE_WORKER=<deployment>:<generation>``, inherited by every
  descendant), of the same user and created no earlier than the worker --
  which finds a helper that detached between two watcher passes;
- and the descendants of any of these.

Never the agent itself nor anything in the agent's own process group.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import signal
import time
from collections.abc import Callable, Iterable
from typing import Any

import psutil

#: create_time is stored as a float; the leader matches within this many seconds.
CREATE_TOLERANCE_S = 1.0
#: A recorded descendant matches its creation time this closely (same clock source).
FAMILY_TOLERANCE_S = 0.05
MAX_FAMILY = 512
_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_MARKER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}:[A-Za-z0-9._-]{1,128}$")
MARKER_ENV = "FADEN_NATIVE_WORKER"


class BadRecord(ValueError):
    """A worker record with a field of the wrong type or range."""


def cmd_hash(argv: Iterable[str]) -> str:
    """Hash of the arguments from ``serve`` on.

    The worker is ``[sandbox-exec -p …] /bin/sh -c <shim> … vllm serve …``,
    which execs on until the venv's Python: the program changes, the
    arguments from ``serve`` on do not.
    """
    args = list(argv)
    try:
        tail = args[args.index("serve"):]
    except ValueError:
        tail = args
    return "sha256:" + hashlib.sha256("\0".join(tail).encode("utf-8", "surrogateescape")).hexdigest()


def _int(value: Any, what: str, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise BadRecord(f"{what} is not an integer in [{lo}, {hi}]: {str(value)[:40]!r}")
    return value


def _float(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise BadRecord(f"{what} is not a time: {str(value)[:40]!r}")
    return float(value)


def validate(record: Any) -> dict[str, Any]:
    """The fields this module acts on, checked; raises :class:`BadRecord`.

    Root runs this on files the service account can write: nothing in them
    may raise anything else or reach a signal unchecked.
    """
    if not isinstance(record, dict):
        raise BadRecord("not an object")
    out: dict[str, Any] = {
        "pid": _int(record.get("pid"), "pid", 2, 1 << 31),
        "started_at": _float(record.get("started_at"), "started_at"),
    }
    pgid = record.get("pgid", out["pid"])
    out["pgid"] = _int(pgid, "pgid", 2, 1 << 31)
    if out["pgid"] != out["pid"]:
        raise BadRecord("pgid is not the leader's own group")
    digest = record.get("cmd_sha256")
    if not isinstance(digest, str) or not _HASH.fullmatch(digest):
        raise BadRecord("cmd_sha256 is not a sha256 digest")
    out["cmd_sha256"] = digest
    root = record.get("runtime_root")
    if root is not None and (not isinstance(root, str) or not root.startswith("/") or len(root) > 4096):
        raise BadRecord("runtime_root is not an absolute path")
    out["runtime_root"] = root
    port = record.get("port")
    out["port"] = None if port is None else _int(port, "port", 1, 65535)
    family = record.get("family") or []
    if not isinstance(family, list) or len(family) > MAX_FAMILY:
        raise BadRecord("family is not a short list")
    mark = record.get("marker")
    if mark is not None and (not isinstance(mark, str) or not _MARKER.fullmatch(mark)):
        raise BadRecord("marker is not <deployment>:<generation>")
    out["marker"] = mark
    out["family"] = [
        {"pid": _int(e.get("pid") if isinstance(e, dict) else None, "family pid", 2, 1 << 31),
         "ct": _float(e.get("ct") if isinstance(e, dict) else None, "family ct")}
        for e in family
    ]
    return out


def _proc(pid: int) -> psutil.Process | None:
    try:
        proc = psutil.Process(int(pid))
        if proc.status() == psutil.STATUS_ZOMBIE:
            return None
        return proc
    except (psutil.Error, ValueError, TypeError):
        return None


def alive(proc: psutil.Process) -> bool:
    try:
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def _runs_from(proc: psutil.Process, runtime_root: Any) -> bool:
    """Its program lives in the recorded runtime (vLLM may retitle itself, changing its cmdline)."""
    if not runtime_root:
        return False
    try:
        exe = os.path.realpath(proc.exe())
        root = os.path.realpath(str(runtime_root))
    except (psutil.Error, OSError):
        return False
    return exe.startswith(root.rstrip("/") + "/")


def matching(record: dict[str, Any], *, uid: int | None = None) -> psutil.Process | None:
    """The recorded worker's leader when it is still that same process, else ``None``.

    Same pid and creation time, and either the same arguments from ``serve``
    on, or a program from the recorded runtime still serving on the recorded
    port (a retitled vLLM).
    """
    try:
        rec = validate(record)
    except BadRecord:
        return None
    proc = _proc(rec["pid"])
    if proc is None:
        return None
    try:
        if abs(proc.create_time() - rec["started_at"]) > CREATE_TOLERANCE_S:
            return None
        if uid is not None and proc.uids().real != uid:
            return None
        cmdline = proc.cmdline()
        if cmd_hash(cmdline) == rec["cmd_sha256"]:
            return proc
        if (
            _runs_from(proc, rec["runtime_root"])
            and "serve" in cmdline
            and rec["port"] is not None
            and str(rec["port"]) in cmdline
        ):
            return proc
    except (psutil.Error, ValueError, TypeError):
        return None
    return None


def family_of(leader: psutil.Process) -> list[dict[str, Any]]:
    """The leader's descendants now, as ``{pid, ct}`` for the record."""
    out = []
    try:
        kids = leader.children(recursive=True)
    except psutil.Error:
        return out
    for kid in kids:
        try:
            out.append({"pid": kid.pid, "ct": kid.create_time()})
        except psutil.Error:
            continue
    return trim_family(out)


def trim_family(entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per pid (the newest), at most :data:`MAX_FAMILY`, keeping the newest processes."""
    by_pid: dict[int, dict[str, Any]] = {}
    for entry in entries:
        old = by_pid.get(entry["pid"])
        if old is None or entry["ct"] >= old["ct"]:
            by_pid[entry["pid"]] = entry
    return sorted(by_pid.values(), key=lambda e: e["ct"])[-MAX_FAMILY:]


def marked(
    *, uid: int, marker: str | None = None, prefix: str | None = None, any_marker: bool = False,
    deployments: set[str] | frozenset[str] | None = None, not_before: float | None = None,
) -> list[psutil.Process]:
    """Processes of *uid* whose environment carries the worker marker.

    The environment a process started with is readable for the same user (and
    root) -- not for Apple's platform binaries, which a worker's Python is not.
    """
    me, own_group = os.getpid(), os.getpgrp()
    out = []
    for proc in psutil.process_iter():
        try:
            if proc.pid in (me, 0, 1) or _pgid(proc.pid) == own_group or proc.uids().real != uid:
                continue
            value = proc.environ().get(MARKER_ENV)
            if value is None:
                continue
            if not (
                any_marker
                or value == marker
                or (prefix is not None and value.startswith(prefix))
                or (deployments is not None and value.split(":", 1)[0] in deployments)
            ):
                continue
            if not_before is not None and proc.create_time() + CREATE_TOLERANCE_S < not_before:
                continue
        except (psutil.Error, OSError):
            continue
        if alive(proc):
            out.append(proc)
    return out


def _pgid(pid: int) -> int | None:
    try:
        return os.getpgid(pid)
    except OSError:
        return None


def collect(record: dict[str, Any], leader: psutil.Process | None, *, uid: int | None = None) -> list[psutil.Process]:
    """Every verified process of the worker (see the module docstring)."""
    rec = validate(record)
    me, own_group = os.getpid(), os.getpgrp()
    found: dict[int, psutil.Process] = {}

    def add(proc: psutil.Process, *, with_children: bool = True) -> None:
        if proc.pid in found or proc.pid == me or proc.pid <= 1:
            return
        if _pgid(proc.pid) == own_group:
            return  # never the agent's own group
        try:
            if uid is not None and proc.uids().real != uid:
                return
            kids = proc.children(recursive=True) if with_children else []
        except psutil.Error:
            return
        if not alive(proc):
            return
        found[proc.pid] = proc
        for kid in kids:
            add(kid, with_children=False)

    if leader is not None and alive(leader):
        assert leader.pid != me and _pgid(leader.pid) != own_group, "a worker must not be the agent's own group"
        add(leader)
        if _pgid(leader.pid) == leader.pid:
            for proc in psutil.process_iter():
                if proc.pid not in found and _pgid(proc.pid) == leader.pid:
                    add(proc)
    if rec["marker"]:
        for proc in marked(uid=os.getuid() if uid is None else uid, marker=rec["marker"],
                           not_before=rec["started_at"]):
            add(proc)
    for entry in rec["family"]:
        proc = _proc(entry["pid"])
        if proc is None:
            continue
        try:
            if abs(proc.create_time() - entry["ct"]) > FAMILY_TOLERANCE_S:
                continue  # the pid now names another process
        except psutil.Error:
            continue
        add(proc)
    return list(found.values())


def stop_worker(
    record: dict[str, Any],
    leader: psutil.Process | None,
    *,
    grace_s: float,
    uid: int | None = None,
    reap: Callable[[], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[int]:
    """SIGTERM, wait *grace_s*, SIGKILL; returns the pids still alive afterwards (``[]`` = gone)."""
    return stop_processes(collect(record, leader, uid=uid), grace_s=grace_s, uid=uid, reap=reap, sleep=sleep)


def stop_processes(
    procs: list[psutil.Process],
    *,
    grace_s: float,
    uid: int | None = None,
    reap: Callable[[], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[int]:
    """End verified *procs* and the children they have meanwhile; never the agent or its group."""
    procs = list(procs)
    me, own_group = os.getpid(), os.getpgrp()

    def signal_all(sig: int) -> None:
        for proc in procs:
            if proc.pid == me or _pgid(proc.pid) == own_group:
                continue
            try:
                proc.send_signal(sig)  # psutil checks the pid is still this process
            except psutil.Error:
                pass

    def wait(seconds: float) -> None:
        deadline = time.monotonic() + max(seconds, 0.0)
        while True:
            if reap is not None:
                reap()
            survivors = [p for p in procs if alive(p)]
            known = {p.pid for p in survivors}
            for proc in list(survivors):
                try:  # children born meanwhile are the worker's too
                    for kid in proc.children(recursive=True):
                        if kid.pid not in known and kid.pid != me and alive(kid) and (
                            uid is None or kid.uids().real == uid
                        ):
                            survivors.append(kid)
                            known.add(kid.pid)
                except psutil.Error:
                    pass
            procs[:] = survivors
            if not procs or time.monotonic() >= deadline:
                return
            sleep(0.1)

    if not procs:
        return []
    signal_all(signal.SIGTERM)
    wait(grace_s)
    if procs:
        signal_all(signal.SIGKILL)
        wait(5.0)
    return [p.pid for p in procs]
