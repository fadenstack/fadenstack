"""HTTPS for the install: certificates made here or brought along, the ports, and deploy's step."""

from __future__ import annotations

import ipaddress
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import click
import pytest
from click.testing import CliRunner
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from llmport.commands import ports as ports_cmd_mod
from llmport.commands import tls as tls_cmd
from llmport.core import tls
from llmport.core.edge import check_ports, console_url, ports, url_for, verify_for
from llmport.core.env_gen import read_env_file, write_env_file
from llmport.core.settings import LlmportConfig

NAMES = ["localhost", "127.0.0.1", "::1", "box", "10.250.10.61"]


@pytest.fixture(autouse=True)
def fixed_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tls, "machine_names", lambda: list(NAMES))
    # Never a real timer or crontab from a test run.
    from llmport.core import schedule

    monkeypatch.setattr(schedule, "available", lambda: False)


@pytest.fixture
def install(tmp_path: Path) -> Path:
    write_env_file(tmp_path / ".env", {"VERSION": "0.3.0"})
    return tmp_path


def _load(path: Path) -> x509.Certificate:
    return x509.load_pem_x509_certificate(path.read_bytes())


# ── made here ─────────────────────────────────────────────────────


def test_a_certificate_made_here_names_the_machine_and_is_signed_by_the_installs_authority(install: Path) -> None:
    made = tls.generate(install, ["console.example.org"])

    leaf = _load(install / "tls" / "edge.crt")
    ca = _load(made.ca_public)
    leaf.verify_directly_issued_by(ca)
    assert made.trust_changed, "the machines have an authority to fetch"
    assert made.summary.made_here
    assert set(made.summary.names) >= {"localhost", "127.0.0.1", "box", "10.250.10.61", "console.example.org"}
    assert tls.material_present(install)
    assert not (install / "tls" / "edge-chain.pem").exists()
    # The authority's key is outside the directory nginx and the backend mount.
    assert (install / "tls-ca" / "ca.key").is_file()
    assert not list((install / "tls").glob("*ca*.key"))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_keys_are_readable_by_their_owner_only(install: Path) -> None:
    tls.generate(install)
    assert os.stat(install / "tls" / "edge.key").st_mode & 0o777 == 0o600
    assert os.stat(install / "tls-ca" / "ca.key").st_mode & 0o777 == 0o600


def test_a_second_certificate_is_signed_by_the_same_authority(install: Path) -> None:
    first = tls.generate(install)
    ca_before = (install / "tls-ca" / "ca.crt").read_bytes()
    second = tls.generate(install, ["another.name"])
    assert not second.trust_changed
    assert (install / "tls-ca" / "ca.crt").read_bytes() == ca_before
    assert first.summary.fingerprint_sha256 != second.summary.fingerprint_sha256
    _load(install / "tls" / "edge.crt").verify_directly_issued_by(_load(install / "tls-ca" / "ca.crt"))


def test_an_authority_about_to_expire_is_replaced_and_the_old_one_still_published(install: Path) -> None:
    tls.generate(install, now=datetime.now(UTC) - timedelta(days=tls.CA_DAYS - 100))
    old = (install / "tls-ca" / "ca.crt").read_bytes()
    made = tls.generate(install)
    assert made.trust_changed
    assert (install / "tls-ca" / "ca.crt").read_bytes() != old
    assert (install / "tls-ca" / "ca-previous.crt").read_bytes() == old
    published = x509.load_pem_x509_certificates((install / "tls" / "ca.crt").read_bytes())
    assert len(published) == 2, "the new authority and, while it is valid, the one before"
    assert made.summary.made_here


@pytest.mark.parametrize("bad", ["https://box", "box:8443", "a b"])
def test_extra_names_are_names_not_urls(install: Path, bad: str) -> None:
    with pytest.raises(tls.TlsError):
        tls.generate(install, [bad])


def test_switching_off_keeps_the_authority(install: Path) -> None:
    tls.generate(install)
    assert tls.remove(install)
    assert not tls.material_present(install)
    assert tls.current(install) is None
    assert (install / "tls-ca" / "ca.key").is_file()
    assert not tls.remove(install)


# ── brought by the operator ───────────────────────────────────────


