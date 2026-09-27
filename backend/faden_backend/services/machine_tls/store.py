"""The machine CA's life in the database: made, used, rotated, retired -- by itself.

The backend makes the CA the first time it is needed, and ``maintain`` (run
hourly, and at startup) keeps everything current without anyone's help:

- a year before the CA ends, the next one is made and trusted everywhere two
  days before it signs anything;
- the one before it is retired once everything it signed has expired;
- the gateway's client certificate is renewed ten days before it ends, and at
  once when another CA has taken over;
- a CA whose key this install cannot open (a database restored without its
  ``.env``) is replaced, and the machines fetch new certificates within a
  minute, over their credential-authenticated connections -- which do not
  depend on this CA, so nothing has to be done on the machines.

``rotate`` is the administrator's lever for a suspected leak: a new CA at
once, every other one distrusted at once.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography import x509
from cryptography.fernet import InvalidToken
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.machine_tls import MachineCA, MachineCertificate, MachineTlsClient
from faden_backend.services.machine_tls import authority as auth

log = logging.getLogger(__name__)

_LOCK = "hashtext('faden.machine_ca')"
#: A machine asking again this soon for the same certificate is refused.
REISSUE_PACE = timedelta(seconds=60)


class TooSoonError(Exception):
    """A machine asked for the same certificate again within ``REISSUE_PACE``."""


async def _lock(session: AsyncSession) -> None:
    """Serialise CA changes across backend processes for this transaction."""
    bind = session.get_bind()
    if getattr(getattr(bind, "dialect", None), "name", "") == "postgresql":
        await session.execute(text(f"SELECT pg_advisory_xact_lock({_LOCK})"))


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def _active(session: AsyncSession, now: datetime) -> list[MachineCA]:
    query = (
        select(MachineCA).where(MachineCA.retired_at.is_(None)).order_by(MachineCA.created_at, MachineCA.id)
    )
    rows = (await session.execute(query)).scalars().all()
    return [r for r in rows if _aware(r.not_after) > now]


def _issuer_of(rows: list[MachineCA], now: datetime) -> MachineCA | None:
    ready = [r for r in rows if _aware(r.issuing_from) <= now]
    return ready[-1] if ready else None


def _load(row: MachineCA, master_key: str) -> tuple[x509.Certificate, Any]:
    cert = x509.load_pem_x509_certificate(row.cert_pem.encode("ascii"))
    key = auth.load_ca_key(auth.unseal(row.key_sealed, master_key, auth.KEY_PURPOSE_CA))
    return cert, key


def _readable(row: MachineCA, master_key: str) -> bool:
    try:
        _load(row, master_key)
    except (InvalidToken, ValueError, auth.AuthorityError):
        return False
    return True


async def _make(
    session: AsyncSession, master_key: str, now: datetime, *, issuing_from: datetime, reason: str,
) -> MachineCA:
    made = auth.make_ca(now)
    row = MachineCA(
        id=uuid.uuid4(),
        cert_pem=made.cert_pem,
        key_sealed=auth.seal(made.key_pem, master_key, auth.KEY_PURPOSE_CA),
        fingerprint_sha256=auth.fingerprint(made.cert),
        not_after=made.cert.not_valid_after_utc,
        issuing_from=issuing_from,
        reason=reason,
        created_at=now,
    )
    session.add(row)
    await session.flush()
    log.info(
        "Machine CA %s made (%s), issuing from %s",
        row.fingerprint_sha256[:16], reason, issuing_from.isoformat(),
    )
    return row


async def _ensure_issuer(session: AsyncSession, master_key: str, now: datetime) -> tuple[MachineCA, bool]:
    """The CA that signs now, made when there is none (or none this install can open)."""
    rows = await _active(session, now)
    issuer = _issuer_of(rows, now)
    if issuer is not None and _readable(issuer, master_key):
        return issuer, False
    reason = "first"
    if issuer is not None:
        # Restored without the .env it was sealed with: nothing it signed can
        # be renewed. Its trust ends here; machines re-enrol within a minute.
        log.warning("The machine CA's key cannot be opened with this install's master key; replacing it.")
        reason = "unreadable"
        for row in rows:
            if not _readable(row, master_key):
                row.retired_at = now
    return await _make(session, master_key, now, issuing_from=now, reason=reason), True


def trust_pem(rows: list[MachineCA]) -> str:
    """The rows' certificates as one PEM bundle."""
    return "".join(r.cert_pem if r.cert_pem.endswith("\n") else r.cert_pem + "\n" for r in rows)


