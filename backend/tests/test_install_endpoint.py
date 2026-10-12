"""The installer the operator runs, and what it refuses to pretend.

The point of generating this server-side is that the human assembles nothing:
the address, the build and the digest are all filled in before the script
leaves the backend. The tests below pin the three things that would quietly
undo that -- a script that guesses a platform, a plan that claims a digest it
did not compute, and an install that says "verified" when nothing was checked.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from faden_backend.web.api import install as install_api

ORIGIN = "http://10.250.10.220:8000"


def _script() -> str:
    return install_api.render_installer(ORIGIN)


# ── what the script is, and is not ───────────────────────────────────────


class TestScript:
    def test_carries_this_backend_so_nobody_has_to_type_it(self) -> None:
        assert f'BACKEND="{ORIGIN}"' in _script()

    def test_is_never_piped_into_a_shell(self) -> None:
        """The whole security argument against the neighbours rests on this.

        The script lands on disk so it can be read before it is run.
        """
        script = _script()
        assert "| bash" not in script
        assert "| sh" not in script

    def test_verifies_what_it_downloads(self) -> None:
        script = _script()
        assert "sha256sum" in script
        assert 'ACTUAL" != "$SHA"' in script

    def test_refuses_to_install_what_it_cannot_verify(self) -> None:
        """There is no "nothing was verified" path: no valid digest, no install."""
        script = _script()
        assert "nothing was verified" not in script
        assert "Refusing to install" in script

    def test_does_not_ask_the_operator_to_pick_an_architecture(self) -> None:
        script = _script()
        assert "uname -m" in script
        assert "uname -s" in script

    @pytest.mark.skipif(shutil.which("sh") is None, reason="no POSIX sh available")
    def test_is_valid_posix_shell(self, tmp_path: Path) -> None:
        path = tmp_path / "install.sh"
        path.write_text(_script(), encoding="utf-8", newline="\n")
        result = subprocess.run(
            ["sh", "-n", str(path)], capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 0, result.stderr

    @pytest.mark.skipif(shutil.which("sh") is None, reason="no POSIX sh available")
    def test_rejects_an_option_it_does_not_understand(self, tmp_path: Path) -> None:
        path = tmp_path / "install.sh"
        path.write_text(_script(), encoding="utf-8", newline="\n")
        result = subprocess.run(
            ["sh", str(path), "--wat"], capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 2
        assert "Unknown option" in result.stderr


# ── the plan: where the build comes from, and what it hashes to ──────────

PIN = {
    "linux-aarch64": hashlib.sha256(b"agent 0.2.8 aarch64").hexdigest(),
    "linux-x86_64": hashlib.sha256(b"agent 0.2.8 x86_64").hexdigest(),
}


@pytest.fixture()
def pinned(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.setattr(install_api, "pinned_digest", PIN.get)
    return PIN


async def _plan(client: AsyncClient, platform: str = "linux-aarch64") -> dict[str, str | None]:
    return (await client.get("/api/install/plan", params={"platform": platform})).json()


def test_the_release_pins_the_agent_it_hands_out() -> None:
    """The tracked agent_release.json is for AGENT_VERSION and names both architectures."""
    for build in ("linux-x86_64", "linux-aarch64"):
        digest = install_api.pinned_digest(build)
        assert digest is not None and len(digest) == 64
        int(digest, 16)


def test_a_pin_for_another_version_pins_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pin = tmp_path / "agent_release.json"
    pin.write_text(
        '{"version": "0.0.1", "sha256": {"linux-x86_64": "' + "a" * 64 + '"}}', encoding="utf-8"
    )
    monkeypatch.setattr(install_api, "__file__", str(tmp_path / "__init__.py"))
    assert install_api.pinned_digest("linux-x86_64") is None


@pytest.mark.anyio()
async def test_plan_carries_the_pinned_digest_with_no_local_copy(
    client: AsyncClient, fastapi_app: FastAPI, monkeypatch: pytest.MonkeyPatch, pinned: dict[str, str]
) -> None:
    """A pip-installed server holds no agent, and still says what it must hash to."""
    monkeypatch.setattr(install_api, "local_binary", lambda _build: None)
    body = await _plan(client)
    assert body["platform"] == "linux-aarch64"
    assert str(body["url"]).startswith("https://github.com/")
    assert body["source"] == "release"
    assert body["sha256"] == pinned["linux-aarch64"]


@pytest.mark.anyio()
async def test_plan_offers_no_build_it_cannot_verify(
    client: AsyncClient, fastapi_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(install_api, "local_binary", lambda _build: None)
    monkeypatch.setattr(install_api, "pinned_digest", lambda _build: None)
    body = await _plan(client)
    assert body["url"] is None and body["sha256"] is None
    assert "pins no agent" in str(body["reason"])


@pytest.mark.anyio()
async def test_plan_serves_the_pinned_local_copy_as_a_fallback(
    client: AsyncClient,
    fastapi_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    pinned: dict[str, str],
) -> None:
    """The air-gapped case: the node never needs to reach the internet.

    Both sources are offered and the *node* chooses, because whether there is
    internet is a fact about the node rather than about this server. The digest
    is the pinned one either way.
    """
    binary = tmp_path / "faden-agent-linux-aarch64"
    binary.write_bytes(b"agent 0.2.8 aarch64")
    monkeypatch.setattr(install_api, "local_binary", lambda _build: binary)
    body = await _plan(client)
    assert body["source"] == "release+backend"
    assert "/api/install/binary/linux-aarch64" in str(body["fallback_url"])
    assert str(body["url"]).startswith("https://github.com/")
    assert body["sha256"] == pinned["linux-aarch64"]


@pytest.mark.anyio()
async def test_a_local_copy_that_is_not_the_pinned_build_is_set_aside(
    client: AsyncClient,
    fastapi_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    pinned: dict[str, str],
) -> None:
    """Nothing replaces the server's copy when the server is upgraded.

    A stale copy used to be pinned by its own digest, so the new release failed
    the install script's check. Now the pin decides: the copy is not offered,
    not as a fallback and not from the binary endpoint.
    """
    binary = tmp_path / "faden-agent-linux-aarch64"
    binary.write_bytes(b"agent 0.1.12")
    monkeypatch.setattr(install_api, "local_binary", lambda _build: binary)
    body = await _plan(client)
    assert (body["source"], body["sha256"], body["fallback_url"]) == (
        "release", pinned["linux-aarch64"], None,
    )
    assert (await client.get("/api/install/binary/linux-aarch64")).status_code == 404


@pytest.mark.anyio()
async def test_the_pinned_local_copy_is_served(
    client: AsyncClient,
    fastapi_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    pinned: dict[str, str],
) -> None:
    binary = tmp_path / "faden-agent-linux-aarch64"
    binary.write_bytes(b"agent 0.2.8 aarch64")
    monkeypatch.setattr(install_api, "local_binary", lambda _build: binary)
    r = await client.get("/api/install/binary/linux-aarch64")
    assert r.status_code == 200
    assert r.content == b"agent 0.2.8 aarch64"


@pytest.mark.anyio()
async def test_a_developers_own_build_carries_its_own_digest(
    client: AsyncClient,
    fastapi_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    pinned: dict[str, str],
) -> None:
    """A build in the checkout's dist/ differs from the release by construction."""
    binary = tmp_path / "faden-agent-linux-aarch64"
    binary.write_bytes(b"agent 0.2.8, built just now")
    monkeypatch.setattr(install_api, "local_binary", lambda _build: binary)
    monkeypatch.setattr(install_api, "_source_checkout_binary_dir", lambda: tmp_path)
    body = await _plan(client)
    assert body["source"] == "checkout"
    assert "/api/install/binary/linux-aarch64" in str(body["url"])
    assert body["fallback_url"] is None
    assert body["sha256"] == hashlib.sha256(binary.read_bytes()).hexdigest()
    assert (await client.get("/api/install/binary/linux-aarch64")).status_code == 200


