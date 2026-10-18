"""The compose file has no published fallback for any secret; the CLI supplies them, and keeps stored data readable.

It fell back to development values that are in the public repository (the settings master key, the MCP key and
tokens, the Langfuse keys, ``postgres``/``postgres``, Grafana ``admin``/``admin``), so an install started by hand
without them was open to anyone who read it. Each is now ``${NAME:?...}``.

Compose checks every variable of the file for every command, so an older ``.env`` must get the new ones before
anything runs compose. A key that seals stored data and is missing means the install ran on the fallback: its
data is readable only with that value, so the upgrade writes it, never a random one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from faden_cli.core.env_gen import (
    ALLOW_PUBLISHED_KEYS,
    PUBLISHED_DATA_KEYS,
    REQUIRED_KEYS,
    default_env_vars,
    dev_env_vars,
    fill_dev_env,
    fill_required_env,
    read_env_file,
    write_env_file,
)

COMPOSE = Path(__file__).resolve().parents[2] / "shared" / "docker-compose.yaml"
PUBLISHED = (
    "dev-settings-master-key-change-me",
    "dev-mcp-encryption-key-change-me",
    "dev-mcp-service-token",
    "dev-skills-service-token",
    "dev-pii-service-token",
    "myredissecret",
    "miniosecret",
    "mysalt",
    "mysecret",
    "0" * 64,
    "postgres:postgres@",
)


def test_the_compose_file_requires_exactly_these() -> None:
    """Compose requires them, or `faden up` does for the keys it mounts as files (core/secret_files.py)."""
    from faden_cli.core.secret_files import REQUIRED  # noqa: PLC0415

    lines = COMPOSE.read_text(encoding="utf-8").splitlines()
    compose = "\n".join(line for line in lines if not line.lstrip().startswith("#"))
    assert set(re.findall(r"\$\{([A-Z0-9_]+):\?", compose)) | REQUIRED == set(REQUIRED_KEYS)


def test_the_compose_file_falls_back_to_nothing_published() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    assert [value for value in PUBLISHED if value in compose] == []
    for key in REQUIRED_KEYS:
        assert not re.search(r"\$\{" + key + r":-", compose), key


@pytest.mark.parametrize("values", [default_env_vars(), dev_env_vars()], ids=["deploy", "dev"])
def test_deploy_and_dev_write_every_required_key(values: dict[str, str]) -> None:
    assert [key for key in REQUIRED_KEYS if not values.get(key)] == []


def test_deploy_writes_nothing_published() -> None:
    values = default_env_vars()
    assert [key for key in REQUIRED_KEYS if any(value in values[key] for value in PUBLISHED)] == []


def _older(tmp_path: Path, *missing: str, **extra: str) -> Path:
    values = {k: v for k, v in default_env_vars().items() if k not in missing}
    values.update(extra)
    env = tmp_path / ".env"
    write_env_file(env, values)
    return env


def test_an_upgrade_keeps_what_the_install_has(tmp_path: Path) -> None:
    env = _older(tmp_path)
    before = read_env_file(env)

    assert fill_required_env(env, profiles=["mcp"]) == []

    assert read_env_file(env) == before


def test_a_missing_master_key_keeps_the_value_the_install_ran_on(tmp_path: Path) -> None:
    env = _older(tmp_path, "FADEN_BACKEND_SETTINGS_MASTER_KEY", HF_CACHE_DIR="/srv/hf")

    notes = fill_required_env(env)

    after = read_env_file(env)
    assert after["FADEN_BACKEND_SETTINGS_MASTER_KEY"] == "dev-settings-master-key-change-me"
    assert after[ALLOW_PUBLISHED_KEYS] == "true", "or the backend refuses to start on it"
    assert after["HF_CACHE_DIR"] == "/srv/hf", "nothing else changes"
    assert len(notes) == 1 and "faden secrets rotate" in notes[0]


def test_mcp_s_key_is_kept_where_mcp_ran_and_random_where_it_did_not(tmp_path: Path) -> None:
    ran = _older(tmp_path, "FADEN_MCP_ENCRYPTION_KEY")
    fill_required_env(ran, profiles=["mcp"])
    assert read_env_file(ran)["FADEN_MCP_ENCRYPTION_KEY"] == PUBLISHED_DATA_KEYS["FADEN_MCP_ENCRYPTION_KEY"]

    (tmp_path / "other").mkdir()
    never = _older(tmp_path / "other", "FADEN_MCP_ENCRYPTION_KEY")
    assert fill_required_env(never, profiles=[]) == []
    key = read_env_file(never)["FADEN_MCP_ENCRYPTION_KEY"]
    assert key and key not in PUBLISHED_DATA_KEYS.values()
    assert ALLOW_PUBLISHED_KEYS not in read_env_file(never)


def test_langfuse_keys_are_kept_without_the_allowance(tmp_path: Path) -> None:
    env = _older(tmp_path, "LANGFUSE_SALT", "LANGFUSE_ENCRYPTION_KEY")
    fill_required_env(env)
    after = read_env_file(env)
    assert after["LANGFUSE_SALT"] == "mysalt" and after["LANGFUSE_ENCRYPTION_KEY"] == "0" * 64
    assert ALLOW_PUBLISHED_KEYS not in after, "Langfuse does not refuse them; nothing to allow"


def test_a_token_group_holds_one_value(tmp_path: Path) -> None:
    env = _older(tmp_path, "FADEN_GATEWAY_SKILLS_SERVICE_TOKEN", "FADEN_BACKEND_SKILLS_SERVICE_TOKEN")
    kept = read_env_file(env)["FADEN_SKILLS_SERVICE_TOKEN"]

    fill_required_env(env)

    after = read_env_file(env)
    assert after["FADEN_GATEWAY_SKILLS_SERVICE_TOKEN"] == after["FADEN_BACKEND_SKILLS_SERVICE_TOKEN"] == kept


def test_a_new_postgres_password_reaches_langfuse(tmp_path: Path) -> None:
    env = _older(tmp_path, "POSTGRES_PASSWORD")

    fill_required_env(env)

    after = read_env_file(env)
    assert after["POSTGRES_PASSWORD"] and after["POSTGRES_PASSWORD"] != "postgres"
    assert after["LANGFUSE_DATABASE_URL"] == f"postgresql://postgres:{after['POSTGRES_PASSWORD']}@postgres:5432/langfuse"


def test_an_empty_value_counts_as_missing(tmp_path: Path) -> None:
    env = _older(tmp_path, FADEN_MCP_SERVICE_TOKEN="", FADEN_GATEWAY_MCP_SERVICE_TOKEN="", FADEN_BACKEND_MCP_SERVICE_TOKEN="")
    fill_required_env(env)
    text = env.read_text(encoding="utf-8")
    assert text.count("FADEN_MCP_SERVICE_TOKEN=") == 1, "rewritten in place"
    assert read_env_file(env)["FADEN_MCP_SERVICE_TOKEN"]


def test_a_development_env_gets_the_development_values(tmp_path: Path) -> None:
    values = {k: v for k, v in dev_env_vars().items() if not k.startswith(("FADEN_MCP_", "FADEN_SKILLS_"))}
    values["FADEN_MCP_ENCRYPTION_KEY"] = "kept-as-it-is"
    env = tmp_path / ".env"
    write_env_file(env, values)

    added = fill_dev_env(env)

    after = read_env_file(env)
    assert "FADEN_MCP_ENCRYPTION_KEY" not in added and after["FADEN_MCP_ENCRYPTION_KEY"] == "kept-as-it-is"
    assert after["FADEN_MCP_SERVICE_TOKEN"] == "dev-mcp-service-token"
    assert [key for key in REQUIRED_KEYS if not after.get(key)] == []


def test_an_older_install_keeps_its_machines_connected_the_pepper_is_the_master_key(tmp_path: Path) -> None:
    """The machines' credential hashes were keyed with the master key; the new setting must hold that value."""
    env = _older(tmp_path, "FADEN_BACKEND_CREDENTIAL_PEPPER", FADEN_BACKEND_SETTINGS_MASTER_KEY="this-installs-own-master-key")

    fill_required_env(env)

    assert read_env_file(env)["FADEN_BACKEND_CREDENTIAL_PEPPER"] == "this-installs-own-master-key"


