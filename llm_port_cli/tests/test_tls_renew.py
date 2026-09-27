"""Keeping HTTPS current: renewing, a private issuer for the machines, what the backend is told."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from llmport.commands import tls as tls_cmd
from llmport.core import schedule, tls
from llmport.core.env_gen import write_env_file
from llmport.core.settings import LlmportConfig
from test_tls import NAMES, _issue

pytestmark = pytest.mark.usefixtures("fixed_machine")


@pytest.fixture
def fixed_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tls, "machine_names", lambda: list(NAMES))
    monkeypatch.setattr(schedule, "available", lambda: False)  # never a real timer from a test


@pytest.fixture
def install(tmp_path: Path) -> Path:
    root = tmp_path / "install"
    root.mkdir()
    write_env_file(root / ".env", {"VERSION": "0.3.0"})
    return root


def _private_pki(tmp: Path, names: list[str], *, days: int = 90) -> tuple[Path, Path, Path]:
    """A private root and a certificate it issued: (certificate, key, root)."""
    tmp.mkdir(parents=True, exist_ok=True)
    cert, key = _issue(tmp, names, intermediate=True, days=days)
    blocks = tls._pem_blocks(cert.read_text())  # noqa: SLF001
    root = tmp / "root.pem"
    root.write_text(blocks[1], encoding="utf-8")  # the issuer _issue makes is self-signed: a root
    cert.write_text(blocks[0], encoding="utf-8")
    return cert, key, root


# ── a private issuer ──────────────────────────────────────────────


def test_a_private_issuer_is_given_to_the_machines(install: Path, tmp_path: Path) -> None:
    cert, key, root = _private_pki(tmp_path / "pki", ["box"])
    tls.ensure_ca(install)
    summary = tls.use_own(install, cert, key, ca_path=root)
    assert summary.trust_changed
    published = (install / "tls" / "ca.crt").read_text()
    assert root.read_text().strip() in published, "the operator's root"
    assert (install / "tls-ca" / "ca.crt").read_text().strip() in published, "and the install's own, still"


def test_the_root_a_fullchain_ends_in_is_taken_as_the_issuer(install: Path, tmp_path: Path) -> None:
    src = tmp_path / "pki"
    src.mkdir()
    cert, key = _issue(src, ["box"], intermediate=True)  # the certificate and its self-signed issuer
    summary = tls.use_own(install, cert, key)
    assert summary.trust_changed
    assert (install / "tls-ca" / "roots.pem").is_file()


def test_an_issuer_that_did_not_issue_the_certificate_is_refused(install: Path, tmp_path: Path) -> None:
    cert, key, _root = _private_pki(tmp_path / "a", ["box"])
    _cert, _key, other_root = _private_pki(tmp_path / "b", ["box"])
    with pytest.raises(tls.TlsError, match="not issued by"):
        tls.use_own(install, cert, key, ca_path=other_root)


# ── renewing ──────────────────────────────────────────────────────


def test_a_certificate_made_here_is_issued_again_only_when_due(install: Path) -> None:
    tls.generate(install, ["console.example.org"])
    assert tls.renew(install).action == "current"
    later = datetime.now(UTC) + timedelta(days=tls.LEAF_DAYS - 10)
    result = tls.renew(install, now=later)
    assert result.action == "renewed"
    assert "days left" in result.message
    assert result.summary is not None
    assert "console.example.org" in result.summary.names, "the names it had are kept"
    assert not result.trust_changed, "the same authority: nothing for the machines or the browsers"


def test_a_new_address_of_this_machine_gets_a_certificate_that_lists_it(
    install: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tls.generate(install)
    monkeypatch.setattr(tls, "machine_names", lambda: [*NAMES, "10.250.10.99"])
    result = tls.renew(install)
    assert result.action == "renewed"
    assert "10.250.10.99" in result.message
    assert result.summary is not None
    assert "10.250.10.99" in result.summary.names


def test_the_operators_renewed_files_are_taken_from_where_they_came(install: Path, tmp_path: Path) -> None:
    cert, key, root = _private_pki(tmp_path / "live", ["box"])
    tls.use_own(install, cert, key, ca_path=root)
    assert tls.renew(install).action == "current", "the same files: nothing to do"
    served = tls.current(install)

    # certbot and the like renew in place: new files at the same paths.
    renewed_cert, renewed_key, renewed_root = _private_pki(tmp_path / "next", ["box"])
    cert.write_bytes(renewed_cert.read_bytes())
    key.write_bytes(renewed_key.read_bytes())
    root.write_bytes(renewed_root.read_bytes())
    result = tls.renew(install)
    assert result.action == "reimported", result.message
    now_served = tls.current(install)
    assert served is not None
    assert now_served is not None
    assert now_served.fingerprint_sha256 != served.fingerprint_sha256


def test_renewed_files_that_cannot_be_used_leave_the_served_certificate_alone(
    install: Path, tmp_path: Path
) -> None:
    src, other = tmp_path / "live", tmp_path / "other"
    src.mkdir()
    other.mkdir()
    cert, key = _issue(src, ["box"])
    tls.use_own(install, cert, key)
    before = (install / "tls" / "edge.crt").read_bytes()
    other_cert, _ = _issue(other, ["box"])
    cert.write_bytes(other_cert.read_bytes())  # a new certificate, still the old key
    result = tls.renew(install)
    assert result.action == "expiring"
    assert "cannot be used" in result.message
    assert (install / "tls" / "edge.crt").read_bytes() == before


def test_a_certificate_close_to_its_end_with_no_renewed_files_warns(install: Path, tmp_path: Path) -> None:
    cert, key = _issue(tmp_path, ["box"], days=20)
    tls.use_own(install, cert, key)
    result = tls.renew(install)
    assert result.action == "expiring"
    assert "renew it there" in result.message
    assert tls.renew(tmp_path / "nothing-here").action == "off"


# ── what the backend is told, and the daily run ───────────────────


def test_the_backend_is_told_this_hosts_addresses_and_ports(install: Path) -> None:
    tls.write_edge_info(install, http_port=8080, https_port=8443, renews="daily")
    info = json.loads((install / "tls" / "edge.json").read_text())
    assert info == {"hosts": ["box", "10.250.10.61"], "http_port": 8080, "https_port": 8443, "renews": "daily"}


def test_the_renewal_runs_every_morning_as_a_user_timer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(schedule, "_cli", lambda: ["/home/ops/.local/bin/llmport"])
    job = schedule.tls_renewal(tmp_path / "logs" / "tls-renew.log")
    units = schedule.job_units(job, tmp_path / "llmport.yaml")
    service = units["llmport-tls-renew.service"]
    assert "ExecStart=/home/ops/.local/bin/llmport tls renew" in service
    assert f"LLMPORT_CONFIG={tmp_path / 'llmport.yaml'}" in service
    timer = units["llmport-tls-renew.timer"]
    assert "OnCalendar=*-*-* 04:40:00" in timer
    assert "Persistent=true" in timer
    assert schedule.job_entry(job, None).endswith("# llmport-tls-renew")


def test_renew_says_what_it_did_and_fails_when_it_cannot_help(
    install: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = LlmportConfig(install_dir=str(install))
    monkeypatch.setattr(tls_cmd, "load_config", lambda: cfg)
    restarted: list[Path] = []
    monkeypatch.setattr(tls_cmd, "apply_to_proxy", lambda d, **_k: restarted.append(d) or True)
    monkeypatch.setattr(tls_cmd.time, "sleep", lambda _s: None)
    runner = CliRunner()

    assert runner.invoke(tls_cmd.tls_group, ["renew"]).exit_code == 0, "HTTPS off: nothing to do"
    tls.generate(install)
    result = runner.invoke(tls_cmd.tls_group, ["renew"])
    assert result.exit_code == 0, result.output
    assert not restarted, "nothing due: the proxy is left alone"
    result = runner.invoke(tls_cmd.tls_group, ["renew", "--force"])
    assert result.exit_code == 0, result.output
    assert restarted == [install]
    assert json.loads((install / "tls" / "edge.json").read_text())["https_port"] == 443

    cert, key = _issue(tmp_path, ["box"], days=10)
    tls.use_own(install, cert, key)
    result = runner.invoke(tls_cmd.tls_group, ["renew"])
    assert result.exit_code == 1, "about to run out, and nothing here can renew it"

    status = " ".join(runner.invoke(tls_cmd.tls_group, ["status"]).output.split())
    assert "taken again from" in status
    assert "days left" in status
