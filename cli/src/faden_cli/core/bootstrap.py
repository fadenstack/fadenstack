"""First-admin bootstrap — calls ``POST /api/bootstrap``.

Used by ``faden deploy`` and ``faden dev up --local-node`` to create the
initial admin user after services are up.  The raw credentials are
displayed once and optionally saved to a local file.

The backend creates the first administrator only for a caller with the
install's bootstrap token (``FADEN_BACKEND_BOOTSTRAP_TOKEN`` in ``.env``), which
is sent in the ``X-Faden-Bootstrap-Token`` header.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx

from faden_cli.core.console import console, error, success, warning
from faden_cli.core.edge import verify_for
from faden_cli.core.env_gen import BOOTSTRAP_TOKEN_KEY, read_env_file
from faden_cli.core.private_files import write_private

#: The header the backend reads the install's bootstrap token from.
BOOTSTRAP_TOKEN_HEADER = "X-Faden-Bootstrap-Token"  # noqa: S105 - a header name


def wait_for_backend(base_url: str, *, timeout: int = 120) -> bool:
    """Poll the backend health endpoint until it responds 200."""
    deadline = time.monotonic() + timeout
    health_url = f"{base_url}/api/health"
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(health_url, timeout=5, verify=verify_for(base_url))
            if resp.status_code == 200:  # noqa: PLR2004
                return True
        except httpx.HTTPError:
            pass
        time.sleep(2)
    return False


def check_needs_bootstrap(base_url: str) -> bool | None:
    """Return True if the system needs bootstrap, False if already done, None on error."""
    try:
        resp = httpx.get(f"{base_url}/api/bootstrap/status", timeout=10, verify=verify_for(base_url))
        if resp.status_code == 200:  # noqa: PLR2004
            return resp.json().get("needs_bootstrap", False)
    except httpx.HTTPError:
        pass
    return None


def read_bootstrap_token(install_dir: Path) -> str | None:
    """The bootstrap token in *install_dir*'s ``.env``, or ``None`` when it has none."""
    return read_env_file(Path(install_dir) / ".env").get(BOOTSTRAP_TOKEN_KEY) or None


def run_bootstrap(
    base_url: str,
    *,
    email: str = "admin@localhost",
    password: str | None = None,
    generate_api_token: bool = True,
    tenant_id: str = "default",
    token: str | None = None,
) -> dict | None:
    """Call the bootstrap endpoint and return the credentials dict, or None on failure.

    *token* is the install's bootstrap token; the backend refuses without it.
    """
    payload: dict = {
        "email": email,
        "generate_api_token": generate_api_token,
        "tenant_id": tenant_id,
    }
    if password:
        payload["password"] = password

    try:
        resp = httpx.post(
            f"{base_url}/api/bootstrap",
            json=payload,
            headers={BOOTSTRAP_TOKEN_HEADER: token} if token else None,
            timeout=30,
            verify=verify_for(base_url),
        )
    except httpx.HTTPError as exc:
        error(f"Could not reach backend: {exc}")
        return None

    if resp.status_code == 201:  # noqa: PLR2004
        return resp.json()

    if resp.status_code == 409:  # noqa: PLR2004
        warning("System is already bootstrapped — admin users exist.")
        return None

    if resp.status_code == 403:  # noqa: PLR2004
        try:
            detail = resp.json().get("detail") or resp.text
        except ValueError:
            detail = resp.text
        error(
            f"The backend refused to create the administrator: {detail}\n"
            f"  The token is {BOOTSTRAP_TOKEN_KEY} in the install's .env (faden deploy adds one when it is"
            " missing), and the backend reads it when it starts: run `faden up`, then `faden deploy` again."
        )
        return None

    error(f"Bootstrap failed (HTTP {resp.status_code}): {resp.text}")
    return None


def display_credentials(creds: dict) -> None:
    """Print credentials in a prominent panel with a save warning."""
    from rich.panel import Panel  # noqa: PLC0415
    from rich.text import Text  # noqa: PLC0415

    lines = Text()
    lines.append("  Admin email:     ", style="bold")
    lines.append(creds["email"], style="cyan")
    lines.append("\n  Admin password:  ", style="bold")
    lines.append(creds["password"], style="cyan")
    lines.append("\n")
    lines.append(
        "\n  Store these in a password manager or secure vault.\n"
        "  The password is hashed and cannot be retrieved from the system after this point.\n"
        "  Make API tokens in your profile in the console.",
        style="dim",
    )

    console.print()
    console.print(
        Panel(
            lines,
            title="[bold yellow]⚠  SAVE THESE CREDENTIALS — THEY CANNOT BE RECOVERED  ⚠[/bold yellow]",
            border_style="yellow",
            padding=(1, 2),
        )
    )