def test_an_install_on_the_published_master_key_gets_that_as_its_pepper(tmp_path: Path) -> None:
    env = _older(tmp_path, "FADEN_BACKEND_SETTINGS_MASTER_KEY", "FADEN_BACKEND_CREDENTIAL_PEPPER")

    fill_required_env(env)

    after = read_env_file(env)
    assert after["FADEN_BACKEND_CREDENTIAL_PEPPER"] == after["FADEN_BACKEND_SETTINGS_MASTER_KEY"] == (
        PUBLISHED_DATA_KEYS["FADEN_BACKEND_SETTINGS_MASTER_KEY"]
    )


def test_a_pepper_that_is_set_is_never_replaced(tmp_path: Path) -> None:
    env = _older(tmp_path, FADEN_BACKEND_CREDENTIAL_PEPPER="the-pepper-from-before")

    fill_required_env(env)

    assert read_env_file(env)["FADEN_BACKEND_CREDENTIAL_PEPPER"] == "the-pepper-from-before"


def test_a_new_install_gets_a_pepper_of_its_own() -> None:
    values = default_env_vars()
    assert values["FADEN_BACKEND_CREDENTIAL_PEPPER"] != values["FADEN_BACKEND_SETTINGS_MASTER_KEY"]


def test_regenerating_env_keeps_the_pepper(tmp_path: Path) -> None:
    env = _older(tmp_path, FADEN_BACKEND_CREDENTIAL_PEPPER="the-pepper-from-before")
    write_env_file(env, default_env_vars(), preserve_secrets=True)
    assert read_env_file(env)["FADEN_BACKEND_CREDENTIAL_PEPPER"] == "the-pepper-from-before"


def test_a_development_env_gets_the_master_key_as_its_pepper(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("FADEN_BACKEND_SETTINGS_MASTER_KEY=a-dev-stacks-key\n", encoding="utf-8")
    fill_dev_env(env)
    assert read_env_file(env)["FADEN_BACKEND_CREDENTIAL_PEPPER"] == "a-dev-stacks-key"
