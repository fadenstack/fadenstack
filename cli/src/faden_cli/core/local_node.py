"""Local node-agent provisioning helpers for dev/deploy workflows."""

from __future__ import annotations

import os
import platform
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx

from faden_cli.core.console import error, info, success, warning
from faden_cli.core.private_files import write_private
from faden_cli.core.edge import verify_for
from faden_cli.core.registry import CORE_REPO, core_repo_clone_url

# The agent lives in the core repository, under agent/.
_NODE_AGENT_DIR = "agent"


def create_enrollment_token(
    backend_url: str,
    api_token: str,
    *,
    note: str = "Auto-enrolled local node",
) -> str | None:
    """Create an enrollment token via the backend API.

    Returns the plaintext token string, or None on failure.
    """
    url = f"{backend_url.rstrip('/')}/api/admin/system/nodes/enrollment-tokens"
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }
    try:
        resp = httpx.post(url, json={"note": note}, headers=headers, timeout=15)
        if resp.status_code in (200, 201):
            token = resp.json().get("token", "")
            if token:
                success("Enrollment token created for local node.")
                return token
            error("Enrollment token response missing 'token' field.")
            return None
        error(f"Failed to create enrollment token (HTTP {resp.status_code}): {resp.text}")
        return None
    except httpx.HTTPError as exc:
        error(f"Could not reach backend for enrollment token: {exc}")
        return None


def remove_local_node_agent(*, workspace: Path, use_sudo: bool = True) -> bool:
    """Stop the local node agent (systemd service and/or running process).

    The cloned source directory is intentionally left in place so that
    unpushed work is never destroyed by ``faden down --all``.

    Returns True if cleanup succeeded (or nothing to clean up).
    """
    removed_anything = False

    # ── Stop and remove systemd service (Linux only) ──────────
    if platform.system() == "Linux" and use_sudo:
        systemctl = shutil.which("systemctl")
        if systemctl:
            # Check if the service unit exists before trying to stop it
            probe = subprocess.run(  # noqa: S603
                ["systemctl", "list-unit-files", "faden-agent.service"],
                capture_output=True, text=True,
            )
            if "faden-agent.service" in (probe.stdout or ""):
                info("Stopping faden-agent systemd service…")
                _run(["sudo", "systemctl", "disable", "--now", "faden-agent"], check=False)
                _run(["sudo", "rm", "-f", "/etc/systemd/system/faden-agent.service"], check=False)
                _run(["sudo", "rm", "-f", "/etc/faden-agent.env"], check=False)
                _run(["sudo", "systemctl", "daemon-reload"], check=False)
                removed_anything = True

    # ── Kill any running agent process (cross-platform) ───────
    if platform.system() == "Windows":
        # Best-effort: kill by process name
        kill_result = subprocess.run(  # noqa: S603
            ["taskkill", "/F", "/IM", "faden-agent.exe"],
            capture_output=True, text=True,
        )
        if kill_result.returncode == 0:
            removed_anything = True
    else:
        kill_result = subprocess.run(  # noqa: S603
            ["pkill", "-f", "fadenstack.node.agent"],
            capture_output=True, text=True,
        )
        if kill_result.returncode == 0:
            removed_anything = True

    if removed_anything:
        success("Local node agent stopped.")
    else:
        info("No local node agent service found to remove.")
    return True


def _derive_loki_url(backend_url: str) -> str:
    """Derive Loki push URL from the backend URL (same host, port 3100)."""
    parsed = urlparse(backend_url)
    host = parsed.hostname or "127.0.0.1"
    scheme = parsed.scheme or "http"
    return f"{scheme}://{host}:3100"


def _build_env_lines(
    backend_url: str,
    advertise_host: str,
    enrollment_token: str,
) -> list[str]:
    """Build the common env lines for all provisioning paths."""
    loki_url = _derive_loki_url(backend_url)
    lines = [
        f"FADEN_NODE_AGENT_BACKEND_URL={backend_url}",
        f"FADEN_NODE_AGENT_LOKI_URL={loki_url}",
    ]
    if advertise_host:
        lines.append(f"FADEN_NODE_AGENT_ADVERTISE_HOST={advertise_host}")
    if enrollment_token:
        lines.append(f"FADEN_NODE_AGENT_ENROLLMENT_TOKEN={enrollment_token}")
    else:
        warning("No enrollment token provided. Agent will require later onboarding.")
    return lines


