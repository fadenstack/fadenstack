"""The content check's rules on this side of the engine: who may check how often, how much, and what.

Signed-in people check text, pictures and files (``/api/provenance/verify``), 30 checks a minute each.
People without an account check pictures and files on the public page when an administrator offers it
(``provenance.public_check_enabled``), 10 checks a minute per address; texts are for people with an
account, and the page names whom to ask (``provenance.public_check_contact``).

What is submitted goes to the engine and back and is kept nowhere: the body is read here, checked for its
size and shape, and never logged -- an error names the field, never what was in it. The counts behind the
limits stay in this process's memory for a minute (a worker counts its own requests).
"""

from __future__ import annotations

import ipaddress
import json
import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.models.system_settings import SystemSettingValue
from faden_backend.services.provenance.client import MAX_CONTENT_BYTES

#: The system settings of the public page.
PUBLIC_CHECK_ENABLED_KEY = "provenance.public_check_enabled"
PUBLIC_CHECK_CONTACT_KEY = "provenance.public_check_contact"
#: The longest contact the public page shows.
MAX_CONTACT_CHARS = 500

#: The longest base64 the engine reads for a file of :data:`MAX_CONTENT_BYTES` (its own rule).
MAX_FILE_BASE64 = MAX_CONTENT_BYTES * 4 // 3 + 4
#: The longest body read: the largest file in base64, and room for the rest of the JSON.
MAX_BODY_BYTES = MAX_FILE_BASE64 + 64 * 1024


def refuse(status_code: int, code: str, message: str, **extra: str) -> HTTPException:
    """An error answer in the engine's shape: ``{"detail": {"code", "message"}}``."""
    return HTTPException(status_code=status_code, detail={"code": code, "message": message, **extra})


# ── How often ────────────────────────────────────────────────────────────────────────


class RateLimiter:
    """At most *limit* hits per *window_sec* for each key, counted in this process.

    A key's hits are forgotten a window after the last one; at most *max_keys* keys are held, the oldest
    dropped first when more come.
    """

    def __init__(
        self,
        limit: int,
        window_sec: float = 60.0,
        *,
        max_keys: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = limit
        self.window_sec = window_sec
        self._max_keys = max_keys
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}

    def hit(self, key: str) -> float:
        """Count a hit for *key*: ``0`` when it is allowed, else the seconds until the next one is."""
        now = self._clock()
        hits = self._hits.get(key)
        if hits is None:
            if len(self._hits) >= self._max_keys:
                self._forget(now)
            hits = self._hits[key] = deque()
        while hits and hits[0] <= now - self.window_sec:
            hits.popleft()
        if len(hits) >= self.limit:
            return max(hits[0] + self.window_sec - now, 0.001)
        hits.append(now)
        return 0.0

    def enforce(self, key: str) -> None:
        """:meth:`hit`, refusing with 429 (and ``Retry-After``) when the limit is reached."""
        wait = self.hit(key)
        if wait:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"code": "rate_limited", "message": "Too many checks in a minute; try again shortly."},
                headers={"Retry-After": str(math.ceil(wait))},
            )

    def reset(self) -> None:
        """Forget every count."""
        self._hits.clear()

    def __len__(self) -> int:
        return len(self._hits)

    def __contains__(self, key: object) -> bool:
        return key in self._hits

    def _forget(self, now: float) -> None:
        for key in [k for k, hits in self._hits.items() if not hits or hits[-1] <= now - self.window_sec]:
            del self._hits[key]
        while len(self._hits) >= self._max_keys:
            del self._hits[next(iter(self._hits))]


#: Checks per signed-in person, per address on the public page, and the public anchors per address.
USER_CHECKS = RateLimiter(30)
PUBLIC_CHECKS = RateLimiter(10)
PUBLIC_ANCHORS = RateLimiter(60)


def client_key(request: Request) -> str:
    """Whom a public request counts against: its address (behind nginx, the client's), IPv6 by its /64."""
    host = request.client.host if request.client else ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host or "unknown"
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return str(address.ipv4_mapped)
        return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return str(address)


