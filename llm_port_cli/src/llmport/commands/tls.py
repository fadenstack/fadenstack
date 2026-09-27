"""``llmport tls`` — serve the console, the API and /v1 over HTTPS, or stop.

nginx picks HTTPS or plain HTTP when it starts, from what ``<install>/tls``
holds (llm_port_shared/nginx/select-config.sh), so every change here ends with
a restart of the proxy -- the only container it touches.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import click

from llmport.core import tls
from llmport.core.console import console, error, info, success, warning
from llmport.core.edge import console_url, url_for
from llmport.core.env_gen import read_env_file
from llmport.core.settings import load_config

PROXY_SERVICE = "nginx"
PROXY_CONTAINER = "llm-port-nginx"


def _install_dir() -> Path:
    cfg = load_config()
    if not cfg.install_dir:
        error("No install found. Run llmport deploy first.")
        sys.exit(1)
    return Path(cfg.install_dir)


def _proxy_running() -> bool:
    docker = shutil.which("docker")
    if not docker:
        return False
    result = subprocess.run(  # noqa: S603
        [docker, "inspect", "-f", "{{.State.Running}}", PROXY_CONTAINER],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def apply_to_proxy(install_dir: Path, *, recreate: bool = False) -> bool:
    """Have nginx pick up the install's material and ports; ``True`` once the console answers.

    A restart re-runs select-config.sh; new ports need the container made
    again (*recreate*), which ``docker compose up`` does when they changed.
    """
    from llmport.core.bootstrap import wait_for_backend  # noqa: PLC0415
    from llmport.core.compose import build_context_from_config, restart, up  # noqa: PLC0415

    if not _proxy_running():
        info("The proxy is not running; it serves this the next time the install starts.")
        return True
    ctx = build_context_from_config(load_config())
    if recreate:
        rc = up(ctx, services=[PROXY_SERVICE], detach=True, wait=True, timeout=120)
    else:
        rc = restart(ctx, services=[PROXY_SERVICE])
    if rc != 0:
        error(f"Restarting the proxy failed (exit code {rc}); see: llmport logs {PROXY_SERVICE}")
        return False
    url = console_url(install_dir, read_env_file(install_dir / ".env"))
    if wait_for_backend(url, timeout=90):
        success(f"The console answers at {url}")
        return True
    error(f"The console does not answer at {url}; see: llmport logs {PROXY_SERVICE}")
    return False


def addresses(install_dir: Path) -> list[str]:
    """The URLs other machines reach the console by, the way it is served now."""
    env = read_env_file(install_dir / ".env")
    https = tls.material_present(install_dir)
    loopback = {"localhost", "127.0.0.1", "::1"}
    return [url_for(name, https=https, env=env) for name in tls.machine_names() if name not in loopback]


def print_summary(summary: tls.CertSummary) -> None:
    """The certificate as an operator reads it, with what to watch out for."""
    console.print(f"  Certificate: [bold]{summary.subject}[/bold]")
    console.print(f"  Issued by:   {summary.issuer}{'  (this install)' if summary.made_here else ''}")
    console.print(f"  Valid until: {summary.not_after:%Y-%m-%d}")
    console.print(f"  Names:       {', '.join(summary.names) or '(none)'}")
    for note in summary.warnings:
        warning(note)


def print_trust_hints(install_dir: Path, ca_public: Path) -> None:
    """How browsers, API clients and machines come to trust a certificate made here."""
    urls = addresses(install_dir)
    example = urls[0] if urls else "https://<this server>"
    console.print(
        "\n  [bold]Trust this install's certificate authority once[/bold],"
        " wherever the console is used from:\n"
        f"    {ca_public}\n"
        "  [dim]Browsers: import it as a trusted certificate authority (or accept the warning).\n"
        f"  API clients: curl --cacert ca.crt {example}/v1/models  ·  Python: SSL_CERT_FILE=ca.crt\n"
        "  Machines already added still use plain HTTP, which now only redirects: point each agent at\n"
        f"  {example} and give it the file (LLM_PORT_NODE_AGENT_BACKEND_URL,\n"
        '  LLM_PORT_NODE_AGENT_TLS_CA_BUNDLE; docs/installing.md, "Enabling HTTPS").[/dim]'
    )


def print_addresses(install_dir: Path) -> None:
    """Where other machines reach the console now."""
    urls = addresses(install_dir)
    if urls:
        console.print("\n  Reach the console at: " + "  ".join(urls))


TLS_MODES = ("off", "generate", "own")


def _free_port(label: str, port: int, *, yes: bool, current: tuple[int, int], other: int) -> int:
    """*port*, or another one the operator picks when something else holds it."""
    from llmport.commands.ports import port_conflicts  # noqa: PLC0415

    while True:
        pair = (port, other) if label == "HTTP" else (other, port)
        clash = [c for c in port_conflicts(*pair, current=current) if c.startswith(f"{label} port")]
        if not clash:
            return port
        if yes:
            error(f"{clash[0]} Free it, or pass --{label.lower()}-port.")
            sys.exit(1)
        warning(clash[0])
        port = click.prompt(f"  {label} port to use instead", type=click.IntRange(1, 65535))


def _ports_step(env_path: Path, *, yes: bool, http_port: int | None, https_port: int | None) -> None:
    from llmport.commands.ports import set_ports  # noqa: PLC0415
    from llmport.core.edge import check_ports, ports  # noqa: PLC0415

    current = ports(read_env_file(env_path))
    http = http_port if http_port is not None else current[0]
    https = https_port if https_port is not None else current[1]
    problem = check_ports(http, https)
    if problem:
        error(problem)
        sys.exit(2)
    http = _free_port("HTTP", http, yes=yes, current=current, other=https)
    https = _free_port("HTTPS", https, yes=yes, current=current, other=http)
    set_ports(env_path, http=http, https=https)
    console.print(
        f"  HTTP on port {http}, HTTPS on port {https} "
        "[dim](change: --http-port/--https-port, or later llmport ports)[/dim]"
    )


def _ask_mode(install_dir: Path, *, yes: bool) -> str | None:
    """What to do about HTTPS when deploy was not told; ``None`` to leave it as it is."""
    present = tls.current(install_dir)
    if present is not None:
        info(
            f"HTTPS is on, with {present.subject} until {present.not_after:%Y-%m-%d}"
            " (change: llmport tls)."
        )
        return None
    if yes:
        info("HTTPS is off. Switch it on any time: llmport tls generate, or llmport tls use CERT KEY.")
        return None
    console.print(
        "  Serve the console, the API and /v1 over HTTPS?\n"
        "    [bold]off[/bold]       plain HTTP (switch HTTPS on later with llmport tls)\n"
        "    [bold]generate[/bold]  a certificate made here for this machine's names and addresses\n"
        "    [bold]own[/bold]       your own certificate and key (PEM files)"
    )
    return click.prompt("  HTTPS", type=click.Choice(TLS_MODES), default="off")


def _generate_step(install_dir: Path, *, yes: bool, names: tuple[str, ...]) -> Path:
    if not names and not yes:
        extra = click.prompt(
            "  Other names or addresses the console is reached by (comma-separated, blank for none)",
            default="",
            show_default=False,
        )
        names = tuple(n.strip() for n in extra.split(",") if n.strip())
    try:
        made = tls.generate(install_dir, names)
    except tls.TlsError as exc:
        error(str(exc))
        sys.exit(1)
    success("HTTPS on, with a certificate made here.")
    print_summary(made.summary)
    return made.ca_public


def _own_step(
    install_dir: Path, *, yes: bool, cert: Path | None, key: Path | None, chain: Path | None
) -> None:
    attempts = 1 if yes else 3
    for attempt in range(attempts):
        if cert is None or key is None:
            if yes:
                error("--tls own needs --tls-cert and --tls-key.")
                sys.exit(2)
            cert = Path(click.prompt("  Certificate file (PEM; may carry its chain)")).expanduser()
            key = Path(click.prompt("  Its private key (PEM, without a passphrase)")).expanduser()
            raw_chain = click.prompt(
                "  Intermediate certificates (PEM, blank for none)", default="", show_default=False
            )
            chain = Path(raw_chain).expanduser() if raw_chain.strip() else None
        try:
            summary = tls.use_own(install_dir, cert, key, chain)
        except tls.TlsError as exc:
            error(str(exc))
            if attempt + 1 == attempts:
                sys.exit(1)
            cert = key = chain = None
            continue
        success("HTTPS on, with your certificate.")
        print_summary(summary)
        return


def deploy_step(
    install_dir: Path,
    env_path: Path,
    *,
    yes: bool,
    http_port: int | None,
    https_port: int | None,
    mode: str | None,
    cert: Path | None,
    key: Path | None,
    chain: Path | None,
    names: tuple[str, ...],
) -> Path | None:
    """``llmport deploy``'s ports-and-HTTPS step, before anything starts.

    Returns the authority to trust when a certificate was made here. With
    ``--yes`` and no ``--tls``, what the install has stays as it is.
    """
    _ports_step(env_path, yes=yes, http_port=http_port, https_port=https_port)

    if mode is None and (cert or key):
        mode = "own"
    if mode is None:
        mode = _ask_mode(install_dir, yes=yes)
    if mode is None:
        return None
    if mode == "off":
        if tls.remove(install_dir):
            info("HTTPS switched off: the certificate was removed (the install's authority is kept).")
        else:
            info("HTTPS is off.")
        return None
    if mode == "generate":
        return _generate_step(install_dir, yes=yes, names=names)
    _own_step(install_dir, yes=yes, cert=cert, key=key, chain=chain)
    return None


@click.group("tls")
def tls_group() -> None:
    """HTTPS for the console, the API and the gateway (/v1).

    \b
    llmport tls status                  what nginx serves now
    llmport tls generate                a certificate made here for this machine
    llmport tls use CERT KEY            your own certificate (and --chain)
    llmport tls off                     back to plain HTTP
    """


@tls_group.command("status")
def status_cmd() -> None:
    """Show whether HTTPS is on, and the certificate it uses."""
    install_dir = _install_dir()
    try:
        summary = tls.current(install_dir)
    except tls.TlsError as exc:
        error(str(exc))
        sys.exit(1)
    if summary is None:
        console.print("HTTPS is [bold]off[/bold]: the console is served over plain HTTP.")
        console.print("[dim]  Switch it on: llmport tls generate, or llmport tls use CERT KEY[/dim]")
    else:
        console.print("HTTPS is [bold green]on[/bold green]; plain HTTP redirects to it.")
        print_summary(summary)
        ca_public = install_dir / tls.TLS_DIR / tls.CA_PUBLIC_FILE
        if summary.made_here and ca_public.is_file():
            console.print(f"  Authority:   {ca_public}")
    print_addresses(install_dir)


@tls_group.command("generate")
@click.option(
    "--name", "-n", "names", multiple=True, metavar="NAME",
    help="Another DNS name or address the console is reached by (repeatable). "
    "This machine's host names and addresses are always included.",
)
@click.option(
    "--no-restart", is_flag=True, help="Write the files only; the proxy picks them up when it next starts."
)
def generate_cmd(*, names: tuple[str, ...], no_restart: bool) -> None:
    """Make a certificate for this machine, signed by the install's own authority."""
    install_dir = _install_dir()
    try:
        made = tls.generate(install_dir, names)
    except tls.TlsError as exc:
        error(str(exc))
        sys.exit(1)
    if made.ca_created:
        success(f"Certificate authority for this install made in {install_dir / tls.CA_DIR}")
    success("Certificate made and installed.")
    print_summary(made.summary)
    if not no_restart and not apply_to_proxy(install_dir):
        sys.exit(1)
    print_trust_hints(install_dir, made.ca_public)
    print_addresses(install_dir)