def core_agent_dir(workspace: Path) -> Path | None:
    """The agent's source in a core checkout, if there is one.

    *workspace* is the development workspace (``faden dev up``: the checkout is
    ``<workspace>/fadenstack``) or the checkout itself (``faden deploy`` run in
    it). A release install has neither, and takes the released agent from its
    server instead (see :func:`_provision_release`). The agent's
    ``pyproject.toml`` is what tells a checkout from a directory of that name.
    """
    for agent_dir in (workspace / CORE_REPO / _NODE_AGENT_DIR, workspace / _NODE_AGENT_DIR):
        if (agent_dir / "pyproject.toml").is_file():
            return agent_dir
    return None


def _find_agent_binary(agent_dir: Path) -> Path | None:
    """Locate a pre-built node agent binary in the core checkout's agent/dist."""
    candidates = [agent_dir / "dist" / "faden-agent", agent_dir / "dist" / "faden-agent.exe"]
    for path in candidates:
        if path.is_file():
            return path
    return None


def provision_local_node_agent(
    *,
    workspace: Path,
    branch: str,
    backend_url: str,
    advertise_host: str,
    enrollment_token: str,
    remote_host: str | None,
    use_sudo: bool,
    method: str = "https",
    github_token: str = "",
    workdir_override: str | None = None,
    ca_file: Path | None = None,
) -> bool:
    """Install the node agent on this or a remote host.

    From a core checkout (the development case) the agent is built from that
    checkout's own source; the checkout is never cloned over, switched or
    pulled. Without one (a release install) the released agent is fetched
    through the running backend's install line, which checks it against the
    SHA-256 this core release pins. *ca_file* is the install's ``tls/ca.crt``
    for a backend on HTTPS: the line fetches from the backend through it, and
    the agent keeps it.
    """
    agent_dir = core_agent_dir(workspace)
    branch = branch.strip() or "main"
    backend_url = backend_url.strip() or "http://127.0.0.1:8000"
    advertise_host = advertise_host.strip()
    enrollment_token = enrollment_token.strip()

    if remote_host:
        if agent_dir is None:
            return _provision_remote_release(
                remote_host=remote_host,
                backend_url=backend_url,
                advertise_host=advertise_host,
                enrollment_token=enrollment_token,
                use_sudo=use_sudo,
                ca_file=ca_file,
            )
        return _provision_remote(
            remote_host=remote_host,
            repo_url=core_repo_clone_url(method=method, token=github_token),
            branch=branch,
            backend_url=backend_url,
            advertise_host=advertise_host,
            enrollment_token=enrollment_token,
            use_sudo=use_sudo,
            workdir_override=workdir_override,
        )

    if agent_dir is None:
        return _provision_release(
            backend_url=backend_url,
            advertise_host=advertise_host,
            enrollment_token=enrollment_token,
            use_sudo=use_sudo,
            ca_file=ca_file,
        )

    # Try binary-first: if a pre-built executable exists, skip the venv.
    binary = _find_agent_binary(agent_dir)
    if binary:
        return _provision_local_binary(
            binary=binary,
            agent_dir=agent_dir,
            backend_url=backend_url,
            advertise_host=advertise_host,
            enrollment_token=enrollment_token,
            use_sudo=use_sudo,
        )

    return _provision_local(
        repo_dir=agent_dir,
        backend_url=backend_url,
        advertise_host=advertise_host,
        enrollment_token=enrollment_token,
        use_sudo=use_sudo,
    )


