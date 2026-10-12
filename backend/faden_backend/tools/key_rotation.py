"""Re-seal stored values with a new key: the database half of ``faden secrets rotate``.

``faden secrets rotate`` writes the new key to ``.env`` next to the old one (the *previous* key), restarts
the services, and runs a job from each service's image. The job opens every sealed value with either key
and seals it again with the current one, then checks that each opens with the current key alone. It does
all of that in one transaction per database: a failure rolls back, every value stays openable with the two
keys the services hold, and running the job again finishes the work. Only after every job has succeeded
does the CLI drop the previous key.

The same file, with only the targets differing, is in the gateway and MCP packages
(``<package>/tools/key_rotation.py``): the images share no code, and a rotation job must not need the
application to start.

Nothing here logs or returns a value, only counts.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import column, select, table, tuple_, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

#: Every Fernet token starts with this (version byte 0x80 in base64).
FERNET_PREFIX = "gAAAAA"
#: Rows read, locked and rewritten at a time.
BATCH = 500


class RotationError(RuntimeError):
    """The transaction was rolled back; the message says what to look at (never a value)."""


@dataclass(frozen=True)
class Scheme:
    """How one kind of value is sealed: the Fernet each of the two keys derives for it."""

    current: Fernet
    previous: Fernet | None = None

    @property
    def either(self) -> MultiFernet:
        """Opens with either key; seals with the current one (``rotate`` re-seals with it)."""
        return MultiFernet([self.current, *([self.previous] if self.previous else [])])


@dataclass(frozen=True)
class Target:
    """One column that holds sealed values, and how."""

    table: str
    primary_key: tuple[str, ...]
    column: str
    scheme: Scheme
    #: A value that is not a Fernet token is left as it is: the column may hold plain text from before it
    #: was sealed (the table is also written by readers that accept it).
    plaintext_ok: bool = False
    #: Values neither key opens are left alone and reported, instead of stopping the rotation: for a column
    #: whose sealing this code does not own.
    optional: bool = False

    @property
    def label(self) -> str:
        """``table.column``: how reports and errors name it."""
        return f"{self.table}.{self.column}"


@dataclass
class Counts:
    """What happened to one column's values."""

    rotated: int = 0
    already_current: int = 0
    plaintext: int = 0
    unreadable: int = 0
    #: Values the verification pass could not open with the current key alone.
    unverified: int = 0

    def as_dict(self) -> dict[str, int]:
        """The counts, for the job's JSON answer."""
        return {
            "rotated": self.rotated,
            "already_current": self.already_current,
            "plaintext": self.plaintext,
            "unreadable": self.unreadable,
        }


@dataclass
class Report:
    """The outcome of a job: counts per column."""

    columns: dict[str, Counts] = field(default_factory=dict)

    def as_dict(self) -> dict[str, dict[str, int]]:
        """Every column's counts, for the job's JSON answer."""
        return {label: counts.as_dict() for label, counts in self.columns.items()}


def _is_token(value: str) -> bool:
    return value.startswith(FERNET_PREFIX)


def _opens(fernet: Fernet, value: str) -> bool:
    try:
        fernet.decrypt(value.encode("ascii"))
    except (InvalidToken, UnicodeEncodeError):
        return False
    return True


def reseal(value: str, scheme: Scheme) -> tuple[str, str | None]:
    """What to do with one stored *value*: ``(outcome, new value)``.

    The outcome is ``current`` (already sealed with the current key), ``rotated`` (the new value is
    given), ``plaintext`` (not a token) or ``unreadable`` (neither key opens it). A new value is
    checked to hold the same plaintext before it is returned.
    """
    if not _is_token(value):
        return "plaintext", None
    if _opens(scheme.current, value):
        return "current", None
    if scheme.previous is None or not _opens(scheme.previous, value):
        return "unreadable", None
    either = scheme.either
    new = either.rotate(value.encode("ascii")).decode("ascii")
    if scheme.current.decrypt(new.encode("ascii")) != either.decrypt(value.encode("ascii")):
        raise RotationError("A value did not keep its content when it was sealed again; nothing was changed.")
    return "rotated", new


