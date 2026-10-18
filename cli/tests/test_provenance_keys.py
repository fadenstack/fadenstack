"""The provenance engine's keys: its signing certificate from the install's authority, and its hash keys.

``faden-provenance`` signs C2PA manifests on generated pictures (EU AI Act Art. 50(2)) with a key whose
certificate the install's own authority issues, and keys the hashes of what it records. The keys live in
``.env`` with the other secrets, reach the engine as files under ``/run/secrets``, and every hash-key version
ever used stays: what was hashed with it is checked with it.
"""

from __future__ import annotations

import base64
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from click.testing import CliRunner
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, ExtensionOID, NameOID
from test_secret_rotation import FakeInstall

from faden_cli.commands import deploy
from faden_cli.commands import up as up_module
from faden_cli.commands import upgrade as upgrade_module
from faden_cli.core import backup, bootstrap, clickhouse, compose, schedule, secret_files, tls
from faden_cli.core import provenance_keys as pk
from faden_cli.core import secret_rotation as rot
from faden_cli.core.env_gen import default_env_vars, read_env_file, write_env_file

SHARED = Path(__file__).resolve().parents[2] / "shared"
KEY_LINE = re.compile(r"[0-9]+:[A-Za-z0-9+/]{43}=")


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


@pytest.fixture
def install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An install's folder: its .env and its authority."""
    monkeypatch.setattr(tls, "machine_names", lambda: ["localhost", "127.0.0.1", "::1"])
    root = tmp_path / "install"
    root.mkdir()
    write_env_file(root / ".env", {"VERSION": "0.4.5", "POSTGRES_PASSWORD": "keep-me"})
    tls.ensure_ca(root)
    return root


def _chain(env: dict[str, str]) -> list[x509.Certificate]:
    return x509.load_pem_x509_certificates(base64.b64decode(env[pk.SIGNING_CERTS]))


def _authority(install: Path) -> x509.Certificate:
    return x509.load_pem_x509_certificate((install / "tls-ca" / "ca.crt").read_bytes())


def _said(result: Any) -> str:
    return " ".join(result.output.split())


# ── The certificate ───────────────────────────────────────────────


def test_the_signer_is_issued_by_the_installs_authority_with_exactly_these_extensions(install: Path) -> None:
    now = _now()
    key_b64, certs_b64 = pk.issue_signer(install, now=now)

    assert "\n" not in key_b64 and "\n" not in certs_b64, "one .env line each"
    leaf, ca = x509.load_pem_x509_certificates(base64.b64decode(certs_b64))
    assert ca == _authority(install), "leaf first, then the install's authority"
    leaf.verify_directly_issued_by(ca)

    der = base64.b64decode(key_b64)
    key = serialization.load_der_private_key(der, password=None)
    assert isinstance(key, ec.EllipticCurvePrivateKey) and key.curve.name == "secp256r1"
    pkcs8 = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption())
    assert der == pkcs8, "PKCS#8 DER"
    spki = (serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    assert key.public_key().public_bytes(*spki) == leaf.public_key().public_bytes(*spki)

    assert leaf.subject.rfc4514_string() == (
        f"CN=Fadenstack provenance signer {pk.installation_id(ca)},OU=Provenance,O=Fadenstack"
    )
    assert re.fullmatch(r"[0-9a-f]{16}", pk.installation_id(ca))
    assert leaf.issuer == ca.subject
    assert leaf.not_valid_before_utc == now - timedelta(minutes=5)
    assert leaf.not_valid_after_utc == now + timedelta(days=365)

    extensions = {ext.oid: ext for ext in leaf.extensions}
    assert set(extensions) == {
        ExtensionOID.BASIC_CONSTRAINTS, ExtensionOID.KEY_USAGE, ExtensionOID.EXTENDED_KEY_USAGE,
        ExtensionOID.SUBJECT_KEY_IDENTIFIER, ExtensionOID.AUTHORITY_KEY_IDENTIFIER,
    }
    basic = extensions[ExtensionOID.BASIC_CONSTRAINTS]
    assert basic.critical and basic.value.ca is False
    usage = extensions[ExtensionOID.KEY_USAGE]
    assert usage.critical
    flags = usage.value
    assert [
        flags.digital_signature, flags.content_commitment, flags.key_encipherment, flags.data_encipherment,
        flags.key_agreement, flags.key_cert_sign, flags.crl_sign,
    ] == [True, False, False, False, False, False, False], "digitalSignature only"
    assert list(extensions[ExtensionOID.EXTENDED_KEY_USAGE].value) == [
        x509.ObjectIdentifier("1.3.6.1.4.1.62558.2.1"),  # C2PA claim signing
        x509.ObjectIdentifier("1.3.6.1.5.5.7.3.36"),  # documentSigning
        ExtendedKeyUsageOID.EMAIL_PROTECTION,
    ]
    assert extensions[ExtensionOID.SUBJECT_KEY_IDENTIFIER].value == x509.SubjectKeyIdentifier.from_public_key(
        leaf.public_key()
    )
    authority_id = extensions[ExtensionOID.AUTHORITY_KEY_IDENTIFIER].value.key_identifier
    assert authority_id == x509.SubjectKeyIdentifier.from_public_key(ca.public_key()).digest


def test_the_signer_names_no_host_company_or_person(install: Path) -> None:
    _, certs = pk.issue_signer(install)
    leaf = x509.load_pem_x509_certificates(base64.b64decode(certs))[0]
    assert [a.oid for a in leaf.subject] == [
        NameOID.ORGANIZATION_NAME, NameOID.ORGANIZATIONAL_UNIT_NAME, NameOID.COMMON_NAME,
    ]
    assert leaf.subject.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)[0].value == "Fadenstack"