def _provision_release(
    *,
    backend_url: str,
    advertise_host: str,
    enrollment_token: str,
    use_sudo: bool,
    ca_file: Path | None = None,
) -> bool:
    """Install the released agent through the backend's own install line (Linux).

    The line downloads the build this core release pins and refuses it unless
    it hashes to the pinned SHA-256, so what runs here is what was released.
    """
    if platform.system() != "Linux":
        error(
            "No core checkout found, and the released agent is a Linux binary. "
            "Install the agent on this machine from the console's install line, "
            "or run from a development workspace."
        )
        return False
    script = _fetch_install_line(backend_url)
    if script is None:
        return False
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".sh", newline="\n") as handle:
        handle.write(script)
        path = Path(handle.name)
    try:
        env = {**os.environ}
        for line in _build_env_lines(backend_url, advertise_host, enrollment_token):
            key, _, value = line.partition("=")
            env[key] = value
        args = _install_line_args(backend_url, enrollment_token, use_sudo, str(ca_file) if ca_file else None)
        info("Installing the released node agent through the backend's install line.")
        proc = subprocess.run(["sh", str(path), *args], env=env)  # noqa: S603, S607
    finally:
        path.unlink(missing_ok=True)
    if proc.returncode != 0:
        error("Installing the released node agent failed.")
        return False
    success("Released node agent installed.")
    return True


def _install_line_args(backend_url: str, enrollment_token: str, use_sudo: bool, ca_file: str | None) -> list[str]:
    args = ["--token", enrollment_token, "--backend", backend_url] if enrollment_token else ["--join", backend_url]
    if ca_file:
        args += ["--ca-file", ca_file]
    return args if use_sudo else [*args, "--user"]


