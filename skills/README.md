# faden_skills — Skills Registry

Skills Registry microservice for **Fadenstack**. Stores skills — reusable
instruction packs in the [Agent Skills](https://agentskills.io/specification)
format — with owners, sharing and immutable versions, and resolves which ones
a chat request gets.

- **MCP** = what the system can do (external tool calls)
- **RAG** = what the system knows (document retrieval)
- **Skills** = how the system should solve a task (reasoning playbooks)

What users see: [Skills](../docs/help/pages/use/skills.md); what administrators manage: [Skills for the organisation](../docs/help/pages/administer/skills.md).

## Model

| Table | Holds |
|---|---|
| `skills` | Owner (`owner_id`, none = the organization), `visibility` (private/tenant/global), `activation` (always/auto/manual), `latest_version`, `published_version` |
| `skill_versions` | Immutable: description, body, SKILL.md's other frontmatter, the `faden.yaml` manifest (tools, knowledge, triggers), files, `content_sha256` |
| `skill_grants` | Sharing with a user or group, `read` or `write` |
| `skill_assignments` | Admin bindings to a tenant, workspace, project or assistant |
| `skill_usage_daily` | Uses per version per day |

## Who is asking

The backend calls the skills API for users, and the gateway calls the internal
API; both send the shared service token. The backend adds the user in headers —
believed only next to the token, or when no service token is set (development):

| Header | Value |
|---|---|
| `X-Faden-User` | The user id |
| `X-Faden-Groups` | Their group ids, comma-separated |
| `X-Faden-Caps` | What RBAC allows: `admin`, `create`, `share`, `publish` |
| `X-Faden-Tenant` | The tenant (default `default`) |

What a user may do with one skill follows from its owner, grants and
visibility (`services/access.py`).

## Quick Start

```bash
# Development
docker compose up -d

# Run migrations
alembic upgrade head

# Start service
python -m faden_skills

# Tests (a real Postgres; the database llm_skills_test is made and dropped)
pytest
```

## Environment Variables

| Variable                        | Default      | Description                   |
| ------------------------------- | ------------ | ----------------------------- |
| `FADEN_SKILLS_PORT`          | `8008`       | Service port                  |
| `FADEN_SKILLS_DB_HOST`       | `127.0.0.1`  | PostgreSQL host               |
| `FADEN_SKILLS_DB_BASE`       | `faden_skills` | Database name                 |
| `FADEN_SKILLS_REDIS_HOST`    | ``           | Redis host (empty = disabled) |
| `FADEN_SKILLS_SERVICE_TOKEN` | ``           | Internal service auth token   |
| `FADEN_SKILLS_JWT_SECRET`    | ``           | Lets a user's own JWT in (read-only, no capabilities) |

Limits: see `settings.py` and [Skills for the organisation](../docs/help/pages/administer/skills.md#limits).

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