def test_an_install_without_an_authority_gets_one_and_publishes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tls, "machine_names", lambda: ["localhost"])
    _, certs = pk.issue_signer(tmp_path)
    ca = x509.load_pem_x509_certificates(base64.b64decode(certs))[1]
    assert ca == _authority(tmp_path)
    assert (tmp_path / "tls" / "ca.crt").read_bytes() == ca.public_bytes(serialization.Encoding.PEM)


# ── When it is issued again ───────────────────────────────────────


def test_a_signer_is_due_when_missing_unreadable_or_not_its_key(install: Path) -> None:
    assert pk.signer_due(install, {}) == "there was none"
    key, certs = pk.issue_signer(install)
    assert pk.signer_due(install, {pk.SIGNING_KEY: key, pk.SIGNING_CERTS: certs}) is None
    assert pk.signer_due(install, {pk.SIGNING_KEY: key, pk.SIGNING_CERTS: "bm90IGEgY2VydA=="}) == (
        "the one in .env does not read"
    )
    other_key, _ = pk.issue_signer(install)
    assert pk.signer_due(install, {pk.SIGNING_KEY: other_key, pk.SIGNING_CERTS: certs}) == (
        "the key in .env does not belong to its certificate"
    )


def test_a_signer_is_due_30_days_before_it_runs_out(install: Path) -> None:
    issued = _now()
    key, certs = pk.issue_signer(install, now=issued)
    env = {pk.SIGNING_KEY: key, pk.SIGNING_CERTS: certs}
    assert pk.signer_due(install, env, now=issued + timedelta(days=365 - 30)) is None
    assert pk.signer_due(install, env, now=issued + timedelta(days=365 - 29)) == "it had 29 days left"
    assert pk.signer_due(install, env, now=issued + timedelta(days=400)) == "it had 0 days left"


