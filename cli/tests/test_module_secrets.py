"""The modules' tokens and keys come from their registry entries, not from lists in env_gen.

PII's, MCP's and skills' token names were written out by hand in four places in env_gen (the secret keys,
the required keys, the token groups, the development values) and once more per module in
``default_env_vars``. A new module had to be added to each, and one missed was a token compose stops on. They
are derived from ``ModuleInfo.secret_groups`` and ``dev_service_token`` now. The lists below are the
hand-written ones as they were: what an install's ``.env`` holds for those modules must not change.
"""

from __future__ import annotations

from pathlib import Path

from faden_cli.core import env_gen
from faden_cli.core.registry import MODULES

PROVENANCE_TOKENS = (
    "FADEN_GATEWAY_PROVENANCE_SERVICE_TOKEN",
    "FADEN_BACKEND_PROVENANCE_SERVICE_TOKEN",
    "FADEN_PROVENANCE_SERVICE_TOKEN",
)

OLD_TOKEN_GROUPS = (
    ("FADEN_GATEWAY_MCP_SERVICE_TOKEN", "FADEN_BACKEND_MCP_SERVICE_TOKEN", "FADEN_MCP_SERVICE_TOKEN"),
    (
        "FADEN_GATEWAY_SKILLS_SERVICE_TOKEN",
        "FADEN_BACKEND_SKILLS_SERVICE_TOKEN",
        "FADEN_SKILLS_SERVICE_TOKEN",
    ),
    (
        "FADEN_GATEWAY_PII_SERVICE_TOKEN",
        "FADEN_BACKEND_PII_SERVICE_TOKEN",
        "FADEN_MCP_PII_SERVICE_TOKEN",
        "FADEN_PII_SERVICE_TOKEN",
    ),
)
OLD_MODULE_SECRETS = (*(key for group in OLD_TOKEN_GROUPS for key in group), "FADEN_MCP_ENCRYPTION_KEY")

OLD_SECRET_KEYS = frozenset({
    "POSTGRES_PASSWORD", "REDIS_AUTH", "MINIO_ROOT_PASSWORD", "CLICKHOUSE_PASSWORD", "GRAFANA_ADMIN_PASSWORD",
    "RABBITMQ_ADMIN_PASS", "RABBITMQ_BACKEND_PASS", "RABBITMQ_API_PASS", "RABBITMQ_PII_PASS",
    "LANGFUSE_NEXTAUTH_SECRET", "LANGFUSE_SALT", "LANGFUSE_ENCRYPTION_KEY",
    "FADEN_BACKEND_SETTINGS_MASTER_KEY", "FADEN_GATEWAY_ENCRYPTION_KEY", "FADEN_BACKEND_CREDENTIAL_PEPPER",
    "USERS_SECRET", "FADEN_BACKEND_RAG_SERVICE_TOKEN", "FADEN_RAG_SERVICE_TOKEN",
    "FADEN_BACKEND_INTERNAL_TOKEN", "FADEN_PII_BACKEND_TOKEN", "FADEN_BACKEND_BOOTSTRAP_TOKEN",
    *OLD_MODULE_SECRETS,
})

OLD_REQUIRED_KEYS = (
    "POSTGRES_PASSWORD", "REDIS_AUTH", "MINIO_ROOT_PASSWORD", "CLICKHOUSE_PASSWORD",
    "LANGFUSE_NEXTAUTH_SECRET", "LANGFUSE_SALT", "LANGFUSE_ENCRYPTION_KEY", "RABBITMQ_ADMIN_PASS",
    "RABBITMQ_API_PASS", "RABBITMQ_BACKEND_PASS", "RABBITMQ_PII_PASS", "GRAFANA_ADMIN_PASSWORD",
    "FADEN_BACKEND_SETTINGS_MASTER_KEY", "FADEN_BACKEND_CREDENTIAL_PEPPER", *OLD_MODULE_SECRETS,
    "FADEN_GATEWAY_ENCRYPTION_KEY",
)

#: What dev_env_vars() wrote for the modules (the old _dev_module_secrets, and the modules' environments).
OLD_DEV_VALUES = {
    "FADEN_GATEWAY_PII_SERVICE_TOKEN": "dev-pii-service-token",
    "FADEN_BACKEND_PII_SERVICE_TOKEN": "dev-pii-service-token",
    "FADEN_MCP_PII_SERVICE_TOKEN": "dev-pii-service-token",
    "FADEN_PII_SERVICE_TOKEN": "dev-pii-service-token",
    "FADEN_GATEWAY_MCP_SERVICE_TOKEN": "dev-mcp-service-token",
    "FADEN_BACKEND_MCP_SERVICE_TOKEN": "dev-mcp-service-token",
    "FADEN_MCP_SERVICE_TOKEN": "dev-mcp-service-token",
    "FADEN_GATEWAY_SKILLS_SERVICE_TOKEN": "dev-skills-service-token",
    "FADEN_BACKEND_SKILLS_SERVICE_TOKEN": "dev-skills-service-token",
    "FADEN_SKILLS_SERVICE_TOKEN": "dev-skills-service-token",
    "FADEN_MCP_ENCRYPTION_KEY": "dev-mcp-encryption-key-change-me-32b",
    "FADEN_PII_ENVIRONMENT": "dev",
    "FADEN_MCP_ENVIRONMENT": "dev",
    "FADEN_SKILLS_ENVIRONMENT": "dev",
}

