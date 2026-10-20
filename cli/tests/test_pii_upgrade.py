"""An existing installation receives the module's runtime updates without losing settings."""

from pathlib import Path

from click.testing import CliRunner
import pytest

from test_provenance_on_by_default import older_install  # noqa: F401
from faden_cli.commands import upgrade
from faden_cli.core import pii_runtime
from faden_cli.core.env_gen import read_env_file, set_env_values


def test_upgrade_calls_shared_pii_step_after_backup_and_keeps_module_settings(older_install, monkeypatch):
    config = older_install.parent / "faden.yaml"
    config.write_text(config.read_text().replace("profiles: [mcp]", "profiles: [pii, mcp]"))
    env = older_install / ".env"
    set_env_values(env, {"FADEN_PII_SERVICE_TOKEN": "keep-pii-token", "FADEN_PII_BACKEND": "presidio"})
    calls = []

    def configure(path, **options):
        calls.append(options)
        assert read_env_file(path)["FADEN_PII_SERVICE_TOKEN"] == "keep-pii-token"
        return False

    monkeypatch.setattr(pii_runtime, "deploy_step", configure)
    result = CliRunner().invoke(upgrade.upgrade_cmd, ["-y", "--no-backup", "--no-build", "--skip-doctor"])
    assert result.exit_code == 0, result.output
    assert len(calls) == 1 and calls[0]["yes"] and calls[0]["upgrade"]
    assert read_env_file(env)["FADEN_PII_SERVICE_TOKEN"] == "keep-pii-token"
    assert "pii" in config.read_text() and "mcp" in config.read_text()


def test_upgrade_dry_run_does_not_prompt_download_or_write_pii(older_install, monkeypatch):
    config = older_install.parent / "faden.yaml"
    config.write_text(config.read_text().replace("profiles: [mcp]", "profiles: [pii, mcp]"))
    before = (older_install / ".env").read_bytes()
    monkeypatch.setattr(pii_runtime, "deploy_step", lambda *a, **kw: pytest.fail("dry-run mutated PII"))
    result = CliRunner().invoke(upgrade.upgrade_cmd, ["--dry-run"])
    assert result.exit_code == 0, result.output
    assert "PII:" in result.output
    assert (older_install / ".env").read_bytes() == before


def test_old_gpu_to_cpu_migration_is_described_truthfully_in_dry_run(older_install):
    config = older_install.parent / "faden.yaml"
    config.write_text(config.read_text().replace("profiles: [mcp]", "profiles: [pii, mcp]"))
    env = older_install / ".env"
    set_env_values(env, {"FADEN_PII_BACKEND": "gliner2", "FADEN_PII_EXECUTION_PROFILE": "gpu-shared"})
    before = env.read_bytes()
    result = CliRunner().invoke(upgrade.upgrade_cmd, ["--dry-run", "--pii-profile", "cpu"])
    assert result.exit_code == 0, result.output
    assert "PII detector: patronus-onnx-v1" in result.output
    assert env.read_bytes() == before