def _fetch_install_line(backend_url: str) -> str | None:
    url = f"{backend_url.rstrip('/')}/api/install/faden-agent.sh"
    try:
        resp = httpx.get(url, timeout=30, verify=verify_for(backend_url), follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        error(f"Could not fetch the install line from {url}: {exc}")
        return None
    return resp.text


def _provision_remote_release(
    *,
    remote_host: str,
    backend_url: str,
    advertise_host: str,
    enrollment_token: str,
    use_sudo: bool,
    ca_file: Path | None = None,
) -> bool:
    """Run the backend's install line on *remote_host*, which checks the pinned SHA-256.

    With *ca_file* the install's authority goes along in the script itself: the
    remote host fetches the line through it, and the agent keeps it.
    """
    ssh = shutil.which("ssh")
    if not ssh:
        error("ssh is not installed or not on PATH.")
        return False
    q = shlex.quote
    env = " ".join(
        f"{q(key)}={q(value)}"
        for key, _, value in (line.partition("=") for line in _build_env_lines(backend_url, advertise_host, enrollment_token))
    )
    ca_pem = ca_file.read_text(encoding="ascii").strip() if ca_file else ""
    args = " ".join(q(a) for a in _install_line_args(backend_url, enrollment_token, use_sudo, None))
    line_url = q(backend_url.rstrip("/") + "/api/install/faden-agent.sh")
    if ca_pem:
        trust = f"""CA=$(mktemp)
trap 'rm -f "$TMP" "$CA"' EXIT
cat > "$CA" <<'FADEN_CA'
{ca_pem}
FADEN_CA
curl -fsSL --cacert "$CA" -o "$TMP" {line_url}
"""
        args += ' --ca-file "$CA"'
    else:
        trust = f'curl -fsSL -o "$TMP" {line_url}\n'
    script = f"""set -eu
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT
{trust}env {env} sh "$TMP" {args}
"""
    info(f"Installing the released node agent on remote host '{remote_host}'")
    proc = subprocess.run([ssh, remote_host, "bash -s"], input=script, text=True, capture_output=True)  # noqa: S603
    if proc.returncode != 0:
        error("Remote node-agent provisioning failed.")
        detail = (proc.stderr or proc.stdout or "").strip()
        if detail:
            error(detail)
        return False
    success(f"Remote node agent installed on {remote_host}.")
    return True


def _provision_local(
    *,
    repo_dir: Path,
    backend_url: str,
    advertise_host: str,
    enrollment_token: str,
    use_sudo: bool,
) -> bool:
    """Run the agent from the core checkout's own ``agent/`` (no clone, no pull)."""
    info(f"Provisioning local node agent from {repo_dir}")
    if not _install_agent_dependencies(repo_dir):
        return False

    env_lines = _build_env_lines(backend_url, advertise_host, enrollment_token)

    if platform.system() != "Linux":
        return _start_agent_foreground(repo_dir, env_lines)

    if not use_sudo:
        info("No sudo — starting node agent as user background process.")
        return _start_agent_foreground(repo_dir, env_lines)

    service_file = repo_dir / "deploy" / "systemd" / "faden-agent.service"
    if not service_file.exists():
        error(f"Systemd service file missing: {service_file}")
        return False

    # Pre-check: verify sudo access before attempting systemd install.
    if _run(["sudo", "-n", "true"], check=False) != 0:
        warning("sudo not available — falling back to user background process.")
        return _start_agent_foreground(repo_dir, env_lines)

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        handle.write("\n".join(env_lines))
        handle.write("\n")
        temp_env = Path(handle.name)

    try:
        if _run(["sudo", "install", "-m", "0644", str(service_file), "/etc/systemd/system/faden-agent.service"]) != 0:
            return False
        if _run(["sudo", "install", "-m", "0600", str(temp_env), "/etc/faden-agent.env"]) != 0:
            return False
        if _run(["sudo", "systemctl", "daemon-reload"]) != 0:
            return False
        if _run(["sudo", "systemctl", "enable", "--now", "faden-agent"]) != 0:
            return False
    finally:
        try:
            temp_env.unlink(missing_ok=True)
        except Exception:
            pass

    success("Local node agent installed and started via systemd.")
    return True


def _provision_local_binary(
    *,
    binary: Path,
    agent_dir: Path,
    backend_url: str,
    advertise_host: str,
    enrollment_token: str,
    use_sudo: bool,
) -> bool:
    """Install a pre-built node agent binary (no Python/venv required)."""
    info(f"Found pre-built binary: {binary}")
    env_lines = _build_env_lines(backend_url, advertise_host, enrollment_token)

    is_linux = platform.system() == "Linux"
    install_path = Path("/usr/local/bin/faden-agent") if is_linux else binary

    if is_linux and use_sudo:
        # Install binary to /usr/local/bin
        if _run(["sudo", "-n", "true"], check=False) != 0:
            warning("sudo not available — running binary in foreground.")
            return _start_binary_foreground(binary, env_lines)

        if _run(["sudo", "install", "-m", "0755", str(binary), str(install_path)]) != 0:
            return False
        info(f"Installed binary to {install_path}")

        # Write env file
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write("\n".join(env_lines) + "\n")
            temp_env = Path(handle.name)

        # Install systemd service (use the template from workspace if available)
        service_src = agent_dir / "deploy" / "systemd" / "faden-agent.service"
        if not service_src.exists():
            # Write a minimal service file inline
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".service") as sf:
                sf.write(
                    "[Unit]\n"
                    "Description=fadenstack node agent\n"
                    "After=network-online.target docker.service\n"
                    "Wants=network-online.target\n"
                    # A start that can never succeed must stop retrying and say so.
                    # The agent handles a backend outage itself -- it reconnects with backoff
                    # and never exits for that -- so a failed *start* means something
                    # structural, most often a second agent already holding the state lock.
                    # Without a limit systemd retries every 5s forever while reporting
                    # "activating (auto-restart)", which reads like a slow boot rather than
                    # a wedged service: observed on the DGX head at restart counter 9259,
                    # roughly fifteen hours of looping that nothing surfaced.
                    "StartLimitIntervalSec=300\n"
                    "StartLimitBurst=10\n\n"
                    "[Service]\n"
                    "Type=simple\n"
                    "EnvironmentFile=-/etc/faden-agent.env\n"
                    f"ExecStart={install_path}\n"
                    "Restart=always\n"
                    "RestartSec=5\n\n"
                    "[Install]\n"
                    "WantedBy=multi-user.target\n"
                )
                service_src = Path(sf.name)

        try:
            if _run(["sudo", "install", "-m", "0644", str(service_src), "/etc/systemd/system/faden-agent.service"]) != 0:
                return False
            if _run(["sudo", "install", "-m", "0600", str(temp_env), "/etc/faden-agent.env"]) != 0:
                return False
            if _run(["sudo", "systemctl", "daemon-reload"]) != 0:
                return False
            if _run(["sudo", "systemctl", "enable", "--now", "faden-agent"]) != 0:
                return False
        finally:
            try:
                temp_env.unlink(missing_ok=True)
            except Exception:
                pass

        success("Node agent binary installed and started via systemd.")
        return True

    return _start_binary_foreground(binary, env_lines)