def _tally(counts: Counts, outcome: str) -> None:
    if outcome == "rotated":
        counts.rotated += 1
    elif outcome == "current":
        counts.already_current += 1
    elif outcome == "plaintext":
        counts.plaintext += 1
    else:
        counts.unreadable += 1


async def _batches(
    conn: AsyncConnection, target: Target, *, lock: bool,
) -> AsyncIterator[tuple[Any, list, list]]:
    """The column's rows, ``BATCH`` at a time in key order: lists of ``(key values, value)``."""
    t = table(target.table, *(column(name) for name in (*target.primary_key, target.column)))
    keys = [t.c[name] for name in target.primary_key]
    value = t.c[target.column]
    last: tuple | None = None
    while True:
        query = select(*keys, value).where(value.is_not(None)).order_by(*keys).limit(BATCH)
        if last is not None:
            query = query.where(keys[0] > last[0] if len(keys) == 1 else tuple_(*keys) > tuple_(*last))
        if lock:
            # Rows a running service writes meanwhile wait for the commit instead of being overwritten.
            query = query.with_for_update()
        rows = (await conn.execute(query)).all()
        if not rows:
            return
        width = len(keys)
        yield t, keys, [(tuple(row[:width]), row[width]) for row in rows]
        last = tuple(rows[-1][:width])


async def _rotate_target(
    conn: AsyncConnection, target: Target, counts: Counts, progress: Callable[[str], None],
) -> None:
    async for t, keys, rows in _batches(conn, target, lock=True):
        for key, stored in rows:
            outcome, new = reseal(stored, target.scheme)
            if outcome == "plaintext" and not target.plaintext_ok:
                outcome = "unreadable"  # this column only ever holds tokens
            _tally(counts, outcome)
            if new is not None:
                where = [column_ == part for column_, part in zip(keys, key, strict=True)]
                await conn.execute(update(t).where(*where).values({target.column: new}))
        seen = counts.rotated + counts.already_current + counts.plaintext + counts.unreadable
        progress(f"{target.label}: {seen} values looked at")


async def _verify_target(conn: AsyncConnection, target: Target, counts: Counts) -> None:
    """Every sealed value opens with the current key alone (the unreadable ones were counted before)."""
    async for _t, _keys, rows in _batches(conn, target, lock=False):
        for _key, stored in rows:
            if _is_token(stored) and not _opens(target.scheme.current, stored):
                counts.unverified += 1


async def rotate(
    engine: AsyncEngine,
    targets: Sequence[Target],
    *,
    skip_unreadable: bool = False,
    progress: Callable[[str], None] | None = None,
) -> Report:
    """Re-seal every target's values in one transaction, verify them, commit; raises ``RotationError``.

    Values neither key opens stop the rotation unless *skip_unreadable* is set (or the target is
    optional): the previous key must not be dropped while something might still need it, and a value
    nobody can open is a problem the operator should see once rather than carry for ever.
    """
    say = progress or (lambda line: print(line, file=sys.stderr, flush=True))  # noqa: T201
    report = Report()
    async with engine.begin() as conn:
        for target in targets:
            counts = report.columns.setdefault(target.label, Counts())
            await _rotate_target(conn, target, counts, say)
        for target in targets:
            counts = report.columns[target.label]
            await _verify_target(conn, target, counts)
        problems = _problems(targets, report, skip_unreadable=skip_unreadable)
        if problems:
            # Leaving the block with an exception rolls the transaction back.
            raise RotationError("; ".join(problems))
    return report


def _problems(targets: Sequence[Target], report: Report, *, skip_unreadable: bool) -> list[str]:
    problems: list[str] = []
    for target in targets:
        counts = report.columns[target.label]
        # A value the verification cannot open with the current key alone, other than the ones already
        # counted as unreadable, was not sealed properly: never accept it.
        if counts.unverified > counts.unreadable:
            missing = counts.unverified - counts.unreadable
            problems.append(f"{target.label}: {missing} value(s) do not open with the new key")
        if counts.unreadable and not (skip_unreadable or target.optional):
            problems.append(
                f"{target.label}: {counts.unreadable} value(s) open with neither the new nor the previous key"
            )
    if any("neither" in problem for problem in problems):
        problems.append("run again with --skip-unreadable to leave those values as they are")
    return problems
