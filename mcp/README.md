# faden_mcp

MCP Tool Registry — governed MCP broker for Fadenstack.

Registers MCP-compliant servers (stdio + SSE), discovers tools automatically,
converts them to OpenAI-compatible tool definitions, and routes all tool
invocations through a Privacy Proxy (Presidio-based PII detection/redaction).

## Quick Start

```bash
uv sync
python -m faden_mcp
```

## Environment Variables

All settings use the `FADEN_MCP_` prefix:

| Variable                       | Default     | Description                             |
| ------------------------------ | ----------- | --------------------------------------- |
| `FADEN_MCP_HOST`            | `127.0.0.1` | Bind address                            |
| `FADEN_MCP_PORT`            | `8000`      | Bind port                               |
| `FADEN_MCP_DB_HOST`         | `127.0.0.1` | PostgreSQL host                         |
| `FADEN_MCP_DB_PORT`         | `5432`      | PostgreSQL port                         |
| `FADEN_MCP_DB_USER`         | `llm_user`  | PostgreSQL user                         |
| `FADEN_MCP_DB_PASS`         | `llm_user`  | PostgreSQL password                     |
| `FADEN_MCP_DB_BASE`         | `faden_mcp`   | PostgreSQL database name                |
| `FADEN_MCP_REDIS_HOST`      | ``          | Redis host (empty = disabled)           |
| `FADEN_MCP_REDIS_PORT`      | `6379`      | Redis port                              |
| `FADEN_MCP_ENCRYPTION_KEY`  | ``          | Fernet master key for secret encryption |
| `FADEN_MCP_PII_SERVICE_URL` | ``          | PII service base URL                    |
| `FADEN_MCP_SERVICE_TOKEN`   | ``          | Shared token for internal API auth      |