def _issue(
    tmp: Path,
    names: list[str],
    *,
    not_before: datetime | None = None,
    days: int = 90,
    intermediate: bool = False,
) -> tuple[Path, Path]:
    """An operator's certificate: optionally under an intermediate, written as a fullchain."""
    now = datetime.now(UTC)
    not_before = not_before or now - timedelta(days=1)

    def make(subject: str, issuer_name: x509.Name | None, issuer_key, *, ca: bool):  # type: ignore[no-untyped-def]
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)])
        signer = issuer_key or key
        # Key identifiers as a real issuer writes them: Python 3.13+ refuses chains without them.
        builder = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(issuer_name or name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(not_before)
            .not_valid_after(not_before + timedelta(days=days))
            .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(signer.public_key()), critical=False
            )
        )
        if ca:
            builder = builder.add_extension(
                x509.KeyUsage(
                    digital_signature=True, content_commitment=False, key_encipherment=False,
                    data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                    encipher_only=False, decipher_only=False,
                ),
                critical=True,
            )
        else:
            sans = [
                x509.IPAddress(ipaddress.ip_address(n)) if tls._is_ip(n) else x509.DNSName(n)  # noqa: SLF001
                for n in names
            ]
            builder = builder.add_extension(x509.SubjectAlternativeName(sans), critical=False)
        return builder.sign(issuer_key or key, hashes.SHA256()), key, name

    blocks = []
    issuer_name = issuer_key = None
    if intermediate:
        inter, inter_key, inter_name = make("Intermediate", None, None, ca=True)
        issuer_name, issuer_key = inter_name, inter_key
        blocks.append(inter)
    leaf, leaf_key, _ = make(names[0] if names else "leaf", issuer_name, issuer_key, ca=False)
    cert_path, key_path = tmp / "fullchain.pem", tmp / "key.pem"
    cert_path.write_bytes(b"".join(c.public_bytes(serialization.Encoding.PEM) for c in [leaf, *blocks]))
    key_path.write_bytes(
        leaf_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()
        )
    )
    return cert_path, key_path


def test_a_fullchain_file_is_split_into_certificate_and_chain(install: Path, tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    cert, key = _issue(src, ["box"], intermediate=True)
    summary = tls.use_own(install, cert, key)
    assert summary.warnings == []
    assert len(tls._pem_blocks((install / "tls" / "edge.crt").read_text())) == 1
    assert len(tls._pem_blocks((install / "tls" / "edge-chain.pem").read_text())) == 1
    assert (install / "tls" / "edge.key").read_text().startswith("-----BEGIN PRIVATE KEY-----")


def test_a_key_of_another_certificate_is_refused(install: Path, tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    cert, _ = _issue(tmp_path / "a", ["box"])
    _, other_key = _issue(tmp_path / "b", ["box"])
    with pytest.raises(tls.TlsError, match="does not belong"):
        tls.use_own(install, cert, other_key)
    assert not tls.material_present(install)


def test_an_expired_certificate_is_refused(install: Path, tmp_path: Path) -> None:
    cert, key = _issue(tmp_path, ["box"], not_before=datetime.now(UTC) - timedelta(days=100), days=30)
    with pytest.raises(tls.TlsError, match="expired"):
        tls.use_own(install, cert, key)


def test_a_key_with_a_passphrase_is_refused_with_the_way_out(install: Path, tmp_path: Path) -> None:
    cert, key = _issue(tmp_path, ["box"])
    loaded = serialization.load_pem_private_key(key.read_bytes(), password=None)
    key.write_bytes(
        loaded.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(b"secret"),
        )
    )
    with pytest.raises(tls.TlsError, match="passphrase"):
        tls.use_own(install, cert, key)


def test_a_certificate_for_other_names_is_installed_with_a_warning(install: Path, tmp_path: Path) -> None:
    cert, key = _issue(tmp_path, ["elsewhere.example.org"])
    summary = tls.use_own(install, cert, key)
    assert tls.material_present(install)
    assert any("names none of this machine" in w for w in summary.warnings)


def test_wildcards_cover_one_label() -> None:
    assert tls.covers(["*.example.org"], "box.example.org")
    assert not tls.covers(["*.example.org"], "a.box.example.org")
    assert not tls.covers(["*.example.org"], "example.org")
    assert tls.covers(["10.250.10.61"], "10.250.10.61")


# ── the front door ────────────────────────────────────────────────


def test_urls_follow_the_ports_and_the_scheme(install: Path) -> None:
    env = {"LLM_PORT_HTTP_PORT": "8080", "LLM_PORT_HTTPS_PORT": "8443"}
    assert url_for("box", https=False, env=env) == "http://box:8080"
    assert url_for("box", https=True, env=env) == "https://box:8443"
    assert url_for("box", https=True, env={}) == "https://box"
    assert url_for("::1", https=True, env={}) == "https://[::1]"
    assert console_url(install, env) == "http://localhost:8080"
    tls.generate(install)
    assert console_url(install, env) == "https://localhost:8443"
    assert ports({}) == (80, 443)


def test_only_this_machine_goes_unverified() -> None:
    assert verify_for("https://localhost:8443") is False
    assert verify_for("https://127.0.0.1") is False
    assert verify_for("https://[::1]") is False
    assert verify_for("https://console.example.org") is True
    assert verify_for("https://10.250.10.61") is True
    assert verify_for("http://localhost") is True


def test_port_pairs_are_checked() -> None:
    assert check_ports(80, 443) is None
    assert "share" in (check_ports(8080, 8080) or "")
    assert "between" in (check_ports(0, 443) or "")


def test_ports_are_written_to_the_env_file(install: Path) -> None:
    env_path = install / ".env"
    assert ports_cmd_mod.set_ports(env_path, http=None, https=None) is None
    assert ports_cmd_mod.set_ports(env_path, http=8080, https=None) == (8080, 443)
    env = read_env_file(env_path)
    assert (env["LLM_PORT_HTTP_PORT"], env["LLM_PORT_HTTPS_PORT"], env["VERSION"]) == ("8080", "443", "0.3.0")
    with pytest.raises(click.UsageError):
        ports_cmd_mod.set_ports(env_path, http=443, https=443)


# ── deploy's step ─────────────────────────────────────────────────


@pytest.fixture
def free_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ports_cmd_mod, "port_conflicts", lambda *_a, **_k: [])