def test_a_signer_is_due_once_its_authority_is_replaced(install: Path) -> None:
    key, certs = pk.issue_signer(install)
    env = {pk.SIGNING_KEY: key, pk.SIGNING_CERTS: certs}
    old_ca = _authority(install)
    # The authority is renewed once it would not outlive a certificate it signs (core/tls.py).
    tls._load_or_make_ca(install, old_ca.not_valid_after_utc - timedelta(days=800))  # noqa: SLF001
    assert _authority(install) != old_ca

    assert pk.signer_due(install, env) == "its authority is no longer the install's current one"
    _, renewed = pk.issue_signer(install)
    leaf, ca = x509.load_pem_x509_certificates(base64.b64decode(renewed))
    assert ca == _authority(install)
    leaf.verify_directly_issued_by(ca)


# ── In .env ───────────────────────────────────────────────────────


def test_ensure_writes_the_three_keys_once(install: Path) -> None:
    env_path = install / ".env"
    done = pk.ensure(install, env_path)
    assert done.issued == "there was none" and done.hash_key_made
    env = read_env_file(env_path)
    assert env["POSTGRES_PASSWORD"] == "keep-me", "the rest of .env stays"
    assert KEY_LINE.fullmatch(env[pk.HASH_KEYS]) and env[pk.HASH_KEYS].startswith("1:")
    assert len(base64.b64decode(env[pk.HASH_KEYS].split(":", 1)[1])) == 32
    _chain(env)[0].verify_directly_issued_by(_authority(install))
    lines = env_path.read_text(encoding="utf-8").splitlines()
    assert sum(line.startswith(tuple(f"{k}=" for k in pk.KEYS)) for line in lines) == 3, "a line each"

    before = env_path.read_bytes()
    again = pk.ensure(install, env_path)
    assert again.issued is None and not again.hash_key_made
    assert env_path.read_bytes() == before, "nothing changes while they serve"


def test_ensure_renews_a_signer_that_is_due_and_keeps_the_hash_keys(install: Path) -> None:
    env_path = install / ".env"
    pk.ensure(install, env_path, now=_now() - timedelta(days=340))
    before = read_env_file(env_path)

    done = pk.ensure(install, env_path)

    after = read_env_file(env_path)
    assert done.issued is not None and "days left" in done.issued
    assert after[pk.SIGNING_KEY] != before[pk.SIGNING_KEY], "a new key with the new certificate"
    assert _chain(after)[0].not_valid_after_utc > _chain(before)[0].not_valid_after_utc
    assert after[pk.HASH_KEYS] == before[pk.HASH_KEYS]


