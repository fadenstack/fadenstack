"""Who may do what, as of this request.

ASVS 5.0 V8.3.2: changes to what authorization decisions rest on apply at once; V7.4.1: a sign-in that was
ended is not used again. The gateway keeps answers about access in caches of its own, per worker: a person's
roles and teams, agents and their grants, API tokens, logouts, Enterprise's exclusions and limits. The backend
database counts every change to the tables these come from (``access_epoch``, moved by triggers in the change's
own transaction; the backend's ``db/access_epoch.py``).

Each request reads the count once (:func:`note`, one primary-key read), before anything else about access. A
cached answer is used only when it was read under the same count, that is, when its read began after the count
was last moved: the request then sees every change made before it, in every worker. A request that read no
count (no backend database: development, tests) takes the caches as they are.
"""

from __future__ import annotations

from contextvars import ContextVar

_EPOCH: ContextVar[int | None] = ContextVar("faden_access_epoch", default=None)

#: The query, against the backend database.
SQL = "SELECT epoch FROM access_epoch WHERE id = 1"


def note(epoch: int) -> None:
    """The count this request read."""
    _EPOCH.set(epoch)


def current() -> int | None:
    """The count this request read; ``None``: it read none."""
    return _EPOCH.get()


def usable(read_under: int | None) -> bool:
    """Whether an answer read under the count *read_under* may answer this request."""
    now = _EPOCH.get()
    return now is None or read_under == now
