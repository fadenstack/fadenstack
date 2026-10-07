# faden_gateway

OpenAI-compatible LLM gateway service.

## MVP Endpoints
- `GET /health`
- `GET /v1/models`
- `POST /v1/chat/completions`
- `POST /v1/embeddings`

Internal template/debug endpoints remain under `/api/*`.

## Quick Start
```bash
uv sync --locked
uv run -m faden_gateway
```

Swagger UI:
- `/api/docs`

## Docker Compose (Shared Stack)
From `shared/`:
```bash
# Normal containerized run
docker compose up -d faden-gateway

# Dev mode with bind mount + reload (no image rebuild per code change)
docker compose -f docker-compose.yaml -f docker-compose.dev.yaml up -d faden-gateway
```

## Environment Variables
Core variables:
- `FADEN_GATEWAY_HOST`
- `FADEN_GATEWAY_PORT`
- `FADEN_GATEWAY_RELOAD`
- `FADEN_GATEWAY_ENVIRONMENT`

Database:
- `FADEN_GATEWAY_DB_HOST`
- `FADEN_GATEWAY_DB_PORT`
- `FADEN_GATEWAY_DB_USER`
- `FADEN_GATEWAY_DB_PASS`
- `FADEN_GATEWAY_DB_BASE` (default `faden_gateway`)
- `FADEN_GATEWAY_DB_ECHO`

JWT:
- `FADEN_GATEWAY_JWT_SECRET`
- `FADEN_GATEWAY_JWT_ALGORITHM` (default `HS256`)

Gateway behavior:
- `FADEN_GATEWAY_HTTP_TIMEOUT_SEC`
- `FADEN_GATEWAY_LEASE_TTL_SEC`
- `FADEN_GATEWAY_RETRY_PRE_FIRST_TOKEN`
- `FADEN_GATEWAY_REQUEST_MAX_BODY_BYTES`
- `FADEN_GATEWAY_STREAM_IDLE_TIMEOUT_SEC`

Langfuse:
- `FADEN_GATEWAY_LANGFUSE_ENABLED`
- `FADEN_GATEWAY_LANGFUSE_HOST`
- `FADEN_GATEWAY_LANGFUSE_PUBLIC_KEY`
- `FADEN_GATEWAY_LANGFUSE_SECRET_KEY`
- `FADEN_GATEWAY_LANGFUSE_TRACING_ENABLED`
- `FADEN_GATEWAY_LANGFUSE_RELEASE`
- `FADEN_GATEWAY_LANGFUSE_DEBUG`

Redis/Rabbit:
- `FADEN_GATEWAY_REDIS_HOST`
- `FADEN_GATEWAY_REDIS_PORT`
- `FADEN_GATEWAY_REDIS_USER`
- `FADEN_GATEWAY_REDIS_PASS`
- `FADEN_GATEWAY_REDIS_BASE`
- `FADEN_GATEWAY_RABBIT_HOST`
- `FADEN_GATEWAY_RABBIT_PORT`
- `FADEN_GATEWAY_RABBIT_USER`
- `FADEN_GATEWAY_RABBIT_PASS`
- `FADEN_GATEWAY_RABBIT_VHOST`

## Migrations
```bash
alembic upgrade head
```

Rollback:
```bash
alembic downgrade base
```

## Curl Smoke Checks
```bash
# models
curl http://localhost:8000/v1/models \
  -H "Authorization: Bearer $TOKEN"

# chat (non-stream)
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3-32b","messages":[{"role":"user","content":"Hello"}]}'

# chat (stream)
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -N \
  -d '{"model":"qwen3-32b","stream":true,"messages":[{"role":"user","content":"Hello"}]}'

# embeddings
curl http://localhost:8000/v1/embeddings \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3-32b","input":"The quick brown fox"}'
```

## Quality Gates
```bash
uv run ruff check .
uv run mypy faden_gateway
uv run pytest -vv
```

## Additional Docs
- `docs/api-sequence.md`
- `docs/gateway-architecture.md`
- `docs/database.md`
