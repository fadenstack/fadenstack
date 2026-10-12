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


# ── The PII service's own routes ──────────────────────────────────

PII_GROUP = (
    "FADEN_GATEWAY_PII_SERVICE_TOKEN",
    "FADEN_BACKEND_PII_SERVICE_TOKEN",
    "FADEN_MCP_PII_SERVICE_TOKEN",
    "FADEN_PII_SERVICE_TOKEN",
)


def test_a_new_install_gets_one_pii_service_token_apart_from_the_internal_one() -> None:
    env = default_env_vars(admin_password="x")
    assert env[PII_GROUP[0]]
    assert len({env[key] for key in PII_GROUP}) == 1, "callers and receiver hold one value"
    assert env[PII_GROUP[0]] != env[BACKEND], "the internal token is the backend's, not the PII service's"


def test_an_upgrade_adds_the_pii_tokens_and_keeps_them_after(tmp_path: Path) -> None:
    from faden_cli.core.env_gen import fill_required_env

    path = tmp_path / ".env"
    values = {k: v for k, v in default_env_vars(admin_password="x").items() if k not in PII_GROUP}
    write_env_file(path, values)

    fill_required_env(path)
    first = read_env_file(path)
    assert first[PII_GROUP[0]] and len({first[key] for key in PII_GROUP}) == 1

    # A second upgrade, and a regenerated file, keep it.
    fill_required_env(path)
    write_env_file(path, default_env_vars(admin_password="x"), preserve_secrets=True)
    assert {read_env_file(path)[key] for key in PII_GROUP} == {first[PII_GROUP[0]]}


def test_dev_gets_the_pii_tokens_and_the_host_run_modules_share_them() -> None:
    from faden_cli.core.dev_modules import caller_env, module_env
    from faden_cli.core.env_gen import dev_env_vars
    from faden_cli.core.registry import MODULES

    dev = dev_env_vars()
    assert all(dev[key] for key in PII_GROUP)

    pii, mcp = MODULES["pii"], MODULES["mcp"]
    shared: dict[str, str] = {}
    receiver = module_env(pii, shared=shared, running=[pii, mcp])["FADEN_PII_SERVICE_TOKEN"]
    from_mcp = module_env(mcp, shared=shared, running=[pii, mcp])["FADEN_MCP_PII_SERVICE_TOKEN"]
    gateway = caller_env("GATEWAY", shared=shared, running=[pii])["FADEN_GATEWAY_PII_SERVICE_TOKEN"]
    backend = caller_env("BACKEND", shared=shared, running=[pii])["FADEN_BACKEND_PII_SERVICE_TOKEN"]
    assert receiver and receiver == from_mcp == gateway == backend
    assert "FADEN_MCP_PII_SERVICE_TOKEN" not in module_env(mcp, shared=shared, running=[mcp])