def test_unreadable_hash_keys_are_reported_and_never_replaced(
    install: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    env_path = install / ".env"
    write_env_file(env_path, {**read_env_file(env_path), pk.HASH_KEYS: "1:not base64!"})

    pk.ensure_step(install, env_path)

    assert read_env_file(env_path)[pk.HASH_KEYS] == "1:not base64!"
    said = " ".join(capsys.readouterr().out.split())
    assert "FADEN_PROVENANCE_HASH_KEYS version 1 is not base64" in said


def test_no_key_is_ever_said(install: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env_path = install / ".env"
    pk.ensure_step(install, env_path)
    env = read_env_file(env_path)
    said = capsys.readouterr().out
    assert "Provenance signing certificate issued" in " ".join(said.split())
    hash_key = env[pk.HASH_KEYS].split(":", 1)[1]
    for secret in (env[pk.SIGNING_KEY][:40], hash_key):
        assert secret not in said


def test_an_authority_that_does_not_read_is_a_warning_and_changes_nothing(
    install: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    (install / "tls-ca" / "ca.key").write_text("not a key", encoding="utf-8")
    env_path = install / ".env"
    before = env_path.read_bytes()

    pk.ensure_step(install, env_path)

    assert "could not be issued" in " ".join(capsys.readouterr().out.split())
    assert env_path.read_bytes() == before


# ── Hash keys ─────────────────────────────────────────────────────


def test_a_new_version_goes_after_the_others_which_stay() -> None:
    first = pk.add_hash_key("")
    second = pk.add_hash_key(first)
    third = pk.add_hash_key(second)
    assert third.startswith(second + ",3:")
    versions = pk.parse_hash_keys(third)
    assert sorted(versions) == [1, 2, 3] and len(set(versions.values())) == 3


@pytest.mark.parametrize("value", ["2", "x:AAAA", "0:AAAA", "1:", "1:@@@", "1:AAAA,1:BBBB"])
def test_a_value_that_does_not_read_is_refused_without_its_content(value: str) -> None:
    with pytest.raises(pk.ProvenanceKeyError) as refused:
        pk.parse_hash_keys(value)
    for content in ("AAAA", "BBBB", "@@@"):
        assert content not in str(refused.value), "names the key, never its value"


def test_merging_loses_no_key() -> None:
    a, b, c = (pk.new_hash_key() for _ in range(3))
    assert pk.merge_hash_keys(f"1:{a}", f"1:{a},2:{b}") == f"1:{a},2:{b}"
    # Another install's version 1 (a restore onto a new install): kept, as a version of its own.
    assert pk.merge_hash_keys(f"1:{a}", f"1:{c}") == f"1:{a},2:{c}"
    assert pk.merge_hash_keys(f"1:{a},3:{b}", f"2:{c}") == f"1:{a},2:{c},3:{b}"


def test_a_value_written_over_them_that_does_not_read_leaves_them_as_they_were(install: Path) -> None:
    env_path = install / ".env"
    pk.ensure(install, env_path)
    before = read_env_file(env_path)[pk.HASH_KEYS]
    write_env_file(env_path, {**read_env_file(env_path), pk.HASH_KEYS: "1:@@@"})
    assert read_env_file(env_path)[pk.HASH_KEYS] == before


def test_regenerating_env_keeps_every_hash_key_version(install: Path) -> None:
    """``faden deploy --force-env`` writes every secret anew; the hash keys of the old file stay."""
    env_path = install / ".env"
    pk.ensure(install, env_path)
    current = read_env_file(env_path)
    write_env_file(env_path, {**current, pk.HASH_KEYS: pk.add_hash_key(current[pk.HASH_KEYS])})
    before = read_env_file(env_path)

    write_env_file(env_path, default_env_vars(), preserve_secrets=False)

    after = read_env_file(env_path)
    assert after[pk.HASH_KEYS] == before[pk.HASH_KEYS]
    assert pk.SIGNING_KEY not in after, "a new signer is issued for a new .env"


def test_up_env_gen_keeps_the_signer_and_the_hash_keys(install: Path) -> None:
    """``faden up --env-gen`` writes .env from the defaults with the secrets kept: the provenance keys too."""
    env_path = install / ".env"
    pk.ensure(install, env_path)
    before = read_env_file(env_path)

    write_env_file(env_path, default_env_vars(), preserve_secrets=True)

    after = read_env_file(env_path)
    assert {k: after[k] for k in pk.KEYS} == {k: before[k] for k in pk.KEYS}


# ── Secret files and the compose file ─────────────────────────────


def test_the_three_are_secret_files_and_none_is_required() -> None:
    assert set(pk.KEYS) <= set(secret_files.SECRET_FILES)
    assert not set(pk.KEYS) & secret_files.REQUIRED, "the module can be switched off"


def test_only_the_engine_mounts_them_where_it_reads_them() -> None:
    doc = yaml.safe_load((SHARED / "docker-compose.yaml").read_text(encoding="utf-8"))
    files = {name: spec["file"] for name, spec in doc["secrets"].items()}
    mounts = {
        service: {entry["target"]: files[entry["source"]] for entry in spec.get("secrets", [])}
        for service, spec in doc["services"].items()
    }
    assert mounts["faden-provenance"] == {key: f"./secrets/{key}" for key in pk.KEYS}
    others = {service for service, mounted in mounts.items() if set(mounted) & set(pk.KEYS)}
    assert others == {"faden-provenance"}
    assert "provenance" in doc["services"]["faden-provenance"]["profiles"]


def test_an_install_without_them_still_starts_every_other_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ran: list[list[str]] = []
    def run(cmd: list[str], **_: object) -> compose.subprocess.CompletedProcess[str]:
        ran.append(cmd)
        return compose.subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(compose, "_run", run)
    monkeypatch.setattr(compose.shutil, "which", lambda name: name)
    (tmp_path / "docker-compose.yaml").write_text(
        (SHARED / "docker-compose.yaml").read_text(encoding="utf-8"), encoding="utf-8",
    )
    write_env_file(tmp_path / ".env", {
        "FADEN_GATEWAY_ENCRYPTION_KEY": "chat-key", "FADEN_BACKEND_SETTINGS_MASTER_KEY": "master-key",
        "FADEN_BACKEND_CREDENTIAL_PEPPER": "pepper", "FADEN_MCP_ENCRYPTION_KEY": "mcp-key",
    })
    ctx = compose.ComposeContext(
        compose_files=[tmp_path / "docker-compose.yaml"], env_file=tmp_path / ".env", project_dir=tmp_path,
    )

    assert compose.up(ctx) == 0
    assert ran and "up" in ran[-1], "not refused for want of them"
    for key in pk.KEYS:
        assert (tmp_path / "secrets" / key).read_bytes() == b"", "an empty file, as for the MCP key"


def test_the_files_hold_what_env_holds(install: Path) -> None:
    pk.ensure(install, install / ".env")
    secret_files.write(install, install / ".env")
    env = read_env_file(install / ".env")
    for key in pk.KEYS:
        assert (install / "secrets" / key).read_text(encoding="utf-8") == env[key]


# ── faden deploy, upgrade, up ─────────────────────────────────────


@pytest.fixture
def deployable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A checkout's shared/ to deploy, and a deploy that reaches no Docker and no backend."""
    from faden_cli.commands import ports as ports_cmd  # noqa: PLC0415
    from faden_cli.commands import runtime_images as runtime_images_cmd  # noqa: PLC0415
    from faden_cli.core import backup_key, chat_retention  # noqa: PLC0415

    workspace = tmp_path / "ws"
    (workspace / "shared").mkdir(parents=True)
    (workspace / "shared" / "docker-compose.yaml").write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setenv("FADEN_CONFIG", str(tmp_path / "faden.yaml"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(tls, "machine_names", lambda: ["localhost", "127.0.0.1", "::1"])
    monkeypatch.setattr(ports_cmd, "port_conflicts", lambda *a, **k: [])
    monkeypatch.setattr(compose, "has_nvidia_gpu", lambda: False)
    monkeypatch.setattr(compose, "pull", lambda *a, **k: 0)
    monkeypatch.setattr(compose, "report_inactive_modules", lambda ctx: None)
    monkeypatch.setattr(deploy, "compose_up", lambda *a, **k: 0)
    monkeypatch.setattr(deploy, "_sync_postgres_password", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "retire_fixed_logins", lambda *a, **k: [])
    monkeypatch.setattr(bootstrap, "wait_for_backend", lambda *a, **k: False)
    monkeypatch.setattr(runtime_images_cmd, "run_step", lambda *a, **k: True)
    monkeypatch.setattr(schedule, "available", lambda: False)
    monkeypatch.setattr(chat_retention, "apply_retention", lambda days: None)
    monkeypatch.setattr(backup_key, "key_step", lambda *a, **k: None)
    return workspace


def _deploy(workspace: Path, *extra: str) -> Any:
    return CliRunner().invoke(deploy.deploy_cmd, [
        str(workspace), "-y", "--skip-doctor", "--tls", "off", "--no-update-check",
        "--chat-retention-days", "30", "--runtime-images", "none",
        "--http-port", "8080", "--https-port", "8443", *extra,
    ])


def test_deploy_issues_the_signer_and_makes_a_hash_key(deployable: Path) -> None:
    shared = deployable / "shared"

    result = _deploy(deployable)

    assert result.exit_code == 0, result.output
    env = read_env_file(shared / ".env")
    leaf, ca = _chain(env)
    assert ca == _authority(shared)
    leaf.verify_directly_issued_by(ca)
    assert KEY_LINE.fullmatch(env[pk.HASH_KEYS])
    assert "Provenance signing certificate issued" in _said(result)


def test_deploy_force_env_keeps_the_hash_keys_and_issues_a_new_signer(deployable: Path) -> None:
    shared = deployable / "shared"
    assert _deploy(deployable).exit_code == 0
    first = read_env_file(shared / ".env")

    result = _deploy(deployable, "--force-env")

    assert result.exit_code == 0, result.output
    after = read_env_file(shared / ".env")
    assert after[pk.HASH_KEYS] == first[pk.HASH_KEYS]
    assert after[pk.SIGNING_KEY] != first[pk.SIGNING_KEY]
    assert after["POSTGRES_PASSWORD"] != first["POSTGRES_PASSWORD"], "the rest was written anew"


@pytest.fixture
def older_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An install from before the provenance keys, and an upgrade that reaches no Docker."""
    shared = tmp_path / "shared"
    (shared / "rabbitmq").mkdir(parents=True)
    (shared / "docker-compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (shared / ".env").write_text(
        "RABBITMQ_ADMIN_USER=admin\nRABBITMQ_ADMIN_PASS=adminpw\nRABBITMQ_BACKEND_PASS=backendpw\n"
        "RABBITMQ_API_PASS=apipw\n",
        encoding="utf-8",
    )
    config = tmp_path / "faden.yaml"
    config.write_text(
        f"version: 1\ninstall_dir: {shared.as_posix()}\ncompose_file: docker-compose.yaml\n"
        "profiles: [provenance]\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FADEN_CONFIG", str(config))
    monkeypatch.setattr(tls, "machine_names", lambda: ["localhost", "127.0.0.1", "::1"])
    monkeypatch.setattr(compose, "has_nvidia_gpu", lambda: False)
    monkeypatch.setattr(compose, "foreign_containers", lambda ctx: [])
    monkeypatch.setattr(upgrade_module, "compose_up", lambda *a, **k: 0)
    monkeypatch.setattr(clickhouse, "drop_stale_logs", lambda ctx: [])
    monkeypatch.setattr(bootstrap, "wait_for_backend", lambda *a, **k: True)
    monkeypatch.setattr(deploy, "_sync_postgres_password", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "retire_fixed_logins", lambda *a, **k: [])
    return shared


def _upgrade() -> Any:
    flags = ["-y", "--no-backup", "--no-build", "--skip-doctor"]
    return CliRunner().invoke(upgrade_module.upgrade_cmd, flags)


def test_an_upgrade_gives_an_older_env_the_keys_and_keeps_them_after(older_install: Path) -> None:
    result = _upgrade()

    assert result.exit_code == 0, result.output
    env = read_env_file(older_install / ".env")
    _chain(env)[0].verify_directly_issued_by(_authority(older_install))
    assert env[pk.HASH_KEYS].startswith("1:")
    assert env["RABBITMQ_API_PASS"] == "apipw"

    assert _upgrade().exit_code == 0
    again = read_env_file(older_install / ".env")
    assert {k: again[k] for k in pk.KEYS} == {k: env[k] for k in pk.KEYS}, "kept while they serve"


def test_an_upgrade_issues_a_signer_that_is_due_again(older_install: Path) -> None:
    tls.ensure_ca(older_install)
    pk.ensure(older_install, older_install / ".env", now=_now() - timedelta(days=350))
    before = read_env_file(older_install / ".env")

    assert _upgrade().exit_code == 0

    after = read_env_file(older_install / ".env")
    assert after[pk.SIGNING_CERTS] != before[pk.SIGNING_CERTS]
    assert after[pk.HASH_KEYS] == before[pk.HASH_KEYS]


def test_up_makes_what_env_lacks(older_install: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(up_module, "compose_up", lambda *a, **k: 0)
    monkeypatch.setattr(up_module, "report_inactive_modules", lambda ctx: None)

    result = CliRunner().invoke(up_module.up_cmd, [])

    assert result.exit_code == 0, result.output
    env = read_env_file(older_install / ".env")
    assert all(env.get(key) for key in pk.KEYS)


# ── faden secrets rotate ──────────────────────────────────────────


def _rotatable(install: Path) -> Path:
    env_path = install / ".env"
    values = default_env_vars(profiles=["provenance"])
    values.update({"FADEN_BACKEND_CREDENTIAL_PEPPER": "the-pepper-dddddddddddd"})
    write_env_file(env_path, values)
    pk.ensure(install, env_path)
    return env_path


def _rotate(env_path: Path, only: list[str] | None = None) -> FakeInstall:
    fake = FakeInstall(env_path)
    plan = rot.make_plan(read_env_file(env_path), ["provenance"], only)
    rot.rotate(fake, plan, say=lambda _line: None)
    return fake


def test_rotation_gives_a_new_signer_and_appends_a_hash_key_version(install: Path) -> None:
    env_path = _rotatable(install)
    before = read_env_file(env_path)

    fake = _rotate(env_path, ["provenance"])

    after = read_env_file(env_path)
    assert after[pk.SIGNING_KEY] != before[pk.SIGNING_KEY]
    _chain(after)[0].verify_directly_issued_by(_authority(install))
    assert after[pk.HASH_KEYS].startswith(before[pk.HASH_KEYS] + ",2:"), "version 1 exactly as it was"
    assert fake.names().count("restart:faden-provenance") == 2
    assert not [name for name in fake.names() if name.startswith("job:")], "nothing to re-seal"
    assert after["FADEN_BACKEND_SETTINGS_MASTER_KEY"] == before["FADEN_BACKEND_SETTINGS_MASTER_KEY"]


def test_every_rotation_keeps_every_version(install: Path) -> None:
    env_path = _rotatable(install)
    seen: list[dict[int, str]] = []
    for _ in range(3):
        _rotate(env_path, ["provenance"])
        seen.append(pk.parse_hash_keys(read_env_file(env_path)[pk.HASH_KEYS]))
    assert [sorted(versions) for versions in seen] == [[1, 2], [1, 2, 3], [1, 2, 3, 4]]
    assert seen[0].items() <= seen[1].items() <= seen[2].items()


def test_a_full_rotation_includes_it_when_the_module_runs(install: Path) -> None:
    env_path = _rotatable(install)
    plan = rot.make_plan(read_env_file(env_path), ["provenance"], None)
    assert [s.name for s in plan.scopes] == ["settings", "chat", "provenance"]
    assert [s.name for s in rot.make_plan(read_env_file(env_path), [], None).scopes] == ["settings", "chat"]
    with pytest.raises(rot.RotationError, match="provenance needs the provenance module"):
        rot.make_plan(read_env_file(env_path), [], ["provenance"])


def test_hash_keys_that_do_not_read_stop_the_rotation_before_env_changes(install: Path) -> None:
    from faden_cli.core.env_gen import rewrite_env  # noqa: PLC0415

    env_path = _rotatable(install)
    rewrite_env(env_path, set_values={pk.HASH_KEYS: "1:@@@"})  # write_env_file would keep the old ones
    before = env_path.read_bytes()

    with pytest.raises(rot.RotationError, match="Nothing was changed"):
        _rotate(env_path, ["provenance"])

    assert env_path.read_bytes() == before


def test_the_commands_show_and_rotate_it_without_a_key(
    install: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from faden_cli.commands.secrets import _shown, secrets_group  # noqa: PLC0415

    env_path = _rotatable(install)
    states = rot.key_states(read_env_file(env_path), ["provenance"])
    state = next(s for s in states if s.scope.name == "provenance")
    assert _shown(state, read_env_file(env_path), ["provenance"]) == (
        "[green]set[/green], 1 hash-key version(s), all kept"
    )
    assert _shown(state, read_env_file(env_path), []) == "[dim]module off[/dim]"
    cfg = SimpleNamespace(
        install_dir=str(install), install_path=install, env_path=env_path,
        profiles=["provenance"], compose_file="docker-compose.yaml",
    )
    monkeypatch.setattr("faden_cli.commands.secrets.load_config", lambda: cfg)
    monkeypatch.setattr(rot, "ComposeOperations", lambda c: FakeInstall(c.env_path))
    before = read_env_file(env_path)

    status = CliRunner().invoke(secrets_group, ["status"])
    rotated = CliRunner().invoke(secrets_group, ["rotate", "--only", "provenance", "-y"])

    assert status.exit_code == 0, status.output
    assert rotated.exit_code == 0, rotated.output
    assert "FADEN_PROVENANCE_SIGNING_KEY is for the C2PA signatures" in _said(rotated)
    assert len(pk.parse_hash_keys(read_env_file(env_path)[pk.HASH_KEYS])) == 2
    for secret in (before[pk.SIGNING_KEY][:40], before[pk.HASH_KEYS].split(":", 1)[1]):
        assert secret not in status.output + rotated.output


# ── Backup and restore ────────────────────────────────────────────


def test_a_backup_carries_the_three_keys(install: Path, tmp_path: Path) -> None:
    pk.ensure(install, install / ".env")
    snapshot = tmp_path / "backup"
    snapshot.mkdir()

    assert backup.snapshot_env(install / ".env", snapshot) == ".env.bak"

    kept = read_env_file(snapshot / ".env.bak")
    env = read_env_file(install / ".env")
    assert {k: kept[k] for k in pk.KEYS} == {k: env[k] for k in pk.KEYS}


def test_a_restore_keeps_the_versions_added_since_the_backup(install: Path, tmp_path: Path) -> None:
    env_path = install / ".env"
    pk.ensure(install, env_path)
    snapshot = tmp_path / "backup"
    snapshot.mkdir()
    backup.snapshot_env(env_path, snapshot)
    backed_up = read_env_file(env_path)
    _rotate_hash_key(env_path)
    now = read_env_file(env_path)[pk.HASH_KEYS]

    assert backup.restore_env(snapshot, env_path)

    restored = read_env_file(env_path)
    assert restored[pk.HASH_KEYS] == now, "version 2 stays"
    assert restored[pk.SIGNING_KEY] == backed_up[pk.SIGNING_KEY], "the rest is the backup's"


def test_a_restore_onto_another_install_keeps_both_installs_keys(install: Path, tmp_path: Path) -> None:
    env_path = install / ".env"
    pk.ensure(install, env_path)
    snapshot = tmp_path / "backup"
    snapshot.mkdir()
    backup.snapshot_env(env_path, snapshot)
    theirs = read_env_file(env_path)[pk.HASH_KEYS]
    ours = pk.add_hash_key("")  # a new install's own version 1
    env_path.write_text(f"VERSION=0.4.5\n{pk.HASH_KEYS}={ours}\n", encoding="utf-8")

    backup.restore_env(snapshot, env_path)

    versions = pk.parse_hash_keys(read_env_file(env_path)[pk.HASH_KEYS])
    assert versions[1] == theirs.split(":", 1)[1], "the backup's keep their numbers"
    assert versions[2] == ours.split(":", 1)[1]


def test_a_restore_of_an_env_from_before_the_module_keeps_the_installs_keys(
    install: Path, tmp_path: Path,
) -> None:
    env_path = install / ".env"
    snapshot = tmp_path / "backup"
    snapshot.mkdir()
    backup.snapshot_env(env_path, snapshot)  # no provenance keys yet
    pk.ensure(install, env_path)
    ours = read_env_file(env_path)[pk.HASH_KEYS]

    backup.restore_env(snapshot, env_path)

    assert read_env_file(env_path)[pk.HASH_KEYS] == ours


def _rotate_hash_key(env_path: Path) -> None:
    from faden_cli.core.env_gen import rewrite_env  # noqa: PLC0415

    rewrite_env(env_path, set_values={pk.HASH_KEYS: pk.add_hash_key(read_env_file(env_path)[pk.HASH_KEYS])})
