"""``faden admin`` — administrative commands for a running deployment."""

from __future__ import annotations

import re
import secrets
import string
import sys

import click

from faden_cli.core.console import console, error, info, success, warning


def _generate_password(length: int = 24) -> str:
    """Generate a random password (letters + digits + punctuation subset)."""
    alphabet = string.ascii_letters + string.digits + "!@#$%&*-_=+"
    return "".join(secrets.choice(alphabet) for _ in range(length))


# Minimal email validation — rejects obvious injection attempts.
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-.]+$")


@click.group("admin")
def admin_group() -> None:
    """Administrative commands for a running Fadenstack deployment."""


@admin_group.command("reset-password")
@click.option(
    "--email",
    default="admin@localhost",
    show_default=True,
    help="Email address of the user whose password will be reset.",
)
@click.option(
    "--password",
    default=None,
    help="New password.  Auto-generated if omitted.",
)
@click.option(
    "-y", "--yes",
    is_flag=True,
    help="Skip confirmation prompt.",
)
def reset_password_cmd(*, email: str, password: str | None, yes: bool) -> None:
    """Reset a user's password, recorded in the audit trail; the account's sign-ins end.

    The backend container must be running: it applies the password rule and the hashing.
    It changes the console sign-in only: Grafana, Langfuse and RabbitMQ
    have passwords of their own in .env, which it leaves as they are.
    """
    # Validate email early to prevent any injection
    if not _EMAIL_RE.match(email):
        error(f"Invalid email address: {email}")
        sys.exit(1)

    # The backend's rule for a new password (faden_backend/services/password_policy.py).
    from faden_cli.core.bootstrap import MIN_PASSWORD_LENGTH  # noqa: PLC0415

    if password is not None and len(password) < MIN_PASSWORD_LENGTH:
        error(f"The password needs at least {MIN_PASSWORD_LENGTH} characters (or leave out --password).")
        sys.exit(1)

    new_password = password or _generate_password()

    if not yes:
        click.confirm(
            f"Reset password for '{email}'?",
            abort=True,
        )

    # The backend sets it (its password rule, its hashing), ends the account's sign-ins -- a reset is often
    # meant to lock someone out -- and records the reset in the audit trail. It used to be an UPDATE in psql,
    # which nobody could see afterwards.
    info("Setting the new password…")
    from faden_cli.core.backend_tool import run_backend_tool  # noqa: PLC0415

    outcome = run_backend_tool(["reset-password", "--email", email], stdin=new_password + "\n")
    if not outcome.get("ok"):
        error(f"The password was not reset: {outcome.get('error', 'unknown error')}")
        error("  Is the backend running? (faden status)")
        sys.exit(1)

    success(f"Password reset for '{email}'.")
    # Only the console's password: Grafana, Langfuse and RabbitMQ have passwords of their own in .env.
    info("Grafana, Langfuse and RabbitMQ sign-ins are not touched: their passwords are in .env.")

    # ── 3. Display credentials ────────────────────────────────
    from rich.panel import Panel
    from rich.text import Text

    lines = Text()
    lines.append("  Email:     ", style="bold")
    lines.append(email, style="cyan")
    lines.append("\n  Password:  ", style="bold")
    lines.append(new_password, style="cyan")
    lines.append("\n\n  Store this in a password manager.", style="dim")

    console.print()
    console.print(
        Panel(
            lines,
            title="[bold yellow]New Credentials[/bold yellow]",
            border_style="yellow",
            padding=(1, 2),
        )
    )


@admin_group.command("grant-admin")
@click.option("--email", required=True, help="The account to make administrator.")
@click.option("--reason", required=True, help="Why (recorded in the audit trail).")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation prompt.")
def grant_admin_cmd(*, email: str, reason: str, yes: bool) -> None:
    """Make an account administrator from the server: the way back for an organisation locked out of the console.

    In the console, an administrator grants the right or hands it over (Users, Root Mode). Recorded in the
    audit trail with the reason.
    """
    if not _EMAIL_RE.match(email):
        error(f"Invalid email address: {email}")
        sys.exit(1)
    if len(reason.strip()) < 10:
        error("Give a reason of at least 10 characters: it is recorded in the audit trail.")
        sys.exit(1)
    if not yes:
        click.confirm(f"Make '{email}' an administrator?", abort=True)
    from faden_cli.core.backend_tool import run_backend_tool  # noqa: PLC0415

    outcome = run_backend_tool(["grant-admin", "--email", email, "--reason", reason])
    if not outcome.get("ok"):
        error(f"Not done: {outcome.get('error', 'unknown error')}")
        sys.exit(1)
    if outcome.get("already"):
        info(f"'{email}' is an administrator already.")
    else:
        success(f"'{email}' is an administrator now; recorded in the audit trail.")