def _step(install: Path, **kwargs):  # type: ignore[no-untyped-def]
    args = {
        "yes": True, "http_port": None, "https_port": None, "mode": None,
        "cert": None, "key": None, "chain": None, "names": (),
    }
    args.update(kwargs)
    return tls_cmd.deploy_step(install, install / ".env", **args)


def test_deploy_with_yes_keeps_what_the_install_has(install: Path, free_ports: None) -> None:
    assert _step(install) is None
    assert not tls.material_present(install)
    tls.generate(install)
    assert _step(install) is None
    assert tls.material_present(install), "a redeploy must not switch HTTPS off"


def test_deploy_can_generate_bring_or_switch_off(install: Path, tmp_path: Path, free_ports: None) -> None:
    ca = _step(install, mode="generate", names=("console.example.org",), http_port=8080, https_port=8443)
    assert ca == install / "tls" / "ca.crt"
    made = tls.current(install)
    assert made is not None and "console.example.org" in made.names
    assert ports(read_env_file(install / ".env")) == (8080, 8443)

    src = tmp_path / "own"
    src.mkdir()
    cert, key = _issue(src, ["box"])
    assert _step(install, cert=cert, key=key) is None  # --tls-cert implies --tls own
    summary = tls.current(install)
    assert summary is not None and not summary.made_here

    _step(install, mode="off")
    assert not tls.material_present(install)


def test_deploy_with_yes_stops_on_a_taken_port(install: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ports_cmd_mod, "port_conflicts",
        lambda http, https, **_k: [f"HTTPS port {https} is in use by something else."] if https == 443 else [],
    )
    with pytest.raises(SystemExit):
        _step(install)
    assert _step(install, https_port=8443) is None
    assert ports(read_env_file(install / ".env")) == (80, 8443)


def test_deploy_asks_for_another_port_when_one_is_taken(install: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ports_cmd_mod, "port_conflicts",
        lambda http, https, **_k: [f"HTTP port {http} is in use by something else."] if http == 80 else [],
    )
    answers = iter([8080, "off"])
    monkeypatch.setattr(tls_cmd.click, "prompt", lambda *_a, **_k: next(answers))
    _step(install, yes=False)
    assert ports(read_env_file(install / ".env")) == (8080, 443)


# ── the commands ──────────────────────────────────────────────────