def _start_binary_foreground(binary: Path, env_lines: list[str]) -> bool:
    """Start a pre-built binary as a detached background process."""
    proc_env = {**os.environ}
    for line in env_lines:
        if "=" in line:
            k, v = line.split("=", 1)
            proc_env[k] = v

    if platform.system() == "Windows":
        creationflags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        proc = subprocess.Popen(  # noqa: S603
            [str(binary)],
            env=proc_env,
            creationflags=creationflags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        proc = subprocess.Popen(  # noqa: S603
            [str(binary)],
            env=proc_env,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    success(f"Node agent binary started (PID {proc.pid}).")
    return True


def _start_agent_foreground(repo_dir: Path, env_lines: list[str]) -> bool:
    """Write a .env file and start the node agent as a background process (non-Linux)."""
    # Write env file
    env_file = repo_dir / ".env"
    # Owner-only: it carries the enrollment token.
    write_private(env_file, ("\n".join(env_lines) + "\n").encode("utf-8"))
    info(f"Wrote agent env to {env_file}")

    # Resolve the Python interpreter from the uv venv
    if platform.system() == "Windows":
        venv_python = repo_dir / ".venv" / "Scripts" / "python.exe"
    else:
        venv_python = repo_dir / ".venv" / "bin" / "python"

    if not venv_python.exists():
        # Fallback to system python
        python = shutil.which("python3") or shutil.which("python")
        if not python:
            error("Cannot locate Python to start node agent.")
            return False
        venv_python = Path(python)

    # Build environment with the agent env vars
    proc_env = {**os.environ}
    for line in env_lines:
        if "=" in line:
            k, v = line.split("=", 1)
            proc_env[k] = v

    # When running as a non-root user, override the default state path
    # (/var/lib/...) to a writable location inside the repo dir.
    if "FADEN_NODE_AGENT_STATE_PATH" not in proc_env:
        state_dir = repo_dir / ".agent-state"
        state_dir.mkdir(parents=True, exist_ok=True)
        proc_env["FADEN_NODE_AGENT_STATE_PATH"] = str(state_dir / "state.json")

    # Launch as detached background process
    cmd = [str(venv_python), "-m", "faden_agent"]
    if platform.system() == "Windows":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        creationflags = 0x00000008 | 0x00000200
        proc = subprocess.Popen(  # noqa: S603
            cmd,
            cwd=str(repo_dir),
            env=proc_env,
            creationflags=creationflags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        proc = subprocess.Popen(  # noqa: S603
            cmd,
            cwd=str(repo_dir),
            env=proc_env,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    success(f"Node agent started (PID {proc.pid}).")
    return True


def _provision_remote(
    *,
    remote_host: str,
    repo_url: str,
    branch: str,
    backend_url: str,
    advertise_host: str,
    enrollment_token: str,
    use_sudo: bool,
    workdir_override: str | None,
) -> bool:
    ssh = shutil.which("ssh")
    if not ssh:
        error("ssh is not installed or not on PATH.")
        return False

    workdir = (workdir_override or "").strip() or "/opt/faden_agent"
    script = _build_remote_script(
        repo_url=repo_url,
        branch=branch,
        workdir=workdir,
        backend_url=backend_url,
        advertise_host=advertise_host,
        enrollment_token=enrollment_token,
        use_sudo=use_sudo,
    )
    info(f"Provisioning node agent on remote host '{remote_host}'")
    proc = subprocess.run(  # noqa: S603
        [ssh, remote_host, "bash -s"],
        input=script,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        error("Remote node-agent provisioning failed.")
        if detail:
            error(detail)
        if use_sudo:
            warning(
                "Remote install used sudo. Ensure remote user has passwordless sudo for "
                "install/systemctl, or rerun with --local-node-no-sudo and a writable --local-node-workdir."
            )
        return False

    if proc.stdout.strip():
        info(proc.stdout.strip())
    success(f"Remote node agent provisioned on {remote_host}.")
    return True


def _build_remote_script(
    *,
    repo_url: str,
    branch: str,
    workdir: str,
    backend_url: str,
    advertise_host: str,
    enrollment_token: str,
    use_sudo: bool,
) -> str:
    q = shlex.quote
    use_sudo_int = "1" if use_sudo else "0"
    return f"""set -euo pipefail
REPO_URL={q(repo_url)}
BRANCH={q(branch)}
WORKDIR={q(workdir)}
BACKEND_URL={q(backend_url)}
ADVERTISE_HOST={q(advertise_host)}
ENROLLMENT_TOKEN={q(enrollment_token)}
USE_SUDO={use_sudo_int}

mkdir -p "$(dirname "$WORKDIR")"
if [ -d "$WORKDIR/.git" ]; then
  git -C "$WORKDIR" fetch --all --prune
elif [ -d "$WORKDIR" ] && [ -n "$(ls -A "$WORKDIR" 2>/dev/null)" ]; then
  # Directory exists but is not a git repo (e.g. rsynced without .git/).
  # Skip clone — use the existing source tree as-is.
  echo "Directory $WORKDIR exists without .git — using existing source."
else
  git clone "$REPO_URL" "$WORKDIR"
fi

if [ -d "$WORKDIR/.git" ] && [ -n "$BRANCH" ]; then
  git -C "$WORKDIR" checkout "$BRANCH" || git -C "$WORKDIR" checkout -b "$BRANCH" "origin/$BRANCH" || true
  git -C "$WORKDIR" pull --ff-only origin "$BRANCH" || true
fi

if command -v uv >/dev/null 2>&1; then
  (cd "$WORKDIR/agent" && uv sync)
else
  python3 -m pip install --user -e "$WORKDIR/agent"
fi

if [ "$USE_SUDO" != "1" ]; then
  echo "Skipping systemd install (--local-node-no-sudo)."
  exit 0
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl not available on remote host; skipping service install."
  exit 0
fi

SUDO_BIN="sudo"
if [ "$(id -u)" -eq 0 ]; then
  SUDO_BIN=""
fi

if [ -n "$SUDO_BIN" ]; then
  $SUDO_BIN -n true >/dev/null 2>&1 || {{
    echo "sudo permission check failed (passwordless sudo required for non-interactive install)."
    exit 2
  }}
fi

# Render the systemd service template by replacing @@PLACEHOLDER@@ values
SVC_USER="$(id -un)"
SVC_GROUP="$(id -gn)"
HOME_DIR="$(eval echo ~"$SVC_USER")"
STATE_DIR="$HOME_DIR/.local/share/faden-agent"
[ "$(id -u)" -eq 0 ] && STATE_DIR="/var/lib/faden-agent"
CONFIG_DIR="$HOME_DIR/.config/faden-agent"
# Model store: standard HuggingFace cache in the service user's home, so the
# agent owns it without a root-owned /srv path (see
# faden_agent.config._default_model_store_root).
MODEL_STORE="$HOME_DIR/.cache/huggingface"
PROTECT_HOME="yes"
RW_PATHS="$MODEL_STORE $STATE_DIR"
case "$STATE_DIR" in "$HOME_DIR"*) PROTECT_HOME="read-only"; RW_PATHS="$RW_PATHS $CONFIG_DIR" ;; esac
case "$MODEL_STORE" in "$HOME_DIR"*) PROTECT_HOME="read-only" ;; esac
USER_ENV_FILE="$CONFIG_DIR/agent.env"

# Find the agent binary for ExecStart
AGENT_BIN=""
if [ -x "$WORKDIR/agent/.venv/bin/faden-agent" ]; then
  AGENT_BIN="$WORKDIR/agent/.venv/bin/faden-agent"
elif command -v faden-agent >/dev/null 2>&1; then
  AGENT_BIN="$(command -v faden-agent)"
else
  AGENT_BIN="/usr/local/bin/faden-agent"
fi

TMP_SVC="$(mktemp)"
sed -e "s|@@USER@@|$SVC_USER|g" \
    -e "s|@@GROUP@@|$SVC_GROUP|g" \
    -e "s|@@PROTECT_HOME@@|$PROTECT_HOME|g" \
    -e "s|@@READ_WRITE_PATHS@@|$RW_PATHS|g" \
    -e "s|@@USER_ENV_FILE@@|$USER_ENV_FILE|g" \
    -e "s|^ExecStart=.*|ExecStart=$AGENT_BIN run|" \
    "$WORKDIR/agent/deploy/systemd/faden-agent.service" > "$TMP_SVC"

if [ -n "$SUDO_BIN" ]; then
  $SUDO_BIN install -m 0644 "$TMP_SVC" /etc/systemd/system/faden-agent.service
else
  install -m 0644 "$TMP_SVC" /etc/systemd/system/faden-agent.service
fi
rm -f "$TMP_SVC"

# Ensure state and model directories exist with correct ownership
if [ -n "$SUDO_BIN" ]; then
  $SUDO_BIN mkdir -p "$STATE_DIR" || true
  $SUDO_BIN chown "$SVC_USER:$SVC_GROUP" "$STATE_DIR" || true
  $SUDO_BIN mkdir -p "$MODEL_STORE" || true
  $SUDO_BIN chown "$SVC_USER:$SVC_GROUP" "$MODEL_STORE" || true
else
  mkdir -p "$STATE_DIR" || true
  mkdir -p "$MODEL_STORE" || true
fi

# Derive Loki URL from backend (same host, port 3100)
BACKEND_HOST=$(echo "$BACKEND_URL" | sed -E 's|^https?://([^:/]+).*|\1|')
BACKEND_SCHEME=$(echo "$BACKEND_URL" | sed -E 's|^(https?)://.*|\1|')
LOKI_URL="${{BACKEND_SCHEME}}://${{BACKEND_HOST}}:3100"

TMP_ENV="$(mktemp)"
{{
  printf "FADEN_NODE_AGENT_BACKEND_URL=%s\\n" "$BACKEND_URL"
  printf "FADEN_NODE_AGENT_LOKI_URL=%s\\n" "$LOKI_URL"
  if [ -n "$ADVERTISE_HOST" ]; then
    printf "FADEN_NODE_AGENT_ADVERTISE_HOST=%s\\n" "$ADVERTISE_HOST"
  fi
  if [ -n "$ENROLLMENT_TOKEN" ]; then
    printf "FADEN_NODE_AGENT_ENROLLMENT_TOKEN=%s\\n" "$ENROLLMENT_TOKEN"
  fi
}} >"$TMP_ENV"

if [ -n "$SUDO_BIN" ]; then
  $SUDO_BIN install -m 0600 "$TMP_ENV" /etc/faden-agent.env
  $SUDO_BIN systemctl daemon-reload
  $SUDO_BIN systemctl enable --now faden-agent
else
  install -m 0600 "$TMP_ENV" /etc/faden-agent.env
  systemctl daemon-reload
  systemctl enable --now faden-agent
fi
rm -f "$TMP_ENV"

echo "Node agent service installed and started."
"""


def _install_agent_dependencies(repo_dir: Path) -> bool:
    uv = shutil.which("uv")
    if uv:
        if _run([uv, "sync"], cwd=repo_dir) == 0:
            success("Node agent dependencies installed via uv.")
            return True
        return False

    python = shutil.which("python3") or shutil.which("python")
    if not python:
        error("Neither uv nor python is available for node agent install.")
        return False
    if _run([python, "-m", "pip", "install", "-e", "."], cwd=repo_dir) != 0:
        return False
    success("Node agent installed via pip editable mode.")
    return True


def _run(cmd: list[str], *, cwd: Path | None = None, check: bool = True) -> int:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    proc = subprocess.run(  # noqa: S603
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        env=env,
    )
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        error(f"Command failed: {' '.join(cmd)}")
        if detail:
            error(detail)
    return proc.returncode


def _has_uncommitted_changes(repo_dir: Path) -> bool:
    """Return True if *repo_dir* is a git repo with uncommitted changes."""
    git = shutil.which("git")
    if not git or not (repo_dir / ".git").exists():
        # Not a git repo — be safe and assume there are changes.
        return True
    proc = subprocess.run(  # noqa: S603
        [git, "status", "--porcelain"],
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
    )
    return bool(proc.stdout.strip())