#: Every key default_env_vars() wrote before (no profiles), random or not.
OLD_DEFAULT_KEYS = frozenset({
    "CLICKHOUSE_PASSWORD", "CLICKHOUSE_USER", "FADEN_BACKEND_BOOTSTRAP_TOKEN",
    "FADEN_BACKEND_CREDENTIAL_PEPPER", "FADEN_BACKEND_ENVIRONMENT", "FADEN_BACKEND_GATEWAY_URL",
    "FADEN_BACKEND_INTERNAL_TOKEN", "FADEN_BACKEND_RAG_SERVICE_TOKEN", "FADEN_BACKEND_SETTINGS_MASTER_KEY",
    "FADEN_DATA_DIR", "FADEN_GATEWAY_ENCRYPTION_KEY", "FADEN_GATEWAY_ENVIRONMENT",
    "FADEN_GATEWAY_LANGFUSE_ENABLED", "FADEN_LOG_MAX_FILE", "FADEN_LOG_MAX_SIZE", "FADEN_MCP_ENVIRONMENT",
    "FADEN_PII_BACKEND_TOKEN", "FADEN_PII_ENVIRONMENT", "FADEN_RAG_SERVICE_TOKEN", "FADEN_REDIS_MAXMEMORY",
    "FADEN_SKILLS_ENVIRONMENT", "GRAFANA_ADMIN_PASSWORD", "GRAFANA_ADMIN_USER", "LANGFUSE_DATABASE_URL",
    "LANGFUSE_ENCRYPTION_KEY", "LANGFUSE_INIT_ORG_ID", "LANGFUSE_INIT_ORG_NAME", "LANGFUSE_INIT_PROJECT_ID",
    "LANGFUSE_INIT_PROJECT_NAME", "LANGFUSE_INIT_PROJECT_PUBLIC_KEY", "LANGFUSE_INIT_PROJECT_SECRET_KEY",
    "LANGFUSE_INIT_USER_EMAIL",
    "LANGFUSE_INIT_USER_NAME", "LANGFUSE_INIT_USER_PASSWORD", "LANGFUSE_NEXTAUTH_SECRET", "LANGFUSE_SALT",
    "MINIO_ROOT_PASSWORD", "MINIO_ROOT_USER", "POSTGRES_DB", "POSTGRES_PASSWORD", "POSTGRES_USER",
    "RABBITMQ_ADMIN_PASS", "RABBITMQ_ADMIN_USER", "RABBITMQ_API_PASS", "RABBITMQ_BACKEND_PASS",
    "RABBITMQ_PII_PASS", "REDIS_AUTH", "USERS_SECRET", *OLD_MODULE_SECRETS,
})


def test_the_secret_keys_are_the_old_ones_and_provenance_s() -> None:
    secret_keys = env_gen.SECRET_KEYS
    assert secret_keys == OLD_SECRET_KEYS | set(PROVENANCE_TOKENS)


def test_the_required_keys_are_the_old_ones_and_provenance_s() -> None:
    assert len(env_gen.REQUIRED_KEYS) == len(set(env_gen.REQUIRED_KEYS)), "no key twice"
    assert set(env_gen.REQUIRED_KEYS) == set(OLD_REQUIRED_KEYS) | set(PROVENANCE_TOKENS)


def test_the_token_groups_are_the_old_ones_and_provenance_s() -> None:
    assert set(env_gen._TOKEN_GROUPS) == {*OLD_TOKEN_GROUPS, PROVENANCE_TOKENS}


def test_a_new_install_gets_the_keys_it_got_and_provenance_s() -> None:
    values = env_gen.default_env_vars()
    assert set(values) == OLD_DEFAULT_KEYS | {*PROVENANCE_TOKENS, "FADEN_PROVENANCE_ENVIRONMENT"}
    assert values["FADEN_PROVENANCE_ENVIRONMENT"] == "production"


def test_each_token_group_holds_one_random_value_of_its_own() -> None:
    values, again = env_gen.default_env_vars(), env_gen.default_env_vars()
    shared = [{values[key] for key in group} for group in (*OLD_TOKEN_GROUPS, PROVENANCE_TOKENS)]
    assert all(len(one) == 1 for one in shared), "every key of a group holds the same value"
    assert len({next(iter(one)) for one in shared}) == len(shared), "and no two groups share one"
    assert all(values[key] != again[key] for key in (*OLD_MODULE_SECRETS, *PROVENANCE_TOKENS)), "random"
    assert values["FADEN_MCP_ENCRYPTION_KEY"] not in {next(iter(one)) for one in shared}


def test_a_development_stack_gets_the_development_values_it_got() -> None:
    values = env_gen.dev_env_vars()
    assert {key: values[key] for key in OLD_DEV_VALUES} == OLD_DEV_VALUES
    assert {key: values[key] for key in PROVENANCE_TOKENS} == dict.fromkeys(
        PROVENANCE_TOKENS, "dev-provenance-service-token"
    )
    assert values["FADEN_PROVENANCE_ENVIRONMENT"] == "dev"


def test_a_module_s_tokens_are_named_in_its_registry_entry_only() -> None:
    """The next module adds its tokens to MODULES; env_gen names none of them."""
    text = Path(env_gen.__file__).read_text(encoding="utf-8")
    assert [key for module in MODULES.values() for key in module.service_token_keys if key in text] == []