# ── the install line refuses what does not match ─────────────────────────


@pytest.mark.skipif(shutil.which("sh") is None, reason="no POSIX sh available")
class TestInstallLineVerification:
    """Run the generated script against a stand-in curl that serves a plan and a file."""

    GOOD = b"#!/bin/sh\nexit 0\n"

    def _run(
        self, tmp_path: Path, plan_sha: str | None, served: bytes
    ) -> subprocess.CompletedProcess[str]:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (tmp_path / "served").write_bytes(served)
        sha = f'"sha256": "{plan_sha}",' if plan_sha is not None else ""
        (tmp_path / "plan.json").write_text(
            f'{{"url": "https://example.invalid/agent", {sha} "source": "release"}}',
            encoding="utf-8", newline="\n",
        )
        curl = bin_dir / "curl"
        curl.write_text(
            "#!/bin/sh\n"
            'out=""\n'
            'while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift ;; esac; shift; done\n'
            f'if [ -n "$out" ]; then cat "{tmp_path.as_posix()}/served" > "$out"; '
            f'else cat "{tmp_path.as_posix()}/plan.json"; fi\n',
            encoding="utf-8", newline="\n",
        )
        curl.chmod(0o755)
        script = tmp_path / "install.sh"
        script.write_text(_script(), encoding="utf-8", newline="\n")
        target = tmp_path / "out" / "faden-agent"
        return subprocess.run(
            ["sh", str(script), "--user", "--install-path", str(target), "--backend", ORIGIN],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "PATH": f"{bin_dir.as_posix()}{os.pathsep}{os.environ['PATH']}",
                 "HOME": str(tmp_path)},
        )

    def test_a_download_that_does_not_match_is_refused(self, tmp_path: Path) -> None:
        pin = hashlib.sha256(self.GOOD).hexdigest()
        result = self._run(tmp_path, pin, b"someone else's binary")
        assert result.returncode == 1
        assert "does not match the expected SHA-256" in result.stderr
        assert pin in result.stderr
        assert not (tmp_path / "out" / "faden-agent").exists()

    def test_a_plan_with_no_digest_is_refused(self, tmp_path: Path) -> None:
        result = self._run(tmp_path, None, self.GOOD)
        assert result.returncode == 1
        assert "Refusing to install" in result.stderr
        assert not (tmp_path / "out" / "faden-agent").exists()

    def test_a_download_that_matches_is_installed(self, tmp_path: Path) -> None:
        result = self._run(tmp_path, hashlib.sha256(self.GOOD).hexdigest(), self.GOOD)
        assert "Verified." in result.stdout, result.stdout + result.stderr
        assert (tmp_path / "out" / "faden-agent").read_bytes() == self.GOOD