def sign_in_with_saved_credentials(backend_url: str, directory: Path) -> str | None:
    """A sign-in for the administrator saved in *directory*'s ``.bootstrap-credentials``, or ``None``."""
    creds_path = directory / ".bootstrap-credentials"
    try:
        saved = dict(
            line.split("=", 1) for line in creds_path.read_text(encoding="utf-8").splitlines()
            if "=" in line and not line.startswith("#")
        )
    except OSError:
        return None
    email, password = saved.get("ADMIN_EMAIL", "").strip(), saved.get("ADMIN_PASSWORD", "").strip()
    if not email or not password:
        return None
    try:
        resp = httpx.post(
            f"{backend_url.rstrip('/')}/api/auth/jwt/login",
            data={"username": email, "password": password},
            timeout=15,
            verify=verify_for(backend_url),
        )
    except httpx.HTTPError as exc:
        warning(f"Could not sign in with the saved credentials: {exc}")
        return None
    if resp.status_code != 200:  # noqa: PLR2004
        warning(f"Signing in with the saved credentials failed (HTTP {resp.status_code}).")
        return None
    return resp.json().get("access_token") or None


def save_credentials_file(creds: dict, directory: Path) -> Path | None:
    """Write credentials to a restricted file (chmod 600). Returns the path."""
    creds_path = directory / ".bootstrap-credentials"
    # Not the setup token: it lasts an hour, the time it takes to finish setting up.
    content_lines = [
        f"# Fadenstack bootstrap credentials — generated {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"ADMIN_EMAIL={creds['email']}",
        f"ADMIN_PASSWORD={creds['password']}",
    ]
    content_lines.append(
        "\n# DELETE THIS FILE after storing the credentials in a secure vault."
    )

    try:
        # Readable by its owner only from the start: it was written with the default mode, then narrowed.
        write_private(creds_path, ("\n".join(content_lines) + "\n").encode("utf-8"))
        return creds_path
    except OSError as exc:
        error(f"Could not save credentials file: {exc}")
        return None


def bootstrap_interactive(
    base_url: str,
    install_dir: Path,
    *,
    auto_confirm: bool = False,
    default_email: str = "admin@localhost",
    default_password: str | None = None,
) -> dict | None:
    """Full interactive bootstrap flow (prompt → call API → display → save).

    If *auto_confirm* is True, uses defaults and auto-saves without prompting.
    Returns the credentials dict if successful, None otherwise.
    """
    import click  # noqa: PLC0415

    # ── Check if bootstrap is needed ──────────────────────────
    needs = check_needs_bootstrap(base_url)
    if needs is None:
        warning("Could not determine bootstrap status — skipping admin setup.")
        return None
    if not needs:
        console.print("[dim]  Admin user already exists — skipping bootstrap.[/dim]")
        return None

    # ── Gather inputs ─────────────────────────────────────────
    if auto_confirm:
        email = default_email
        password = default_password
    else:
        email = click.prompt("  Admin email", default=default_email)
        password_input = click.prompt(
            "  Admin password (leave blank to auto-generate)",
            default="",
            hide_input=True,
            show_default=False,
        )
        password = password_input or None

    # ── Call bootstrap API ────────────────────────────────────
    creds = run_bootstrap(
        base_url,
        email=email,
        password=password,
        generate_api_token=True,
        token=read_bootstrap_token(install_dir),
    )
    if not creds:
        return None

    success("Admin user created.")

    # ── Display credentials ───────────────────────────────────
    display_credentials(creds)

    # ── Save to file ──────────────────────────────────────────
    if auto_confirm:
        save_file = True
    else:
        save_file = click.confirm("\n  Save to file?", default=False)

    if save_file:
        saved = save_credentials_file(creds, install_dir)
        if saved:
            success(f"Saved to {saved}")
            if os.name != "nt":
                console.print(f"  [dim](permissions: 600 — owner read/write only)[/dim]")
            warning("Delete this file after storing the credentials elsewhere.")

    return creds
