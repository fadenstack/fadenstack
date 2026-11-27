"""Which processes are a worker's, and ending all of them.

A worker is found by what was recorded when it started (pid, the process's
creation time, a hash of its arguments), never by user: the per-user
``distnoted``/``cfprefsd`` agents run as the service account too, and are
not ours. The agent (as ``_fadenagent``) and ``faden-agent stop|uninstall``
(as root, reading the record only) both end workers through this module.
"""

from __future__ import annotations

import hashlib
import os
import signal
import time
from collections.abc import Callable, Iterable
from typing import Any

import psutil

#: create_time is stored as a float; a worker matches within this many seconds.
CREATE_TOLERANCE_S = 1.0


def cmd_hash(argv: Iterable[str]) -> str:
    """Hash of the arguments from ``serve`` on.

    The worker is ``/bin/sh -c <shim> … vllm serve …``, which execs the vllm
    script, which execs the venv's Python: the program changes, the
    arguments from ``serve`` on do not.
    """
    args = list(argv)
    try:
        tail = args[args.index("serve"):]
    except ValueError:
        tail = args
    return "sha256:" + hashlib.sha256("\0".join(tail).encode("utf-8", "surrogateescape")).hexdigest()


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


def matching(record: dict[str, Any], *, uid: int | None = None) -> psutil.Process | None:
    """The recorded worker's leader when it is still that same process, else ``None``."""
    proc = _proc(record.get("pid") or 0)
    if proc is None:
        return None
    try:
        if abs(proc.create_time() - float(record.get("started_at") or 0)) > CREATE_TOLERANCE_S:
            return None
        if uid is not None and proc.uids().real != uid:
            return None
        if cmd_hash(proc.cmdline()) != record.get("cmd_sha256") and not _runs_from(proc, record.get("runtime_root")):
            return None
    except (psutil.Error, ValueError, TypeError):
        return None
    return proc


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


def _pgid(pid: int) -> int | None:
    try:
        return os.getpgid(pid)
    except OSError:
        return None


def tree(pid: int | None, pgid: int | None, *, not_before: float = 0.0, uid: int | None = None) -> list[psutil.Process]:
    """The leader, its descendants, everything in its process group, and their descendants.

    Group members must be no older than *not_before* (a reused group id is
    not ours) and, when *uid* is given, run as that user.
    """
    found: dict[int, psutil.Process] = {}

    def add(proc: psutil.Process) -> None:
        if proc.pid in found or proc.pid == os.getpid():
            return
        try:
            if uid is not None and proc.uids().real != uid:
                return
            kids = proc.children(recursive=True)
        except psutil.Error:
            return
        found[proc.pid] = proc
        for kid in kids:
            try:
                if uid is not None and kid.uids().real != uid:
                    continue
            except psutil.Error:
                continue
            found.setdefault(kid.pid, kid)

    if pid:
        leader = _proc(pid)
        if leader is not None:
            add(leader)
    if pgid and pgid > 1:
        for proc in psutil.process_iter():
            if proc.pid in found or _pgid(proc.pid) != pgid:
                continue
            try:
                if proc.create_time() + CREATE_TOLERANCE_S < not_before:
                    continue
            except psutil.Error:
                continue
            add(proc)
    return [p for p in found.values() if alive(p)]


def _signal(procs: Iterable[psutil.Process], sig: int) -> None:
    for proc in procs:
        try:
            proc.send_signal(sig)  # psutil checks the pid is still this process
        except psutil.Error:
            pass


def stop_tree(
    pid: int | None,
    pgid: int | None,
    *,
    grace_s: float,
    not_before: float = 0.0,
    uid: int | None = None,
    use_killpg: bool = True,
    reap: Callable[[], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[int]:
    """SIGTERM, wait *grace_s*, SIGKILL; returns the pids still alive afterwards (``[]`` = gone).

    ``use_killpg`` is for the agent, which owns the group. Root (``uid`` set)
    signals the verified processes one by one instead: a group id read from a
    file the service can write is not trusted to name only its processes.
    """
    procs = tree(pid, pgid, not_before=not_before, uid=uid)

    def kill(sig: int) -> None:
        if use_killpg and pgid and pgid > 1:
            try:
                os.killpg(pgid, sig)
            except OSError:
                pass
        _signal(procs, sig)

    def wait(seconds: float) -> None:
        deadline = time.monotonic() + max(seconds, 0.0)
        while True:
            if reap is not None:
                reap()
            procs[:] = [p for p in procs if alive(p)]
            for extra in tree(pid, pgid, not_before=not_before, uid=uid):
                if extra.pid not in {p.pid for p in procs}:
                    procs.append(extra)
            if not procs or time.monotonic() >= deadline:
                return
            sleep(0.1)

    if not procs:
        return []
    kill(signal.SIGTERM)
    wait(grace_s)
    if procs:
        kill(signal.SIGKILL)
        wait(5.0)
    return [p.pid for p in procs]