@pytest.mark.anyio()
async def test_arm64_and_aarch64_are_the_same_build(
    client: AsyncClient, fastapi_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``uname -m`` says aarch64; plenty of tooling says arm64."""
    monkeypatch.setattr(install_api, "local_binary", lambda _build: None)
    a = (await client.get("/api/install/plan", params={"platform": "linux-arm64"})).json()
    b = (await client.get("/api/install/plan", params={"platform": "linux-aarch64"})).json()
    assert a["url"] == b["url"]


@pytest.mark.anyio()
async def test_an_unpublished_platform_says_so_rather_than_guessing(
    client: AsyncClient, fastapi_app: FastAPI
) -> None:
    r = await client.get("/api/install/plan", params={"platform": "linux-riscv64"})
    body = r.json()
    assert body["url"] is None
    assert "riscv64" in body["reason"]


@pytest.mark.anyio()
async def test_the_script_is_downloadable_without_a_login(
    client: AsyncClient, fastapi_app: FastAPI
) -> None:
    """A machine that has never enrolled has no credential to present."""
    r = await client.get("/api/install/faden-agent.sh")
    assert r.status_code == 200
    assert "faden-agent.sh" in r.headers.get("content-disposition", "")
    assert "#!/bin/sh" in r.text


@pytest.mark.anyio()
async def test_binary_endpoint_404s_for_an_unknown_build(
    client: AsyncClient, fastapi_app: FastAPI
) -> None:
    r = await client.get("/api/install/binary/linux-vax")
    assert r.status_code == 404


@pytest.mark.anyio()
async def test_the_installer_unescapes_the_urls_it_is_given(
    client: AsyncClient, fastapi_app: FastAPI
) -> None:
    r"""JSON may escape forward slashes, and this encoder does.

    The script pulls URLs out of the plan with sed. Without unescaping, every
    one arrived as ``https:\/\/host\/path`` and curl rejected it with "URL
    using bad/illegal format" -- from both sources, so the installer could
    never fetch the agent at all.
    """
    script = (await client.get("/api/install/faden-agent.sh")).text
    assert r"sed 's|\\/|/|g'" in script, "the plan's escaped slashes are never undone"


@pytest.mark.anyio()
async def test_enrolling_as_root_is_not_handed_a_bare_flag(
    client: AsyncClient, fastapi_app: FastAPI
) -> None:
    """``sudo -E`` keeps the environment; root has no sudo to pass it to.

    Running the token path as root -- the ordinary case for an installer --
    executed a bare ``-E`` and died with "-E: not found", after the agent had
    already been installed. It read as a broken agent rather than a broken
    installer.
    """
    script = (await client.get("/api/install/faden-agent.sh")).text
    assert "$SUDO -E " not in script
    assert "SUDO_E=\"\"" in script and 'SUDO_E="sudo -E"' in script


# -- where the served binary comes from -----------------------------------


class TestBinaryResolution:
    """One built file, not two that are supposed to match.

    `build-binary.sh` writes into agent/dist/. That output used
    to be copied into `agent_binary_dir` by hand, and the served copy drifted
    a build behind the built one with nothing to say so -- the served x86_64
    agent was a build old and carried no licence, while the aarch64 build
    existed and had never been copied at all, so the backend reported having
    no aarch64 agent.
    """

    def test_prefers_the_build_output_when_nothing_is_configured(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        checkout = tmp_path / "dist"
        checkout.mkdir()
        monkeypatch.setattr(
            install_api.settings,
            "agent_binary_dir",
            install_api.DEFAULT_AGENT_BINARY_DIR,
        )
        monkeypatch.setattr(
            install_api, "_source_checkout_binary_dir", lambda: checkout
        )

        assert install_api._local_binary_dirs()[0] == checkout

    def test_an_operators_directory_outranks_a_checkout_on_the_same_disk(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Configuring it is a deliberate act and has to mean something.

        An air-gapped site that places binaries somewhere specific must not
        have a checkout that happens to sit above the backend quietly win.
        """
        chosen = tmp_path / "chosen"
        chosen.mkdir()
        monkeypatch.setattr(install_api.settings, "agent_binary_dir", str(chosen))
        monkeypatch.setattr(
            install_api, "_source_checkout_binary_dir", lambda: tmp_path / "dist"
        )

        assert install_api._local_binary_dirs() == [chosen]

    def test_a_deployment_with_no_checkout_still_reads_its_own_directory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            install_api.settings,
            "agent_binary_dir",
            install_api.DEFAULT_AGENT_BINARY_DIR,
        )
        monkeypatch.setattr(install_api, "_source_checkout_binary_dir", lambda: None)

        assert install_api._local_binary_dirs() == [
            Path(install_api.DEFAULT_AGENT_BINARY_DIR)
        ]

    def test_finds_every_platform_the_build_produced(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The aarch64 build existed in dist/ and was invisible to the API."""
        checkout = tmp_path / "dist"
        checkout.mkdir()
        for build in ("linux-x86_64", "linux-aarch64"):
            (checkout / f"faden-agent-{build}").write_bytes(b"ELF")
        monkeypatch.setattr(
            install_api.settings,
            "agent_binary_dir",
            install_api.DEFAULT_AGENT_BINARY_DIR,
        )
        monkeypatch.setattr(
            install_api, "_source_checkout_binary_dir", lambda: checkout
        )

        assert install_api.local_binary("linux-aarch64") is not None
        assert install_api.local_binary("linux-x86_64") is not None
        assert install_api.local_binary("macos-universal") is None

    def test_the_checkout_path_points_at_the_agents_real_build_output(self) -> None:
        """Guards the parents[5] hop: a moved module breaks this silently."""
        found = install_api._source_checkout_binary_dir()
        assert found is not None, "running from a checkout, so this must resolve"
        assert found.name == "dist"
        assert found.parent.name == "agent"
        assert (found.parent / "build-binary.sh").is_file()


# -- the address the console tells operators to install against ------------


class TestInstallAddress:
    """The console must not hand out an address only it can reach.

    It built the install command from the browser's own URL, on the
    assumption that the operator and the machine see this backend the same
    way. On a dev proxy the operator is at localhost:5173, so the command
    read `curl -fsSLO http://localhost:5173/...` -- which on the machine
    being added fetches from that machine, and finds nothing. The failure is
    silent until somebody runs it on real hardware.
    """

    @pytest.mark.parametrize(
        ("host", "expected"),
        [
            ("localhost", True),
            ("localhost:5173", True),
            ("127.0.0.1", True),
            ("127.0.0.1:8000", True),
            ("::1", True),
            ("[::1]:8000", True),
            ("10.250.10.220", False),
            ("10.250.10.220:8000", False),
            ("faden.internal", False),
        ],
    )
    def test_knows_which_addresses_mean_only_this_machine(
        self, host: str, expected: bool
    ) -> None:
        assert install_api._is_loopback(host) is expected

    def test_offers_only_addresses_another_machine_could_use(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import socket as _socket
        from collections import namedtuple

        Addr = namedtuple("Addr", "family address")
        monkeypatch.setattr(
            install_api.psutil,
            "net_if_addrs",
            lambda: {
                "Loopback": [Addr(_socket.AF_INET, "127.0.0.1")],
                "Ethernet": [Addr(_socket.AF_INET, "10.250.10.220")],
                # Windows hands out one of these when DHCP fails; it reaches
                # nothing.
                "Unplugged": [Addr(_socket.AF_INET, "169.254.10.1")],
                "IPv6 only": [Addr(_socket.AF_INET6, "fe80::1")],
            },
        )

        urls = [c["url"] for c in install_api.reachable_origins()]

        assert urls == [f"http://10.250.10.220:{install_api.settings.port}"]

    def test_ranks_a_real_adapter_above_the_virtual_ones(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A workstation has many, and all but one are useless here."""
        import socket as _socket
        from collections import namedtuple

        Addr = namedtuple("Addr", "family address")
        monkeypatch.setattr(
            install_api.psutil,
            "net_if_addrs",
            lambda: {
                "VirtualBox Host-Only Network": [Addr(_socket.AF_INET, "192.168.56.1")],
                "vEthernet (Default Switch)": [Addr(_socket.AF_INET, "172.24.224.1")],
                "Tailscale": [Addr(_socket.AF_INET, "100.64.0.59")],
                "Wi-Fi 3": [Addr(_socket.AF_INET, "10.250.10.220")],
            },
        )

        first = install_api.reachable_origins()[0]

        assert first["interface"] == "Wi-Fi 3"
        assert "10.250.10.220" in first["url"]

    def test_names_the_interface_so_the_operator_can_choose(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import socket as _socket
        from collections import namedtuple

        Addr = namedtuple("Addr", "family address")
        monkeypatch.setattr(
            install_api.psutil,
            "net_if_addrs",
            lambda: {"Ethernet 2": [Addr(_socket.AF_INET, "10.0.0.5")]},
        )

        assert install_api.reachable_origins()[0]["interface"] == "Ethernet 2"

    def test_an_inventory_failure_does_not_block_onboarding(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom() -> dict[str, list[object]]:
            raise OSError("no such device")

        monkeypatch.setattr(install_api.psutil, "net_if_addrs", _boom)

        assert install_api.reachable_origins() == []


def test_the_logger_can_print_the_characters_this_service_uses() -> None:
    """Log lines were lost, not mangled, and only when running headless.

    Windows gives a redirected stdout the cp1252 codec. Several log messages
    here contain an arrow; each one raised UnicodeEncodeError inside loguru's
    sink, which swallowed it as "--- End of logging error ---" and dropped
    the line -- so the log lacked exactly the messages describing what was
    happening, in the mode where the log is all there is.
    """
    import io

    from faden_backend import log as backend_log

    class _Cp1252Stdout(io.TextIOWrapper):
        def __init__(self) -> None:
            super().__init__(io.BytesIO(), encoding="cp1252", errors="strict")
            self.reconfigured: dict[str, object] = {}

        def reconfigure(self, **kwargs: object) -> None:  # type: ignore[override]
            self.reconfigured = kwargs

    stream = _Cp1252Stdout()
    original = backend_log.sys.stdout
    backend_log.sys.stdout = stream  # type: ignore[assignment]
    try:
        backend_log._use_utf8_stdout()
    finally:
        backend_log.sys.stdout = original  # type: ignore[assignment]

    assert stream.reconfigured == {"encoding": "utf-8", "errors": "replace"}


# -- installing without root -------------------------------------------------


def _privilege_block() -> str:
    script = _script()
    start = script.index("# -- with what privilege")
    end = script.index("# -- which build")
    return script[start:end]


def _decide(tmp_path: Path, *, sudo_ok: bool, uid: int = 1000, extra: str = "") -> dict[str, str]:
    """Run the installer's privilege decision against stub sudo and id."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "sudo").write_text(f"#!/bin/sh\nexit {0 if sudo_ok else 1}\n", newline="\n")
    (bin_dir / "id").write_text(
        f'#!/bin/sh\ncase "$1" in -u) echo {uid} ;; -un) echo tester ;; esac\n', newline="\n"
    )
    for stub in bin_dir.iterdir():
        stub.chmod(0o755)
    harness = tmp_path / "decide.sh"
    harness.write_text(
        "set -eu\n"
        'INSTALL_PATH="/usr/local/bin/faden-agent"\n'
        "INSTALL_PATH_SET=0\nFORCE_USER=0\n"
        f"{extra}\n"
        + _privilege_block()
        + 'echo "ROOTLESS=$ROOTLESS"\necho "INSTALL_PATH=$INSTALL_PATH"\n',
        newline="\n",
    )
    result = subprocess.run(
        ["sh", str(harness)],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}", "HOME": "/home/tester"},
    )
    assert result.returncode == 0, result.stderr
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


@pytest.mark.skipif(shutil.which("sh") is None, reason="no POSIX sh available")
class TestInstallingWithoutRoot:
    """On both DGX nodes sudo needs a password.

    A run without root installed the binary and then died writing the service
    unit, leaving an agent that ran only while somebody's shell was open.
    """

    def test_no_root_and_no_passwordless_sudo_means_a_home_install(self, tmp_path: Path) -> None:
        decided = _decide(tmp_path, sudo_ok=False)
        assert decided["ROOTLESS"] == "1"
        assert decided["INSTALL_PATH"] == "/home/tester/.local/bin/faden-agent"

    def test_passwordless_sudo_keeps_the_system_install(self, tmp_path: Path) -> None:
        decided = _decide(tmp_path, sudo_ok=True)
        assert decided["ROOTLESS"] == "0"
        assert decided["INSTALL_PATH"] == "/usr/local/bin/faden-agent"

    def test_root_is_a_system_install(self, tmp_path: Path) -> None:
        decided = _decide(tmp_path, sudo_ok=False, uid=0)
        assert decided["ROOTLESS"] == "0"

    def test_user_can_be_asked_for_even_with_sudo(self, tmp_path: Path) -> None:
        decided = _decide(tmp_path, sudo_ok=True, extra="FORCE_USER=1")
        assert decided["ROOTLESS"] == "1"

    def test_an_explicit_path_is_kept(self, tmp_path: Path) -> None:
        decided = _decide(
            tmp_path,
            sudo_ok=False,
            extra='INSTALL_PATH="/opt/me/faden-agent"\nINSTALL_PATH_SET=1',
        )
        assert decided["INSTALL_PATH"] == "/opt/me/faden-agent"

    def test_the_service_it_starts_is_a_user_one(self) -> None:
        script = _script()
        assert '"$INSTALL_PATH" join $SCOPE "$@" "$BACKEND"' in script
        assert 'SCOPE="--user"' in script


def test_the_backend_expects_the_agent_version_the_checkout_builds() -> None:
    """The installer pins a version; it must be the one build-binary.sh produces.

    Skipped outside a checkout, where there is no agent source to compare to.
    """
    import tomllib

    agent_pyproject = Path(install_api.__file__).resolve().parents[5] / "agent" / "pyproject.toml"
    if not agent_pyproject.is_file():
        pytest.skip("no agent source beside this backend")
    version = tomllib.loads(agent_pyproject.read_text(encoding="utf-8"))["project"]["version"]
    assert install_api.AGENT_VERSION == version


@pytest.mark.parametrize(
    ("running", "announced"),
    [("0.1.18", True), ("v0.2.2", True), (install_api.AGENT_VERSION, False), ("9.0.0", False), (None, False), ("dev", False)],
    ids=["older", "older-with-v", "current", "newer", "unknown", "unparsable"],
)
def test_a_newer_agent_is_announced_for_an_older_machine_only(running: str | None, announced: bool) -> None:
    """Shown on the machine (NodeDTO.agent_update); updating stays the operator's step."""
    from faden_backend.web.api.admin.system.schema import NodeDTO  # noqa: PLC0415

    node = NodeDTO(id="n", agent_id="a", host="10.250.10.49", status="healthy", version=running,
                   created_at="2026-09-29T00:00:00Z", updated_at="2026-09-29T00:00:00Z")
    assert node.model_dump()["agent_update"] == (install_api.AGENT_VERSION if announced else None)


@pytest.mark.anyio()
async def test_a_clients_forwarded_host_does_not_reach_the_install_script(
    client: AsyncClient, fastapi_app: FastAPI
) -> None:
    """The script is run as root on a new machine; the address in it is the one the caller reached."""
    r = await client.get(
        "/api/install/faden-agent.sh",
        headers={"Host": "faden.example:8443", "X-Forwarded-Host": "probe.invalid", "X-Forwarded-Proto": "https"},
    )
    assert r.status_code == 200
    assert "probe.invalid" not in r.text
    assert "https://faden.example:8443" in r.text


@pytest.mark.anyio()
async def test_a_host_that_is_not_a_plain_name_is_not_put_in_the_script(
    client: AsyncClient, fastapi_app: FastAPI
) -> None:
    r = await client.get("/api/install/faden-agent.sh", headers={"Host": 'evil.invalid";touch /x;"'})
    assert r.status_code == 200
    assert "evil.invalid" not in r.text
