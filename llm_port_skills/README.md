# llm_port_skills — Skills Registry

Skills Registry microservice for **llm.port**. Stores skills — reusable
instruction packs in the [Agent Skills](https://agentskills.io/specification)
format — with owners, sharing and immutable versions, and resolves which ones
a chat request gets.

- **MCP** = what the system can do (external tool calls)
- **RAG** = what the system knows (document retrieval)
- **Skills** = how the system should solve a task (reasoning playbooks)

What users see: [docs/skills.md](../docs/skills.md).

## Model

| Table | Holds |
|---|---|
| `skills` | Owner (`owner_id`, none = the organization), `visibility` (private/tenant/global), `activation` (always/auto/manual), `latest_version`, `published_version` |
| `skill_versions` | Immutable: description, body, SKILL.md's other frontmatter, the `llmport.yaml` manifest (tools, knowledge, triggers), files, `content_sha256` |
| `skill_grants` | Sharing with a user or group, `read` or `write` |
| `skill_assignments` | Admin bindings to a tenant, workspace, project or assistant |
| `skill_usage_daily` | Uses per version per day |

## Who is asking

The backend is the only caller of the skills API. It sends the shared
service token plus the user in headers — believed only next to the token:

| Header | Value |
|---|---|
| `X-LLM-Port-User` | The user id |
| `X-LLM-Port-Groups` | Their group ids, comma-separated |
| `X-LLM-Port-Caps` | What RBAC allows: `admin`, `create`, `share`, `publish` |
| `X-LLM-Port-Tenant` | The tenant (default `default`) |

What a user may do with one skill follows from its owner, grants and
visibility (`services/access.py`).

## Quick Start

```bash
# Development
docker compose up -d

# Run migrations
alembic upgrade head

# Start service
python -m llm_port_skills

# Tests (a real Postgres; the database llm_skills_test is made and dropped)
pytest
```

## Environment Variables

| Variable                        | Default      | Description                   |
| ------------------------------- | ------------ | ----------------------------- |
| `LLM_PORT_SKILLS_PORT`          | `8008`       | Service port                  |
| `LLM_PORT_SKILLS_DB_HOST`       | `127.0.0.1`  | PostgreSQL host               |
| `LLM_PORT_SKILLS_DB_BASE`       | `llm_skills` | Database name                 |
| `LLM_PORT_SKILLS_REDIS_HOST`    | ``           | Redis host (empty = disabled) |
| `LLM_PORT_SKILLS_SERVICE_TOKEN` | ``           | Internal service auth token   |
| `LLM_PORT_SKILLS_JWT_SECRET`    | ``           | Lets a user's own JWT in (read-only, no capabilities) |

Limits: see `settings.py` and [docs/skills.md](../docs/skills.md#limits).

## API

### Skills (`/api/skills`, as the user in the headers)

- `GET /` — skills the caller can see (`view=all|mine|shared|org`, `q`, `usable=true` for the chat's picker)
- `POST /` — create (fields, or a whole SKILL.md in `content`)
- `POST /import` — create from a `.md` or a zipped skill folder
- `GET /completions` — editor intellisense data
- `GET|PATCH|DELETE /{id}` — detail; title, tags, activation, priority, enabled; delete
- `POST /{id}/versions` — save a version (`base_version`, `publish`)
- `GET /{id}/versions`, `GET /{id}/versions/{n}`, `GET /{id}/diff?from=&to=`
- `POST /{id}/publish` — serve a version (roll back with an older one)
- `POST /{id}/archive`, `POST /{id}/restore`
- `PUT /{id}/visibility`, `GET|PUT /{id}/grants`
- `GET /{id}/export?format=zip|md&version=`
- `GET|POST /{id}/assignments`, `DELETE /{id}/assignments/{aid}` — administrators

### Internal (service token)

- `POST /api/internal/skills/resolve` — the skills a chat request gets (`user_id`, `group_ids`, `requested`, `user_query`)
- `POST /api/internal/skills/usage` — count a use
- `DELETE /api/internal/principals/{user|group}/{id}` — drop a deleted principal's grants
