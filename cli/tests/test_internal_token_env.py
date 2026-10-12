"""The internal token the PII service reports to the backend with reaches both, and survives an upgrade."""

from __future__ import annotations

from pathlib import Path

from faden_cli.core.env_gen import default_env_vars, read_env_file, write_env_file

BACKEND = "FADEN_BACKEND_INTERNAL_TOKEN"
PII = "FADEN_PII_BACKEND_TOKEN"


def test_both_sides_get_the_same_generated_token() -> None:
    env = default_env_vars()
    assert env[BACKEND]
    assert env[BACKEND] == env[PII]


def test_an_old_env_gets_a_token_on_upgrade_and_keeps_it_after(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("POSTGRES_PASSWORD=keep\nFADEN_MCP_SERVICE_TOKEN=old\n", encoding="utf-8")
    # What `faden upgrade` does: generated defaults, the file's own values on top.
    env = default_env_vars()
    env.update(read_env_file(path))
    write_env_file(path, env)
    first = read_env_file(path)
    assert first["POSTGRES_PASSWORD"] == "keep"
    assert first[BACKEND] and first[BACKEND] == first[PII]

    # Regenerating with new random values keeps the pair that is there.
    write_env_file(path, default_env_vars(), preserve_secrets=True)
    second = read_env_file(path)
    assert second[BACKEND] == first[BACKEND]
    assert second[PII] == first[PII]
