"""What the CLI does to accounts and the audit trail, run in the backend image, recorded like the console.

``faden admin reset-password``, ``faden admin grant-admin``, ``faden audit verify`` and the CLI's own events
(backup, restore, key rotation, upgrade) run this in the backend container::

    python -m faden_backend.tools.admin record --action backup.create --target-type install \
        --target-id nightly
    python -m faden_backend.tools.admin verify
    python -m faden_backend.tools.admin reset-password --email someone@example.com  # password on stdin
    python -m faden_backend.tools.admin grant-admin --email someone@example.com --reason "..."

Each prints one JSON line last (``{"ok": true, ...}``) and exits 1 when it did not do what it was asked.
Events written here say ``source: cli``; a password is never printed or recorded.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from faden_backend.db.models.containers import AuditResult
from faden_backend.services.audit import chain
from faden_backend.settings import settings


def _factory() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(str(settings.db_url))
    return async_sessionmaker(engine, expire_on_commit=False)


async def _record(
    factory: async_sessionmaker[AsyncSession],
    action: str,
    target_type: str,
    target_id: str,
    *,
    result: AuditResult = AuditResult.ALLOW,
    severity: str = "high",
    metadata: dict[str, Any] | None = None,
) -> str | None:
    chain.configure(factory, settings.audit_dir or None)
    event = await chain.append(chain.NewEvent(
        action=action,
        target_type=target_type,
        target_id=target_id,
        result=result,
        severity=severity,
        metadata_json=json.dumps(metadata) if metadata else None,
        source="cli",
    ))
    return event.hash


async def record(args: argparse.Namespace) -> dict[str, Any]:
    """One event from the CLI (a backup, a restore, a key rotation, an upgrade)."""
    metadata = json.loads(args.meta) if args.meta else None
    digest = await _record(
        _factory(), args.action, args.target_type, args.target_id,
        result=AuditResult(args.result), severity=args.severity, metadata=metadata,
    )
    return {"ok": True, "hash": digest}


async def verify(args: argparse.Namespace) -> dict[str, Any]:
    """The whole chain and the file copies (``faden audit verify``)."""
    async with _factory()() as session:
        verdict = await chain.verify(session, args.audit_dir or settings.audit_dir or None)
    return {
        "ok": verdict.ok,
        "events": verdict.events,
        "legacy_rows": verdict.legacy_rows,
        "intact_until": verdict.intact_until.isoformat() if verdict.intact_until else None,
        "problems": verdict.problems or [],
    }


async def _user_by_email(session: AsyncSession, email: str) -> Any:
    from faden_backend.db.models.users import User  # noqa: PLC0415

    return (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()


async def reset_password(args: argparse.Namespace) -> dict[str, Any]:
    """Set a new password (read from stdin); the account's sign-ins end; recorded, never the password."""
    from fastapi_users.password import PasswordHelper  # noqa: PLC0415

    from faden_backend.db.models.users import end_sign_ins  # noqa: PLC0415
    from faden_backend.services.password_policy import password_problem  # noqa: PLC0415

    password = sys.stdin.readline().rstrip("\r\n")
    problem = password_problem(password, args.email)
    if problem:
        return {"ok": False, "error": problem}
    factory = _factory()
    async with factory() as session:
        user = await _user_by_email(session, args.email)
        if user is None:
            return {"ok": False, "error": f"No account with the address {args.email}."}
        user.hashed_password = PasswordHelper().hash(password)
        end_sign_ins(user)
        await session.commit()
        user_id = str(user.id)
    await _record(factory, "user.password.reset_cli", "user", user_id, metadata={"email": args.email})
    return {"ok": True, "user_id": user_id}


async def grant_admin(args: argparse.Namespace) -> dict[str, Any]:
    """Make an account administrator from the server (a locked-out organisation's way back); recorded."""
    factory = _factory()
    async with factory() as session:
        user = await _user_by_email(session, args.email)
        if user is None:
            return {"ok": False, "error": f"No account with the address {args.email}."}
        already = bool(user.is_superuser)
        user.is_superuser = True
        user.is_active = True
        await session.commit()
        user_id = str(user.id)
    if not already:
        await _record(
            factory, "user.administrator.grant", "user", user_id,
            metadata={"email": args.email, "reason": args.reason, "how": "faden admin grant-admin"},
        )
    return {"ok": True, "user_id": user_id, "already": already}


def main(argv: list[str] | None = None) -> int:
    """Parse, run, print the JSON line."""
    parser = argparse.ArgumentParser(prog="python -m faden_backend.tools.admin")
    commands = parser.add_subparsers(dest="command", required=True)
    rec = commands.add_parser("record")
    rec.add_argument("--action", required=True)
    rec.add_argument("--target-type", required=True)
    rec.add_argument("--target-id", required=True)
    rec.add_argument("--result", choices=["allow", "deny"], default="allow")
    rec.add_argument("--severity", default="high")
    rec.add_argument("--meta", default="")
    ver = commands.add_parser("verify")
    ver.add_argument("--audit-dir", default="")
    reset = commands.add_parser("reset-password")
    reset.add_argument("--email", required=True)
    grant = commands.add_parser("grant-admin")
    grant.add_argument("--email", required=True)
    grant.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    run = {"record": record, "verify": verify, "reset-password": reset_password, "grant-admin": grant_admin}
    try:
        outcome = asyncio.run(run[args.command](args))
    except Exception as exc:
        outcome = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(outcome))  # noqa: T201 - the CLI reads this line
    return 0 if outcome.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