def test_the_commands_work_on_the_configured_install(install: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = LlmportConfig(install_dir=str(install))
    monkeypatch.setattr(tls_cmd, "load_config", lambda: cfg)
    monkeypatch.setattr(ports_cmd_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(ports_cmd_mod, "port_conflicts", lambda *_a, **_k: [])
    runner = CliRunner()

    result = runner.invoke(tls_cmd.tls_group, ["status"])
    assert result.exit_code == 0, result.output
    assert "off" in result.output

    result = runner.invoke(tls_cmd.tls_group, ["generate", "--no-restart", "-n", "console.example.org"])
    assert result.exit_code == 0, result.output
    assert "https://10.250.10.61" in result.output
    assert "ca.crt" in result.output

    result = runner.invoke(ports_cmd_mod.ports_cmd, ["--https", "8443", "--no-restart"])
    assert result.exit_code == 0, result.output
    assert "https://10.250.10.61:8443" in result.output

    result = runner.invoke(tls_cmd.tls_group, ["off", "--no-restart"])
    assert result.exit_code == 0, result.output
    assert not tls.material_present(install)
    assert "http://10.250.10.61" in result.output


def test_other_machines_are_shown_an_address_they_can_resolve() -> None:
    assert tls_cmd.reachable_example(["https://llm-port", "https://10.250.10.61"]) == "https://10.250.10.61"
    assert tls_cmd.reachable_example(["https://box", "https://box.lan:8443"]) == "https://box.lan:8443"
    assert tls_cmd.reachable_example(["https://box"]) == "https://box"
    assert tls_cmd.reachable_example([]) == "https://<this server>"


# ── the authority, ahead of HTTPS ─────────────────────────────────


def test_the_authority_is_made_once_and_its_public_copy_kept(install: Path) -> None:
    public, created = tls.ensure_ca(install)
    assert created
    assert public == install / "tls" / "ca.crt"
    assert public.read_bytes() == (install / "tls-ca" / "ca.crt").read_bytes()
    assert not tls.material_present(install), "making the authority serves nothing"
    ca_before = (install / "tls-ca" / "ca.crt").read_bytes()
    assert tls.ensure_ca(install) == (public, False), "nothing new to fetch"
    public.unlink()
    again, changed = tls.ensure_ca(install)
    assert changed, "the public copy is written again"
    assert (install / "tls-ca" / "ca.crt").read_bytes() == ca_before, "by the same authority"
    assert again.read_bytes() == ca_before
    assert not tls.generate(install).trust_changed, "a certificate later is signed by the same authority"


def test_deploy_makes_the_authority_even_with_https_off(install: Path, free_ports: None) -> None:
    assert _step(install) is None
    assert (install / "tls" / "ca.crt").is_file()
    assert not tls.material_present(install)


def test_generate_waits_for_the_machines_only_when_the_authority_is_new(
    install: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = LlmportConfig(install_dir=str(install))
    monkeypatch.setattr(tls_cmd, "load_config", lambda: cfg)
    monkeypatch.setattr(tls_cmd, "apply_to_proxy", lambda *_a, **_k: True)
    slept: list[float] = []
    monkeypatch.setattr(tls_cmd.time, "sleep", slept.append)
    runner = CliRunner()

    assert runner.invoke(tls_cmd.tls_group, ["generate", "--no-wait"]).exit_code == 0
    assert slept == []
    for path in (install / "tls-ca").iterdir():
        path.unlink()
    result = runner.invoke(tls_cmd.tls_group, ["generate"])
    assert result.exit_code == 0, result.output
    assert slept == [tls_cmd.MACHINE_TRUST_WAIT_SEC]
    assert runner.invoke(tls_cmd.tls_group, ["generate"]).exit_code == 0
    assert slept == [tls_cmd.MACHINE_TRUST_WAIT_SEC], "an authority the machines have already is not waited for"

    result = runner.invoke(tls_cmd.tls_group, ["off"])
    assert result.exit_code == 0, result.output
    assert "never falls back" in " ".join(result.output.split())


# ── the machine CA (model traffic TLS) ─────────────────────────────────────


def test_machine_ca_status_and_replace_run_in_the_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    import json
    import subprocess

    from click.testing import CliRunner

    from llmport.commands import tls as tls_cmd

    calls: list[list[str]] = []
    doc = {
        "enabled": True,
        "authorities": [{"fingerprint_sha256": "ab" * 32, "not_after": "2031-09-26T14:38:41+00:00",
                         "issuing": True, "issuing_from": "2026-09-27T14:38:41+00:00", "reason": "replaced"}],
        "gateway": {"not_after": "2026-10-27T15:02:08+00:00", "current": True},
        "machines": {"n": {"not_after": "2026-10-27T15:02:45+00:00", "names": ["10.250.10.71"],
                           "issued_at": "2026-09-27T15:02:45+00:00", "current": False}},
    }

    def run(argv: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        assert "store.rotate" in str(kw["input"]), "the script is fed on stdin"
        return subprocess.CompletedProcess(argv, 0, "noise\n" + json.dumps(doc) + "\n", "")

    monkeypatch.setattr(tls_cmd.shutil, "which", lambda name: "docker")
    monkeypatch.setattr(tls_cmd.subprocess, "run", run)
    out = CliRunner().invoke(tls_cmd.tls_group, ["machine-ca"])
    assert out.exit_code == 0, out.output
    assert calls[-1][:5] == ["docker", "exec", "-i", "llm-port-backend", "python"] and calls[-1][-1] == "status"
    assert "10.250.10.71" in out.output and "renews within a minute" in out.output

    refused = CliRunner().invoke(tls_cmd.tls_group, ["machine-ca", "--replace"], input="n\n")
    assert refused.exit_code != 0 and calls[-1][-1] == "status", "nothing replaced without a yes"
    done = CliRunner().invoke(tls_cmd.tls_group, ["machine-ca", "--replace", "--yes"])
    assert done.exit_code == 0 and calls[-1][-1] == "replace"
    assert "replaced" in done.output