async def trust_bundle(session: AsyncSession, now: datetime | None = None) -> str:
    """Every CA the gateway and the machines' proxies should trust: PEM, oldest first."""
    return trust_pem(await _active(session, _now(now)))


@dataclass
class Maintained:
    """What one maintenance pass did."""

    created: bool = False
    scheduled: bool = False
    retired: int = 0
    gateway_renewed: bool = False
    notes: list[str] = field(default_factory=list)


async def _ensure_gateway(session: AsyncSession, master_key: str, issuer: MachineCA, now: datetime) -> bool:
    row = await session.get(MachineTlsClient, auth.GATEWAY_CLIENT)
    fresh = (
        row is not None
        and row.ca_id == issuer.id
        and _aware(row.not_after) - now > auth.RENEW_BEFORE
    )
    if fresh:
        try:
            auth.unseal(row.key_sealed, master_key, auth.KEY_PURPOSE_CLIENT)  # type: ignore[union-attr]
        except InvalidToken:
            fresh = False
    if fresh:
        return False
    ca_cert, ca_key = _load(issuer, master_key)
    made = auth.make_client(auth.GATEWAY_CLIENT, ca_cert=ca_cert, ca_key=ca_key, now=now)
    sealed = auth.seal(made.key_pem, master_key, auth.KEY_PURPOSE_CLIENT)
    if row is None:
        session.add(MachineTlsClient(
            name=auth.GATEWAY_CLIENT, cert_pem=made.cert_pem, key_sealed=sealed,
            not_after=made.cert.not_valid_after_utc, ca_id=issuer.id, updated_at=now,
        ))
    else:
        row.cert_pem = made.cert_pem
        row.key_sealed = sealed
        row.not_after = made.cert.not_valid_after_utc
        row.ca_id = issuer.id
        row.updated_at = now
    await session.flush()
    log.info("Gateway client certificate issued, valid until %s", made.cert.not_valid_after_utc.isoformat())
    return True


async def maintain(session: AsyncSession, master_key: str, now: datetime | None = None) -> Maintained:
    """Bring the CA and the gateway's certificate up to date. Idempotent; the caller commits."""
    now = _now(now)
    await _lock(session)
    out = Maintained()
    issuer, out.created = await _ensure_issuer(session, master_key, now)
    rows = await _active(session, now)

    newer = [r for r in rows if r.created_at and _aware(r.created_at) > _aware(issuer.created_at)]
    if not newer and _aware(issuer.not_after) - now < auth.ROTATE_BEFORE:
        await _make(session, master_key, now, issuing_from=now + auth.PROPAGATE, reason="scheduled")
        out.scheduled = True
        out.notes.append("next machine CA made; it signs from " + (now + auth.PROPAGATE).date().isoformat())

    # Everything an older CA signed has expired a leaf lifetime after its
    # successor started signing (renewals move to the successor meanwhile).
    horizon = now - timedelta(days=auth.LEAF_DAYS + 1)
    if _aware(issuer.issuing_from) <= horizon:
        for row in rows:
            if row.id != issuer.id and _aware(row.created_at) < _aware(issuer.created_at):
                row.retired_at = now
                out.retired += 1

    out.gateway_renewed = await _ensure_gateway(session, master_key, issuer, now)
    await session.flush()
    return out


async def rotate(session: AsyncSession, master_key: str, now: datetime | None = None) -> MachineCA:
    """A new CA at once; every other one distrusted at once. For a suspected leak."""
    now = _now(now)
    await _lock(session)
    for row in await _active(session, now):
        row.retired_at = now
    await session.flush()
    issuer = await _make(session, master_key, now, issuing_from=now, reason="replaced")
    await _ensure_gateway(session, master_key, issuer, now)
    await session.flush()
    return issuer


def machine_names(host: str | None) -> list[str]:
    """What a machine's certificate names: the address the backend reaches it by."""
    value = (host or "").strip()
    if value.startswith("host="):
        value = value[len("host="):].strip()
    return [value] if value else []


@dataclass(frozen=True)
class Issued:
    """A machine's new certificate, and what comes with it."""

    cert_pem: str
    trust_pem: str
    not_after: datetime
    names: list[str]
    issuer_key_id: str