@tls_group.command("use")
@click.argument("cert", type=click.Path(dir_okay=False, path_type=Path))
@click.argument("key", type=click.Path(dir_okay=False, path_type=Path))
@click.option(
    "--chain", type=click.Path(dir_okay=False, path_type=Path), default=None,
    help="The issuer's intermediate certificates (PEM), when CERT does not already carry them.",
)
@click.option(
    "--no-restart", is_flag=True, help="Write the files only; the proxy picks them up when it next starts."
)
def use_cmd(*, cert: Path, key: Path, chain: Path | None, no_restart: bool) -> None:
    """Serve your own certificate: CERT and KEY are PEM files (CERT may be a full chain)."""
    install_dir = _install_dir()
    try:
        summary = tls.use_own(install_dir, cert, key, chain)
    except tls.TlsError as exc:
        error(str(exc))
        sys.exit(1)
    success("Certificate checked and installed.")
    print_summary(summary)
    if not no_restart and not apply_to_proxy(install_dir):
        sys.exit(1)
    print_addresses(install_dir)


@tls_group.command("off")
@click.option(
    "--no-restart", is_flag=True, help="Remove the files only; the proxy follows when it next starts."
)
def off_cmd(*, no_restart: bool) -> None:
    """Go back to plain HTTP. The install's authority is kept for next time."""
    install_dir = _install_dir()
    if not tls.remove(install_dir):
        info("HTTPS was already off.")
        return
    success("Certificate removed.")
    if not no_restart and not apply_to_proxy(install_dir):
        sys.exit(1)
    env = read_env_file(install_dir / ".env")
    if (env.get("LLM_PORT_HSTS_MAX_AGE") or "0").strip() not in ("", "0"):
        warning(
            "LLM_PORT_HSTS_MAX_AGE is set: browsers that saw it keep insisting on HTTPS until it runs out."
        )
    print_addresses(install_dir)
