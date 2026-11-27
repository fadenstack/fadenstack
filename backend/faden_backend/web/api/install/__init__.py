"""The installer the operator actually runs, assembled by the backend.

Why this is a generated endpoint rather than a file in the repo: the whole
point of R-1 is that nothing is assembled by the human.  The script that lands
on the node already knows where this backend is, which architecture it is
running on, which agent version to fetch and what that binary should hash to.
None of those are things the operator should have to look up, and a checksum
in particular is the worst possible thing to ask a person to verify by eye --
so the script verifies it instead.

On ``curl | bash``: we deliberately do not.  The neighbouring products pipe a
script from a third-party host straight into a root shell, which is how they
get a one-line install.  Here the script lands on disk first, so it can be
read before it is run, and it verifies what it downloads.  The trust anchor is
"you are talking to your own backend", which the operator is doing anyway --
it is the machine holding all their credentials.

Binary source: the plan offers both the published release and this backend's
own copy, and the *node* picks by trying them in order.  Deciding here was
wrong on its face -- whether there is internet is a fact about the node, not
about this server, so one answer had to serve an air-gapped rack and a
connected office alike.

Either way the digest is the one this core release pins (agent_release.json,
written from the ``node-agent-v<version>`` release's ``.sha256`` files), so the
verification is identical and the source really is the only difference.  A
download that does not match is refused, wherever it came from, and there is no
way to install without a digest.  A local copy that is not the pinned build --
left from before an upgrade -- is not served; machines are sent the release.
The exception is a developer's own build in a checkout's ``dist/``, which
carries its own computed digest.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import socket
from pathlib import Path

import psutil
from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, Response
from packaging.version import InvalidVersion, Version
from yarl import URL

from faden_backend.services import edge_trust
from faden_backend.settings import DEFAULT_AGENT_BINARY_DIR, settings
from faden_backend.web.origin import public_origin

log = logging.getLogger(__name__)

router = APIRouter(prefix="/install", tags=["install"])

#: Agent version this backend expects.  Bumping it here is what makes a fleet
#: converge, because every fresh install reads it from the running backend.
AGENT_VERSION = "0.2.12"


def newer_agent_version(running: str | None) -> str | None:
    """:data:`AGENT_VERSION` when a machine runs an older agent, else None.

    Shown, never applied: an agent is updated by re-running the install line
    on the machine, in a window its operator picks -- not under a production
    load by itself.
    """
    try:
        return AGENT_VERSION if running and Version(running) < Version(AGENT_VERSION) else None
    except InvalidVersion:
        return None


#: Where published binaries live when this backend has no local copy: the
#: release .github/workflows/node-agent-release.yml makes for a
#: ``node-agent-v<version>`` tag. (This pointed at the retired
#: faden-node-agent repository, under a tag neither repository used, so
#: every install depended on a local copy.)
RELEASE_URL = (
    "https://github.com/fadenstack/fadenstack/releases/download"
    "/node-agent-v{version}/faden-agent-{platform}"
)

#: Platforms we publish, keyed as ``uname -s``/``uname -m`` reports them.
PLATFORMS = {
    "linux-aarch64": "linux-aarch64",
    "linux-arm64": "linux-aarch64",
    "linux-x86_64": "linux-x86_64",
    "linux-amd64": "linux-x86_64",
    # Apple Silicon only. Intel Macs are refused by the script itself, before
    # it asks for a plan, so they get a reason rather than "no build".
    "darwin-arm64": "macos-arm64",
}

#: The oldest macOS the agent is certified on (Apple Silicon, macOS 26 and 27).
MIN_MACOS_MAJOR = 26

#: A Mac release signed with a Developer ID also carries the binary in a
#: notarized flat pkg (agent/macos-package.sh), pinned as ``macos-arm64-pkg``.
#: The pkg only stages the binary, root's, at :data:`MAC_PKG_STAGED`; the
#: install script verifies it against the binary's own pin, joins with it and
#: removes it. Without that pin the Mac installs the bare binary, as before.
MAC_PKG_BUILD = "macos-arm64"
MAC_PKG_PIN = "macos-arm64-pkg"
MAC_PKG_ID = "com.fadenstack.agent.pkg"
MAC_PKG_LIBRARY = "/Library/Fadenstack"
MAC_PKG_STAGING_DIR = f"{MAC_PKG_LIBRARY}/staging"
MAC_PKG_STAGING = f"{MAC_PKG_STAGING_DIR}/"


def _source_checkout_binary_dir() -> Path | None:
    """The agent's own build output, when this backend runs from a checkout.

    ``sh build-binary.sh`` writes into ``agent/dist/``.  That
    used to have to be copied into ``agent_binary_dir`` by hand: two copies
    of a 14MB binary, and a standing chore to keep them identical that
    nothing enforced -- so the one the backend served drifted behind the one
    that was built, silently, because both files exist and neither says
    which is current.

    Reading the build output directly means there is one file.  An installed
    backend has no checkout above it, so this finds nothing there.
    """
    candidate = Path(__file__).resolve().parents[5] / "agent" / "dist"
    return candidate if candidate.is_dir() else None


def _local_binary_dirs() -> list[Path]:
    """Where to look for a local agent copy, in priority order.

    An operator who set ``agent_binary_dir`` gets exactly that directory:
    they placed those binaries deliberately, and a checkout that happens to
    be on the same disk must not quietly outrank them.  Left at the default,
    a checkout's ``dist/`` comes first instead -- running from source, the
    binary you just built is the one you meant.
    """
    configured = Path(settings.agent_binary_dir)
    if settings.agent_binary_dir != DEFAULT_AGENT_BINARY_DIR:
        return [configured]

    checkout = _source_checkout_binary_dir()
    return [checkout, configured] if checkout else [configured]


def local_binary(platform: str) -> Path | None:
    """The local copy for *platform*, when this deployment carries one."""
    for directory in _local_binary_dirs():
        candidate = directory / f"faden-agent-{platform}"
        if candidate.is_file():
            return candidate
    return None


def binary_digest(path: Path) -> str:
    """The sha256 of the file at *path*, as the install script checks it."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_pin() -> dict[str, str]:
    """The pinned digest per build, when the pin is for :data:`AGENT_VERSION`.

    A pin for another version is as good as none: handing out its digests
    for this version's URL would make every install fail, so it is ignored
    (and :func:`install_plan` says why there is no build).
    """
    try:
        pin = json.loads(Path(__file__).with_name("agent_release.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(pin, dict) or pin.get("version") != AGENT_VERSION:
        return {}
    digests = pin.get("sha256")
    if not isinstance(digests, dict):
        return {}
    return {
        build: digest.lower()
        for build, digest in digests.items()
        if isinstance(digest, str) and len(digest) == 64 and all(c in "0123456789abcdefABCDEF" for c in digest)
    }


def pinned_digest(build: str) -> str | None:
    """What *build* of the released agent must hash to; None when this core pins none."""
    return _load_pin().get(build)


def _from_checkout(path: Path) -> bool:
    """Whether *path* is a developer's own build in the checkout's ``dist/``."""
    checkout = _source_checkout_binary_dir()
    return checkout is not None and path.parent == checkout


def servable_binary(build: str) -> tuple[Path, str] | None:
    """This server's copy of *build* and its digest, when it may be handed out.

    The pinned build, or a checkout's own build (which differs from the release
    by construction and is the one a developer meant to test). Any other copy --
    left behind by an upgrade, or swapped -- is not served.
    """
    local = local_binary(build)
    if local is None:
        return None
    digest = binary_digest(local)
    if _from_checkout(local) or digest == pinned_digest(build):
        return local, digest
    return None


def _origin(request: Request) -> str:
    """This backend, as the caller reached it (:func:`public_origin`).

    Taken from the request rather than configuration precisely because the
    node has to reach the same address the operator's browser did.  A
    configured value would be right in exactly the deployments that do not
    need this endpoint.
    """
    return public_origin(request)


def _is_loopback(host: str) -> bool:
    """Whether *host* only ever means "this machine".

    Splitting on ``:`` to drop a port is wrong for IPv6, where the colons are
    the address: ``::1`` came back as not-loopback, which is exactly backwards
    and would have let the console hand out ``http://[::1]:8000``.
    """
    bare = host.strip().lower()
    if bare.startswith("["):  # [::1]:8000
        bare = bare[1:].split("]", 1)[0]
    elif bare.count(":") == 1:  # host:port
        bare = bare.split(":", 1)[0]
    if bare == "localhost":
        return True
    try:
        return ipaddress.ip_address(bare).is_loopback
    except ValueError:
        return False


def reachable_origins() -> list[dict[str, str]]:
    """Addresses of this backend that another machine could actually use.

    Read from the host's own interfaces rather than from the request. The
    operator's browser is frequently somewhere the node is not -- at
    ``localhost:5173`` in front of a dev proxy, or through an SSH tunnel --
    and the console was handing out that address as the one to install
    against. A machine told to fetch the agent from ``localhost`` fetches it
    from itself and finds nothing there.
    """
    # A deployment: the CLI noted this host's addresses and ports (tls/edge.json);
    # from inside the container the interfaces are the container's own, and the
    # backend's port is not the one nginx publishes.
    noted = edge_trust.host_urls()
    if noted:
        return [{"url": url, "interface": "host"} for url in noted]

    port = settings.port
    found: list[dict[str, str]] = []
    try:
        for name, addresses in psutil.net_if_addrs().items():
            for address in addresses:
                if address.family != socket.AF_INET:
                    continue
                host = address.address
                if _is_loopback(host) or host.startswith("169.254."):
                    continue
                found.append({"url": f"http://{host}:{port}", "interface": name})
    except Exception:  # noqa: BLE001 - never block onboarding on inventory
        return []

    # A workstation has a pile of these -- VirtualBox host-only, WSL's
    # vEthernet, Docker's bridge, a VPN's CGNAT range -- and every one of them
    # is an address the node cannot use. None can be ruled out from here, so
    # order them and let the operator see which is which rather than picking
    # one silently and being wrong.
    def rank(entry: dict[str, str]) -> tuple[int, str]:
        label = entry["interface"].lower()
        virtual = any(
            token in label
            for token in ("virtualbox", "host-only", "vethernet", "docker", "vmware", "loopback", "tailscale")
        )
        cgnat = entry["url"].startswith("http://100.")
        return (1 if (virtual or cgnat) else 0, entry["url"])

    return sorted(found, key=rank)


@router.get("/address")
async def install_address(request: Request) -> dict[str, object]:
    """Where a machine being added should be pointed.

    The console used to build its install command from the browser's own
    URL, on the assumption that the operator and the node see the backend
    the same way. They frequently do not, and the failure is silent until
    someone runs the command on a real machine.
    """
    seen_as = _origin(request)
    candidates = reachable_origins()
    usable = not _is_loopback(URL(seen_as).host or "")
    return {
        # Prefer what the caller used when it can work; it accounts for
        # reverse proxies and real hostnames, which interfaces cannot.
        "url": seen_as if usable else (candidates[0]["url"] if candidates else seen_as),
        "seen_as": seen_as,
        "candidates": candidates,
        "request_origin_is_reachable": usable,
        # Set when HTTPS is served with a certificate of the install's own
        # authority: a new machine has to fetch the authority first and check
        # it against this before it trusts anything from here.
        "ca_sha256": edge_trust.ca_sha256() if edge_trust.needs_ca() else None,
    }


@router.get("/ca.crt")
async def install_ca() -> Response:
    """The install's certificate authority, for a machine about to join.

    Unauthenticated: a certificate authority's certificate is public. What
    makes it safe to use is the fingerprint the console shows next to the
    install line, which the line checks before trusting it.
    """
    pem = edge_trust.ca_pem()
    if pem is None:
        return Response(status_code=404)
    return Response(
        pem,
        media_type="application/x-pem-file",
        headers={"Content-Disposition": 'attachment; filename="ca.crt"'},
    )


def render_installer(origin: str) -> str:
    """The POSIX-sh installer, with this deployment's facts already in it."""
    # Written as plain sh, not bash: a DGX OS image is not guaranteed to have
    # anything more, and the script has to run before we have installed
    # anything at all.
    return f"""#!/bin/sh
# Fadenstack node agent installer.
#
# Generated by {origin} for that backend specifically -- the address, the
# agent version and the expected digest are already filled in, so there is
# nothing here for you to look up.
#
# It downloads the agent, checks the digest it was told to expect, installs
# it, and then either asks to join (and waits for someone to approve in the
# browser) or enrolls with a token you passed.
#
# Usage:
#   sudo sh faden-agent.sh --join [{origin}]     system service
#   sh faden-agent.sh --join [{origin}]          no sudo: user service
#   sh faden-agent.sh --backend {origin} --token <token>
#   ... --ca-file ca.crt     the backend's own authority, when its HTTPS certificate is of its making
#
# Run it as root and the agent becomes a system service. Run it as yourself,
# without passwordless sudo, and it installs into ~/.local/bin and runs as a
# systemd user service -- no password asked for at any point.
#
# On a Mac (Apple Silicon, macOS {MIN_MACOS_MAJOR} or newer) it must run with sudo: the agent
# becomes a system LaunchDaemon under its own hidden account, _fadenagent.

set -eu

BACKEND="{origin}"
AGENT_VERSION="{AGENT_VERSION}"
MODE="join"
TOKEN=""
INSTALL_PATH="/usr/local/bin/faden-agent"
INSTALL_PATH_SET=0
FORCE_USER=0
CA_FILE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --join)
      MODE="join"
      if [ $# -gt 1 ] && [ "${{2#--}}" = "$2" ]; then BACKEND="$2"; shift; fi
      ;;
    --backend) BACKEND="$2"; shift ;;
    --token) MODE="token"; TOKEN="$2"; shift ;;
    --install-path) INSTALL_PATH="$2"; INSTALL_PATH_SET=1; shift ;;
    --user) FORCE_USER=1 ;;
    --ca-file) CA_FILE="$2"; shift ;;
    -h|--help) sed -n '2,23p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

# -- the backend's own authority --------------------------------------------
# A backend on HTTPS with a certificate from the install's own authority. The
# console's line fetched the authority, checked it against the fingerprint it
# showed, and hands it over here: every request to the backend is verified
# against it, and the agent keeps it. The release download is not, since
# GitHub's certificate is a public one and --cacert would replace the roots.
if [ -n "$CA_FILE" ]; then
  if [ ! -f "$CA_FILE" ]; then
    echo "ERROR: $CA_FILE does not exist." >&2
    exit 1
  fi
  CA_FILE="$(cd "$(dirname "$CA_FILE")" && pwd)/$(basename "$CA_FILE")"
fi
backend_curl() {{
  if [ -n "$CA_FILE" ]; then curl --cacert "$CA_FILE" "$@"; else curl "$@"; fi
}}
fetch() {{
  case "$2" in
    "$BACKEND"*) backend_curl -fsSL -o "$1" "$2" ;;
    *) curl -fsSL -o "$1" "$2" ;;
  esac
}}

# -- which machine ----------------------------------------------------------
# Known before anything else, so a machine that cannot run the agent is told
# so before it is asked for a password or downloads anything.
OS=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m)
if [ "$OS" = darwin ]; then
  # A shell under Rosetta reports x86_64 on an Apple Silicon Mac.
  if [ "$ARCH" = x86_64 ] && [ "$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)" = 1 ]; then
    ARCH=arm64
  fi
  if [ "$ARCH" != arm64 ]; then
    echo "ERROR: this is an Intel Mac. The Fadenstack agent needs a Mac with Apple Silicon." >&2
    exit 1
  fi
  MACOS=$(sw_vers -productVersion 2>/dev/null || echo 0)
  MACOS_MAJOR=${{MACOS%%.*}}
  case "$MACOS_MAJOR" in ''|*[!0-9]*) MACOS_MAJOR=0 ;; esac
  if [ "$MACOS_MAJOR" -lt {MIN_MACOS_MAJOR} ]; then
    echo "ERROR: this Mac runs macOS $MACOS. The Fadenstack agent needs macOS {MIN_MACOS_MAJOR} or newer." >&2
    exit 1
  fi
  # A Mac's agent is always a system service under its own account: there is
  # no per-user variant to fall back to.
  if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: on a Mac the agent installs as a system service. Run this with sudo:" >&2
    # With the CA the console's line fetched: dropped, the rerun cannot reach an HTTPS backend.
    HINT_CA=""
    if [ -n "$CA_FILE" ]; then HINT_CA=" --ca-file '$CA_FILE'"; fi
    echo "       sudo sh faden-agent.sh --join $BACKEND$HINT_CA" >&2
    exit 1
  fi
  if [ "$FORCE_USER" = 1 ] || [ "$INSTALL_PATH_SET" = 1 ]; then
    echo "ERROR: --user and --install-path are not available on a Mac." >&2
    exit 2
  fi
fi

# -- with what privilege ----------------------------------------------------
# Decided once, up front, rather than discovered halfway: a run that could not
# get root used to install the binary and then die writing the service unit,
# leaving an agent that ran only while somebody's shell stayed open.
ROOTLESS=0
if [ "$(id -u)" -ne 0 ]; then
  if [ "$FORCE_USER" = 1 ] || ! sudo -n true 2>/dev/null; then
    ROOTLESS=1
    if [ "$INSTALL_PATH_SET" = 0 ]; then
      INSTALL_PATH="$HOME/.local/bin/faden-agent"
    fi
    echo "  No root here, so installing for $(id -un) only: no password needed."
    echo
  fi
fi

# -- which build do we need -------------------------------------------------
PLATFORM_KEY="${{OS}}-${{ARCH}}"

echo "  Machine:  $PLATFORM_KEY"
echo "  Fadenstack: $BACKEND"
echo

# The backend knows which of its builds fits this machine, and what that build
# should hash to.  Asking it beats making the operator choose.
PLAN=$(backend_curl -fsSL "$BACKEND/api/install/plan?platform=$PLATFORM_KEY") || {{
  echo "ERROR: could not reach $BACKEND" >&2
  exit 1
}}

# JSON is allowed to escape forward slashes, and this backend's encoder does:
# every URL arrives as "https:\\/\\/host\\/path". Pulling one out with sed and
# handing it to curl gave "URL using bad/illegal format" -- from both sources,
# so the installer could not fetch the agent at all.
PLAN=$(printf '%s' "$PLAN" | sed 's|\\\\/|/|g')

URL=$(printf '%s' "$PLAN" | sed -n 's/.*"url"[[:space:]]*:[[:space:]]*"\\([^"]*\\)".*/\\1/p')
FALLBACK=$(printf '%s' "$PLAN" | sed -n 's/.*"fallback_url"[[:space:]]*:[[:space:]]*"\\([^"]*\\)".*/\\1/p')
SHA=$(printf '%s' "$PLAN" | sed -n 's/.*"sha256"[[:space:]]*:[[:space:]]*"\\([^"]*\\)".*/\\1/p')

if [ -z "$URL" ]; then
  echo "ERROR: this backend publishes no agent build for $PLATFORM_KEY." >&2
  echo "       $PLAN" >&2
  exit 1
fi

# -- fetch and verify -------------------------------------------------------
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

echo "  Downloading the agent..."
if ! fetch "$TMP/faden-agent" "$URL"; then
  if [ -n "$FALLBACK" ]; then
    # No internet here, or the release is unreachable. Fadenstack carries its
    # own copy, and it is a machine this node can obviously reach -- it is
    # the one it is about to enrol with.
    echo "  Could not reach $URL"
    echo "  Falling back to Fadenstack's own copy..."
    fetch "$TMP/faden-agent" "$FALLBACK" || {{
      echo "ERROR: could not download the agent from either source." >&2
      exit 1
    }}
  else
    echo "ERROR: could not download the agent from $URL" >&2
    echo "       This Fadenstack carries no local copy to fall back to." >&2
    exit 1
  fi
fi

# No digest, no install: the build this core release hands out is pinned, and
# an installer that skipped the check would run whatever it was served.
case "$SHA" in
  *[!0-9a-fA-F]*|"")
    echo "ERROR: this backend gave no valid SHA-256 for the agent, so it cannot be verified." >&2
    echo "       Refusing to install. Plan: $PLAN" >&2
    exit 1 ;;
esac
if [ "${{#SHA}}" -ne 64 ]; then
  echo "ERROR: this backend gave no valid SHA-256 for the agent, so it cannot be verified." >&2
  echo "       Refusing to install. Plan: $PLAN" >&2
  exit 1
fi
echo "  Checking it is the build this backend expects..."
# The tool is chosen before anything is hashed, and its own exit status is the
# one checked: piped into cut, a failing sha256sum used to read as success.
# macOS has a BSD sha256sum with other options, so a Mac uses shasum.
if [ "$OS" != darwin ] && command -v sha256sum >/dev/null 2>&1; then
  HASH="sha256sum"
elif command -v shasum >/dev/null 2>&1; then
  HASH="shasum -a 256"
else
  echo "ERROR: neither sha256sum nor shasum is installed, so the agent cannot be verified." >&2
  echo "       Refusing to install." >&2
  exit 1
fi
if ! HASHED=$($HASH "$TMP/faden-agent"); then
  echo "ERROR: $HASH failed, so the agent cannot be verified. Refusing to install." >&2
  exit 1
fi
ACTUAL=${{HASHED%% *}}
SHA=$(printf '%s' "$SHA" | tr '[:upper:]' '[:lower:]')
case "$ACTUAL" in
  *[!0-9a-f]*|"")
    echo "ERROR: $HASH gave no SHA-256 ($HASHED). Refusing to install." >&2
    exit 1 ;;
esac
if [ "${{#ACTUAL}}" -ne 64 ]; then
  echo "ERROR: $HASH gave no SHA-256 ($HASHED). Refusing to install." >&2
  exit 1
fi
if [ "$ACTUAL" != "$SHA" ]; then
  echo "ERROR: the downloaded agent does not match the expected SHA-256; nothing was installed." >&2
  echo "       expected $SHA" >&2
  echo "       got      $ACTUAL" >&2
  exit 1
fi
echo "  Verified."

# -- install ----------------------------------------------------------------
# A Mac's agent places itself: it copies this verified file into
# /Library/Fadenstack/agent/<version>/, checks the copy against it, and links
# /usr/local/bin/faden-agent to it, so the command and the service are always
# the same build. Run as root (checked above), straight from the download.
if [ "$OS" = darwin ]; then
  chmod 0755 "$TMP/faden-agent"
  AGENT="$TMP/faden-agent"
  # A release signed with a Developer ID also offers the agent as a notarized
  # pkg. Installer checks it and stages the binary, root's; the staged copy
  # must be the very build verified above, and it is what joins. The pkg runs
  # no scripts. When the pkg cannot be fetched, the verified download is used.
  # (Each key is preceded by "_", so the url and sha256 patterns above never match these.)
  PKG_URL=$(printf '%s' "$PLAN" | sed -n 's/.*"package_url"[[:space:]]*:[[:space:]]*"\\([^"]*\\)".*/\\1/p')
  PKG_SHA=$(printf '%s' "$PLAN" | sed -n 's/.*"package_sha256"[[:space:]]*:[[:space:]]*"\\([^"]*\\)".*/\\1/p')
  PKG_BIN=$(printf '%s' "$PLAN" | sed -n 's/.*"package_binary"[[:space:]]*:[[:space:]]*"\\([^"]*\\)".*/\\1/p')
  # A directory root runs from must be root's: owned by root, a real directory,
  # writable by no one else. Absent is fine (Installer makes it, root's).
  root_only_dir() {{
    [ -e "$1" ] || [ -L "$1" ] || return 0
    [ -d "$1" ] && [ ! -L "$1" ] || return 1
    OWNER_MODE=$(stat -f '%u %Lp' "$1") || return 1
    [ "${{OWNER_MODE%% *}}" = 0 ] || return 1
    [ $((0${{OWNER_MODE#* }} & 022)) -eq 0 ]
  }}
  if [ -n "$PKG_URL" ]; then
    case "$PKG_SHA" in
      *[!0-9a-f]*|"")
        echo "ERROR: this backend offers a package without a valid SHA-256. Refusing to install." >&2
        exit 1 ;;
    esac
    case "${{PKG_BIN#{MAC_PKG_STAGING}}}" in
      "$PKG_BIN"|""|*/*|*..*)
        echo "ERROR: this backend's package stages the agent outside {MAC_PKG_STAGING}." >&2
        echo "       Refusing to install." >&2
        exit 1 ;;
    esac
    echo "  Downloading the signed package..."
    if fetch "$TMP/agent.pkg" "$PKG_URL"; then
      if ! PKG_HASHED=$($HASH "$TMP/agent.pkg") || [ "${{PKG_HASHED%% *}}" != "$PKG_SHA" ]; then
        echo "ERROR: the downloaded package does not match the expected SHA-256; nothing was installed." >&2
        exit 1
      fi
      for dir in {MAC_PKG_LIBRARY} {MAC_PKG_STAGING_DIR}; do
        root_only_dir "$dir" || {{
          echo "ERROR: $dir is not root's alone (owner root, writable by no one else)." >&2
          echo "       Refusing to install." >&2
          exit 1
        }}
      done
      # From here on, the staged copy and the package receipt go when this script does.
      cleanup_pkg() {{
        rm -rf "$TMP"; rm -f "$PKG_BIN"
        rmdir {MAC_PKG_STAGING_DIR} 2>/dev/null || true
        pkgutil --forget {MAC_PKG_ID} >/dev/null 2>&1 || true
      }}
      trap cleanup_pkg EXIT
      if ! installer -pkg "$TMP/agent.pkg" -target / >/dev/null; then
        echo "ERROR: macOS Installer refused the agent package; nothing was joined." >&2
        exit 1
      fi
      # Run nothing from the staging directory: copy the staged binary into this
      # script's own directory (mktemp -d, root's, 0700), and check and run that copy.
      if ! root_only_dir {MAC_PKG_LIBRARY} || ! root_only_dir {MAC_PKG_STAGING_DIR} \
          || [ -L "$PKG_BIN" ] || [ ! -f "$PKG_BIN" ] || ! cp "$PKG_BIN" "$TMP/faden-agent-pkg"; then
        echo "ERROR: the package staged no plain file in a root-only directory. Refusing to install." >&2
        exit 1
      fi
      if ! STAGED=$($HASH "$TMP/faden-agent-pkg") || [ "${{STAGED%% *}}" != "$ACTUAL" ]; then
        echo "ERROR: the package staged another build than the one verified. Refusing to install." >&2
        exit 1
      fi
      chmod 0755 "$TMP/faden-agent-pkg"
      AGENT="$TMP/faden-agent-pkg"
      echo "  Package verified and installed."
    else
      echo "  Could not reach $PKG_URL; installing the verified agent itself."
    fi
  fi
  set --
  if [ -n "$CA_FILE" ]; then
    set -- --ca-file "$CA_FILE"
  fi
  if [ "$MODE" = "token" ]; then
    FADEN_NODE_AGENT_BACKEND_URL="$BACKEND" \\
    FADEN_NODE_AGENT_ENROLLMENT_TOKEN="$TOKEN" \\
      "$AGENT" start "$@"
  else
    "$AGENT" join "$@" "$BACKEND"
  fi
  exit 0
fi

# Two forms, because the enrol path below passes the configuration through the
# environment: sudo needs -E to keep it, and running as root must not be handed
# a bare "-E" to execute. It was, and every token enrolment run as root -- the
# ordinary case for an installer -- died on "-E: not found" after the agent had
# already been installed, so it looked like the agent was broken.
#
# Which of the two is needed depends on where the agent is going. Installing
# into /usr/local/bin needs root; installing into a directory this user
# already owns does not, and asking for a password to write a file you own is
# how a rootless install failed on a machine whose sudo needs one.
INSTALL_DIR=$(dirname "$INSTALL_PATH")
mkdir -p "$INSTALL_DIR" 2>/dev/null || true
if [ "$(id -u)" -eq 0 ] || [ "$ROOTLESS" = 1 ]; then
  SUDO=""
  SUDO_E=""
elif [ -w "$INSTALL_DIR" ]; then
  SUDO=""
  # A system service still needs root whatever the binary's path.
  SUDO_E="sudo -E"
else
  SUDO="sudo"
  SUDO_E="sudo -E"
fi
$SUDO install -m 0755 "$TMP/faden-agent" "$INSTALL_PATH"
echo "  Installed $INSTALL_PATH"
case ":$PATH:" in
  *":$INSTALL_DIR:"*) ;;
  # Silence here means "command not found" on the very next line of the
  # instructions, for a file that installed perfectly well.
  *) echo "  NOTE: $INSTALL_DIR is not on your PATH." ;;
esac
echo

# -- join or enroll ---------------------------------------------------------
# Without root the service is a systemd user service, which needs none.
SCOPE=""
if [ "$ROOTLESS" = 1 ]; then
  SCOPE="--user"
fi
# The options are all read, so the positional parameters are free to carry
# the authority's path as one word, whatever it contains.
set --
if [ -n "$CA_FILE" ]; then
  set -- --ca-file "$CA_FILE"
fi
if [ "$MODE" = "token" ]; then
  FADEN_NODE_AGENT_BACKEND_URL="$BACKEND" \\
  FADEN_NODE_AGENT_ENROLLMENT_TOKEN="$TOKEN" \\
    $SUDO_E "$INSTALL_PATH" start $SCOPE "$@"
else
  "$INSTALL_PATH" join $SCOPE "$@" "$BACKEND"
fi
"""


@router.get("/faden-agent.sh", response_class=PlainTextResponse)
async def installer_script(request: Request) -> PlainTextResponse:
    """The installer, generated for this backend.

    Unauthenticated: it contains no secret.  Everything it knows is the
    address the caller already reached and which builds exist.
    """
    return PlainTextResponse(
        render_installer(_origin(request)),
        media_type="text/x-shellscript",
        headers={"Content-Disposition": 'attachment; filename="faden-agent.sh"'},
    )


@router.get("/plan")
async def install_plan(request: Request, platform: str = "linux-x86_64") -> dict[str, str | None]:
    """Where to get the agent for *platform*, and what it should hash to.

    Answering this here is what lets the script stay dumb and the operator
    stay uninvolved: the backend decides between its own copy and the
    published release, and the digest it returns is checked either way.
    """
    build = PLATFORMS.get(platform.strip().lower())
    if build is None:
        return {
            "platform": platform,
            "url": None,
            "sha256": None,
            "reason": f"No agent build is published for {platform}.",
        }

    release_url = RELEASE_URL.format(version=AGENT_VERSION, platform=build)
    pinned = pinned_digest(build)
    served = servable_binary(build)
    fallback_url = f"{_origin(request)}/api/install/binary/{build}"

    if served is not None and _from_checkout(served[0]):
        # A developer's own build: it differs from the release by construction,
        # so it is fetched from this server and checked against its own digest.
        return {
            "platform": build,
            "url": fallback_url,
            "fallback_url": None,
            "sha256": served[1],
            "source": "checkout",
        }

    if pinned is None:
        return {
            "platform": build,
            "url": None,
            "sha256": None,
            "reason": f"This Fadenstack pins no agent {AGENT_VERSION} build for {build}, so none can be verified.",
        }

    if served is None:
        # Release only. The digest is the pinned one -- the node checks the
        # download against it and refuses a mismatch.
        local = local_binary(build)
        if local is not None:
            log.warning(
                "The agent copy %s is not agent %s's pinned build; machines are sent the release. "
                "Replace it with the release's %s, or delete it.",
                local, AGENT_VERSION, build,
            )
        return {
            "platform": build,
            "url": release_url,
            "fallback_url": None,
            "sha256": pinned,
            "source": "release",
            **_mac_package(build),
        }

    # Both are available, and the node picks by trying: whether there is
    # internet is a fact about the *node*, not about this server. The release
    # goes first because it is a CDN and this is somebody's server; the
    # fallback makes the same command work with no internet at all. The digest
    # is the pinned one either way.
    return {
        "platform": build,
        "url": release_url,
        "fallback_url": fallback_url,
        "sha256": pinned,
        "source": "release+backend",
        **_mac_package(build),
    }


def _mac_package(build: str) -> dict[str, str]:
    """The notarized pkg's fields for a Mac plan, when this core pins one; else none.

    Only beside the release: a checkout's own build has no pkg, and the pkg is
    fetched from the release alone -- a Mac that cannot reach it installs the
    verified binary instead, as the script says.
    """
    if build != MAC_PKG_BUILD:
        return {}
    digest = pinned_digest(MAC_PKG_PIN)
    if digest is None:
        return {}
    return {
        "package_url": RELEASE_URL.format(version=AGENT_VERSION, platform=f"{build}.pkg"),
        "package_sha256": digest,
        "package_binary": f"{MAC_PKG_STAGING}faden-agent-{AGENT_VERSION}",
    }


@router.get("/binary/{build}")
async def install_binary(build: str) -> Response:
    """Serve this deployment's own copy of the agent, when it has one."""
    if build not in set(PLATFORMS.values()):
        return Response(status_code=404)
    served = servable_binary(build)
    if served is None:
        return Response(status_code=404)
    return Response(
        content=served[0].read_bytes(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="faden-agent-{build}"'},
    )