# ── What and how much ──────────────────────────────────────────────────────────────


class CheckBody(BaseModel):
    """What to check: a text or a file (base64), and whether the result comes back signed."""

    model_config = ConfigDict(extra="forbid")

    text: str | None = None
    #: A file's bytes, base64.
    file: str | None = None
    media_type: str | None = Field(default=None, max_length=200)
    #: Return the result signed, to download (CoP 2.1.2).
    signed: bool = False

    @property
    def is_text(self) -> bool:
        """Whether a text is to be checked: pasted, or a file the engine reads as text."""
        return self.text is not None or (self.media_type or "").strip().lower().startswith("text/")

    def engine_body(self) -> dict[str, Any]:
        """The engine's ``VerifyRequest``."""
        return {
            "text": self.text,
            "file": self.file,
            "media_type": (self.media_type or "").strip().lower() or None,
            "signed": self.signed,
        }


#: The body as the API documents it: the routes read it themselves, to cap its size before parsing.
CHECK_BODY_DOC: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": CheckBody.model_json_schema()}},
    },
}


def _too_large() -> HTTPException:
    return refuse(
        status.HTTP_413_CONTENT_TOO_LARGE,
        "too_large",
        f"Content over {MAX_CONTENT_BYTES // (1024 * 1024)} MB is not checked.",
    )


async def _read_capped(request: Request) -> bytes:
    """The request's body, refused with 413 as soon as it is longer than :data:`MAX_BODY_BYTES`."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise _too_large()
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise _too_large()
        chunks.append(chunk)
    return b"".join(chunks)


def _invalid(message: str) -> HTTPException:
    return refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid", message)


async def read_check_body(request: Request) -> CheckBody:
    """The body of a check, refused when too large (413) or not a check (422).

    The refusals name the field and the problem, never the value: what was sent is not repeated, logged or
    kept.
    """
    raw = await _read_capped(request)
    try:
        data = json.loads(raw)
    except ValueError:
        raise _invalid("The body is not JSON.") from None
    try:
        body = CheckBody.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'body'}: {error['msg']}"
            for error in exc.errors(include_input=False, include_url=False)
        )
        raise _invalid(problems or "The body is not a check.") from None
    if (body.text is None) == (body.file is None):
        raise refuse(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "nothing-to-check", "Send either a text or a file.",
        )
    if body.text is not None:
        try:
            size = len(body.text.encode("utf-8"))
        except UnicodeEncodeError:  # a lone surrogate (``"\ud800"`` in the JSON) is no text
            raise _invalid("text: not valid Unicode") from None
        if size > MAX_CONTENT_BYTES:
            raise _too_large()
    if body.file is not None and len(body.file) > MAX_FILE_BASE64:
        raise _too_large()
    return body


# ── The public page ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PublicCheck:
    """Whether the public page is offered, and whom it names for text checks."""

    enabled: bool
    contact: str


async def public_check_settings(session: AsyncSession) -> PublicCheck:
    """The public page's settings as stored now, the registry's defaults where none is stored.

    Read from the database, not this process's copy of the settings: another worker may have changed them.
    """
    rows = await session.execute(
        select(SystemSettingValue.key, SystemSettingValue.value_json).where(
            SystemSettingValue.key.in_((PUBLIC_CHECK_ENABLED_KEY, PUBLIC_CHECK_CONTACT_KEY)),
        ),
    )
    values: dict[str, Any] = {}
    for key, value_json in rows.all():
        values[str(key)] = value_json.get("value") if isinstance(value_json, dict) else value_json
    contact = values.get(PUBLIC_CHECK_CONTACT_KEY)
    return PublicCheck(
        enabled=values.get(PUBLIC_CHECK_ENABLED_KEY) is True,
        contact=contact.strip()[:MAX_CONTACT_CHARS] if isinstance(contact, str) else "",
    )