async def issue_for_node(
    session: AsyncSession,
    master_key: str,
    *,
    node_id: uuid.UUID,
    host: str | None,
    csr_pem: str,
    now: datetime | None = None,
) -> Issued:
    """Sign a machine's request for its model-TLS proxy. The caller commits."""
    now = _now(now)
    names = machine_names(host)
    issuer, _ = await _ensure_issuer(session, master_key, now)
    last = await session.get(MachineCertificate, node_id)
    if (
        last is not None
        and last.ca_id == issuer.id
        and list(last.names_json or []) == names
        and now - _aware(last.issued_at) < REISSUE_PACE
    ):
        raise TooSoonError
    ca_cert, ca_key = _load(issuer, master_key)
    cert = auth.sign_machine(csr_pem, names=names, ca_cert=ca_cert, ca_key=ca_key, now=now)
    if last is None:
        session.add(MachineCertificate(
            node_id=node_id, serial=auth.serial_hex(cert), names_json=names,
            not_after=cert.not_valid_after_utc, ca_id=issuer.id, issued_at=now,
        ))
    else:
        last.serial = auth.serial_hex(cert)
        last.names_json = names
        last.not_after = cert.not_valid_after_utc
        last.ca_id = issuer.id
        last.issued_at = now
    await session.flush()
    from cryptography.hazmat.primitives import serialization  # noqa: PLC0415

    return Issued(
        cert_pem=cert.public_bytes(serialization.Encoding.PEM).decode("ascii"),
        trust_pem=await trust_bundle(session, now),
        not_after=cert.not_valid_after_utc,
        names=names,
        issuer_key_id=auth.key_id(ca_cert),
    )


async def issuer_key_id(session: AsyncSession, now: datetime | None = None) -> str | None:
    """Which CA signs now (its key identifier), without making one."""
    now = _now(now)
    issuer = _issuer_of(await _active(session, now), now)
    if issuer is None:
        return None
    return auth.key_id(x509.load_pem_x509_certificate(issuer.cert_pem.encode("ascii")))


async def status(session: AsyncSession, now: datetime | None = None) -> dict[str, Any]:
    """For the console: the CAs, the gateway's certificate, and each machine's."""
    now = _now(now)
    rows = await _active(session, now)
    issuer = _issuer_of(rows, now)
    gateway = await session.get(MachineTlsClient, auth.GATEWAY_CLIENT)
    machines = (await session.execute(select(MachineCertificate))).scalars().all()
    return {
        "authorities": [
            {
                "fingerprint_sha256": r.fingerprint_sha256,
                "not_after": _aware(r.not_after).isoformat(),
                "issuing": issuer is not None and r.id == issuer.id,
                "issuing_from": _aware(r.issuing_from).isoformat(),
                "reason": r.reason,
            }
            for r in rows
        ],
        "gateway": None if gateway is None else {
            "not_after": _aware(gateway.not_after).isoformat(),
            "current": issuer is not None and gateway.ca_id == issuer.id,
        },
        "machines": {
            str(m.node_id): {
                "not_after": _aware(m.not_after).isoformat(),
                "names": list(m.names_json or []),
                "issued_at": _aware(m.issued_at).isoformat(),
                "current": issuer is not None and m.ca_id == issuer.id,
            }
            for m in machines
        },
    }


async def client_context(session: AsyncSession, master_key: str, now: datetime | None = None) -> Any:
    """An ``ssl.SSLContext`` presenting the gateway's certificate, for the backend's own probes.

    The machines' proxies let only the gateway's name through; the backend
    holds that identity anyway (it issued it), so its health probe of a model
    behind a proxy presents it too. ``None`` before there is one.
    """
    import os  # noqa: PLC0415
    import ssl  # noqa: PLC0415
    import tempfile  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    now = _now(now)
    row = await session.get(MachineTlsClient, auth.GATEWAY_CLIENT)
    trust = await trust_bundle(session, now)
    if row is None or not trust:
        return None
    key_pem = auth.unseal(row.key_sealed, master_key, auth.KEY_PURPOSE_CLIENT)
    ctx = ssl.create_default_context(cadata=trust)
    directory = Path(tempfile.mkdtemp(prefix="faden-machine-tls-"))
    try:
        cert_path, key_path = directory / "cert.pem", directory / "key.pem"
        cert_path.write_bytes(row.cert_pem.encode("ascii"))
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
        try:
            os.write(fd, key_pem)
        finally:
            os.close(fd)
        ctx.load_cert_chain(cert_path, key_path)
    finally:
        for name in ("cert.pem", "key.pem"):
            (directory / name).unlink(missing_ok=True)
        directory.rmdir()
    return ctx
