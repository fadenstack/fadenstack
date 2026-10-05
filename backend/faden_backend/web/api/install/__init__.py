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

Either way the digest comes from here, so the verification is identical and
the source really is the only difference.  That is also what makes preferring
the release safe: a download that does not match the build this deployment
expects is refused, wherever it came from.  The one exception is a local copy
that is not this version's build at all -- left from before an upgrade -- which
is set aside for the release when the release's own digest says so.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import socket
import time
from pathlib import Path

import httpx
import psutil
from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, Response
from packaging.version import InvalidVersion, Version
from yarl import URL

from faden_backend.services import edge_trust
from faden_backend.services.tls import default_httpx_verify
from faden_backend.settings import DEFAULT_AGENT_BINARY_DIR, settings

log = logging.getLogger(__name__)

router = APIRouter(prefix="/install", tags=["install"])

#: Agent version this backend expects.  Bumping it here is what makes a fleet
#: converge, because every fresh install reads it from the running backend.
AGENT_VERSION = "0.2.6"


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
    "darwin-arm64": "macos-universal",
    "darwin-x86_64": "macos-universal",
}


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


#: (version, build) -> (digest or None, monotonic expiry). An answer is kept an
#: hour, a failure to get one a few minutes: an offline server asks rarely.
_published_digests: dict[tuple[str, str], tuple[str | None, float]] = {}
_DIGEST_TTL_SEC = 3600.0
_NO_DIGEST_TTL_SEC = 300.0


async def published_digest(build: str) -> str | None:
    """The digest the release publishes for this version's *build*; None when it cannot be read.

    The release workflow puts ``<binary>.sha256`` beside each binary. Only
    asked when this server holds its own copy, to find out whether that copy
    is this version's build (see :func:`install_plan`).
    """
    key = (AGENT_VERSION, build)
    cached = _published_digests.get(key)
    if cached is not None and cached[1] > time.monotonic():
        return cached[0]
    digest: str | None = None
    url = RELEASE_URL.format(version=AGENT_VERSION, platform=build) + ".sha256"
    try:
        async with httpx.AsyncClient(
            verify=default_httpx_verify(), timeout=3.0, follow_redirects=True,
        ) as client:
            resp = await client.get(url)
        if resp.status_code == 200:
            first = resp.text.split()[0] if resp.text.split() else ""
            digest = first.lower() if len(first) == 64 else None
    except httpx.HTTPError:
        digest = None
    ttl = _DIGEST_TTL_SEC if digest else _NO_DIGEST_TTL_SEC
    _published_digests[key] = (digest, time.monotonic() + ttl)
    return digest


def _origin(request: Request) -> str:
    """This backend, as the caller reached it.

    Taken from the request rather than configuration precisely because the
    node has to reach the same address the operator's browser did.  A
    configured value would be right in exactly the deployments that do not
    need this endpoint.
    """
    forwarded_host = request.headers.get("x-forwarded-host")
    forwarded_proto = request.headers.get("x-forwarded-proto")
    if forwarded_host:
        return f"{forwarded_proto or request.url.scheme}://{forwarded_host}"
    return str(request.base_url).rstrip("/")


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
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
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
OS=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m)
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

if [ -n "$SHA" ]; then
  echo "  Checking it is the build this backend expects..."
  ACTUAL=$(sha256sum "$TMP/faden-agent" 2>/dev/null | cut -d' ' -f1) \\
    || ACTUAL=$(shasum -a 256 "$TMP/faden-agent" | cut -d' ' -f1)
  if [ "$ACTUAL" != "$SHA" ]; then
    echo "ERROR: the download does not match the expected digest." >&2
    echo "       expected $SHA" >&2
    echo "       got      $ACTUAL" >&2
    exit 1
  fi
  echo "  Verified."
else
  # Say so rather than implying a check happened.  A missing digest is a
  # deployment that has not published one, not a passed verification.
  echo "  NOTE: this backend published no digest, so nothing was verified."
fi

# -- install ----------------------------------------------------------------
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

    local = local_binary(build)
    release_url = RELEASE_URL.format(version=AGENT_VERSION, platform=build)

    if local is None:
        # Nothing shipped with this deployment, so the release is the only
        # source -- and we cannot state a digest for bytes we have never
        # seen. The script says so rather than implying a check happened.
        return {
            "platform": build,
            "url": release_url,
            "fallback_url": None,
            "sha256": None,
            "source": "release",
        }

    # Both are available, and the node picks by trying. Whether there is
    # internet is a fact about the *node*, not about this server, so deciding
    # here gave one answer to an air-gapped rack and a connected office alike.
    #
    # The release goes first because it is a CDN and this is somebody's
    # server; the fallback is what makes the same command work with no
    # internet at all.
    #
    # The digest is this backend's own copy either way. A release that does
    # not match it is not the build this deployment expects and the script
    # refuses it -- which is the point of pinning: the source stops mattering.
    #
    # Unless the copy is not this version's build at all. Nothing replaces
    # it when the server is upgraded, so after an upgrade the pin held
    # machines on the agent before: the new release failed the check ("does
    # not match the expected digest") and the update stopped there, and a
    # machine without internet got the old copy from the fallback. Where the
    # release's own digest can be read and the copy is not that build, the
    # copy is set aside. With no internet the copy stays authoritative: it is
    # all there is.
    local_digest = binary_digest(local)
    # A build in a checkout's dist/ is what a developer just made: it differs
    # from the release by construction, and is the one they meant to test.
    checkout = _source_checkout_binary_dir()
    from_checkout = checkout is not None and local.parent == checkout
    published = None if from_checkout else await published_digest(build)
    if published is not None and published != local_digest:
        log.warning(
            "The agent copy %s is not agent %s's build (sha256 %s, release %s); "
            "machines are sent the release. Replace it with the release's %s, or delete it.",
            local, AGENT_VERSION, local_digest[:12], published[:12], build,
        )
        return {
            "platform": build,
            "url": release_url,
            "fallback_url": None,
            "sha256": published,
            "source": "release",
            "reason": f"This server's copy of the agent is not {AGENT_VERSION}'s build; using the release.",
        }
    return {
        "platform": build,
        "url": release_url,
        "fallback_url": f"{_origin(request)}/api/install/binary/{build}",
        "sha256": local_digest,
        "source": "release+backend",
    }


@router.get("/binary/{build}")
async def install_binary(build: str) -> Response:
    """Serve this deployment's own copy of the agent, when it has one."""
    if build not in set(PLATFORMS.values()):
        return Response(status_code=404)
    local = local_binary(build)
    if local is None:
        return Response(status_code=404)
    return Response(
        content=local.read_bytes(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="faden-agent-{build}"'},
    )
