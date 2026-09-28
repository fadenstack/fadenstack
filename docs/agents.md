# Agents

An agent is an assistant that runs inside another application -- a TIA Portal
or EPLAN add-in, an Office add-in, a desktop tool -- built with the Fadenstack
SDKs. Fadenstack keeps what has to be central: the agent's instructions and
model, who may use it, and the usage. The application keeps the conversation:
an agent's sessions stay on the device.

---

## Setting up an agent

Agents are managed under `/api/agents` (admin console pages follow). You need
`agents:create` to create one; you then maintain it.

| Step | Call |
|---|---|
| Create, with its first version | `POST /api/agents` `{slug, name, version: {instructions, model_alias, host_tool_contract, ...}}` |
| Change it | `POST /api/agents/{slug}/versions` -- a new version; versions never change |
| Serve a version (or roll back) | `POST /api/agents/{slug}/versions/{n}/publish` |
| Allow users and teams | `PUT /api/agents/{slug}/grants` `{grants: [{principal_type: "user" \| "group", principal_id}]}` |
| Maintainers | `PUT /api/agents/{slug}/members` |
| Why may someone use it (or not)? | `GET /api/agents/{slug}/access/{user_id}` |
| Usage (no content) | `GET /api/agents/{slug}/usage?days=30` |
| Retire / restore | `DELETE /api/agents/{slug}`, `POST /api/agents/{slug}/restore` |

Nobody can use an agent until it is published and granted: grants go to users
or to groups (teams), and everyone else is refused. Maintainers can use their
agent before it is published.

A version holds:

- `instructions`: put before every conversation; the SDK never sees them.
- `model_alias`: the model every request uses, whatever the client asks for.
- `host_tool_contract`: the tools the host application may offer (`name`,
  optionally `schema_sha256`, `risk`, `approval`). A request declaring any
  other tool is refused. Empty means no restriction.
- `server_tools`: the gateway's own tools (knowledge search, MCP tools, ...)
  the model may be offered, as names or patterns (`knowledge_*`). Leave it out
  for what a chat would get; `[]` for none.
- `local_mcp_mode`: `disable` (default) or `allow` for local MCP servers the
  SDK attaches.

---

## Signing in

The SDK signs the user in to one agent on one device:

```
POST /api/agent-tokens          {agent: "eplan-assistant", device_label: "EPLAN on PC-7"}
  -> {access_token, expires_in, refresh_token, refresh_expires_in, device_id}
POST /api/agent-tokens/refresh  {refresh_token}          (the refresh token is replaced)
GET  /api/agent-tokens/mine                               (your sign-ins)
DELETE /api/agent-tokens/{device_id}                      (sign a device out)
```

- The access token is valid for one agent only, for 10 minutes by default.
  Plain `/v1/...` routes refuse it.
- A refresh checks access again. Take a user out of the agent's teams and
  their next refresh fails.
- A refresh token works once. Using an old one again signs that device out
  everywhere, because someone has copied it.
- Lifetimes are system settings (`agents.access_token_ttl_s`,
  `agents.refresh_token_ttl_s`, `agents.refresh_idle_s`).
- Developer mode (`PUT /api/admin/users/{id}/developer-mode`, admins only)
  lets a user attach any local MCP server; it shows in their agent tokens.

---

## Calling an agent

Every OpenAI client works against the agent's base URL:

```python
from openai import OpenAI

client = OpenAI(base_url="https://faden.plant.local/v1/agents/eplan-assistant", api_key=access_token)
client.chat.completions.create(model="any", messages=[...], stream=True,
                               stream_options={"include_usage": True})
```

| Route | What |
|---|---|
| `POST /v1/agents/{agent}/chat/completions` | Chat, streamed or whole, under the agent's rules |
| `GET /v1/agents/{agent}/profile` | What the SDK caches: version, model, tool contract, local MCP mode, a hash of the instructions |
| `GET /v1/agents/{agent}/models` | The agent's model |
| `POST /v1/agents/{agent}/events` | Content-free events from the SDK: tools run, approvals, local MCP servers, session counts |
| `GET /v1/features` | The edition and its features |

- Send the whole conversation each time: the endpoint keeps no session, and
  refuses `session_id`.
- `X-Faden-Conversation-Id` and `X-Faden-Turn-Id` let usage be grouped per
  conversation without keeping it.
- Answers are the OpenAI shapes, with `usage`. A stream that breaks after it
  started ends with an error event before `[DONE]`, never silently.
- A maintainer can call the routes with their normal login token, to try a
  draft.

What the server keeps: one request-log row per request (agent, user, device,
conversation id, model, tokens, latency, status), the events, and nothing of
the conversation. Traces of agent requests hold metadata only.
