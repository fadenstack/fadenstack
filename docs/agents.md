# Agents

An agent is an assistant that runs inside another application -- a TIA Portal
or EPLAN add-in, an Office add-in, a desktop tool -- built with the Fadenstack
SDKs. Fadenstack keeps what has to be central: the agent's instructions and
model, who may use it, and the usage. The application keeps the conversation:
an agent's sessions stay on the device.

---

## Setting up an agent

In the console: **Agents → Agents** (`/admin/agents`). Each agent has five tabs:

| Tab | What it does |
|---|---|
| Definition | Instructions, model, knowledge, server tools, local MCP, host tools, greeting and suggestions. Saving makes a new version; *Save and publish* serves it at once. |
| Versions | Every version, which one is live, what changed between two, publish or roll back. |
| Apps | The apps that run it and what they bring -- their tools with the classes their developers gave them, their greeting, which versions are in use -- against the latest version. *Accept and publish* takes it all in. |
| Access | The users and teams who may use it, its maintainers, and "may this person use it, and why?" |
| Activity | Requests and tokens per day, what the SDKs reported (tools run, approvals, local MCP servers), and the signed-in devices, which can be signed out. |

*New agent* sets an agent up in one step: pick one of Fadenstack's own apps
(Chrome, Word, Excel, PowerPoint, Outlook) and its definition -- instructions,
host tools, greeting -- is filled in, published and granted to the users and
teams you add; or start blank, or paste a JSON definition. Someone with
`agents:create` can also create the agent from inside an app that finds the
server without it (the Chrome extension offers it at sign-in). Tool rules, tool
classes and the decision log are under **Agents → Governance** (superusers;
see [governance.md](governance.md)), and developer mode is switched per user
under **Access Control → Users**.

### What the apps bring

Nobody types host tools in. Each app built with an SDK reports, when it starts,
what it brings (`PUT /v1/agents/{agent}/manifest`): its tools with their
description, parameters and the class its developer gave them, the context it
offers (`page:document`, ...), its default greeting and suggestions, and the
app's and SDK's versions. The gateway keeps one copy per distinct manifest.

When an app is updated with a tool the agent's contract does not list, the
gateway **holds it back**: the model does not see it, the app keeps working
with the tools it may use, `X-Faden-Held-Tools` names what was held, and the
tool waits on the agent's **Apps** tab (requests from clients without an SDK
show there too, as "Requests"). The tab marks each tool:

| | |
|---|---|
| New | not in the contract: held back |
| Changed | its parameters differ from the ones accepted (`schema_sha256`) |
| Riskier | the app now calls it riskier than the contract does |
| Unused | in the contract, but no app in use brings it |

and flags descriptions that try to instruct the model, hide characters, link
out or run long -- a model reads tool descriptions like instructions. The
app's classes are suggestions: change any, or leave a tool out, then *Accept
and publish* (or *Accept as draft*); `POST /api/agents/{slug}/apps/accept`
does the same. Nothing is taken without that click. Until a version with the
change is published the dashboard keeps asking ("tool changes from its apps
are not live").

Who sees what:

- `agents:create` creates agents; the creator maintains them.
- Maintainers and `agents:manage` change an agent, its versions and its access,
  and retire it.
- `agents:read` sees every agent and its details (versions, access,
  activity), but changes nothing.
- `agents:delete` deletes a retired agent for good. Maintaining an agent does
  not give it: it is a role's right. The built-in admin role has it; give it
  to another role under **Access Control → Roles**.

Retiring closes an agent to everyone at once and can be undone. Deleting
takes a retired agent: the console offers *Delete* on it, after you type its
address. Its versions, grants, maintainers and device sign-ins go with it.
What it did stays -- the request log, the governance decisions and the SDKs'
events keep its id, and the audit log records who deleted it, by name -- and
its address is free for a new agent.

The admin console is for superusers. Everyone else who looks after agents --
maintainers, and users with an `agents` permission -- opens the same pages at
`/agents`, from **Agents** in the chat's user menu. Without `agents:read` or
`agents:manage` the list holds only the agents they manage; the agents they
merely use are their SDK's business. (`GET /api/admin/users/me/access` says
`maintains_agents`, since maintaining is a right on an agent, not an RBAC
permission.)

The API is under `/api/agents`:

| Step | Call |
|---|---|
| Create, with its first version | `POST /api/agents` `{slug, name, version: {instructions, model_alias, host_tool_contract, ...}}` |
| Change it | `POST /api/agents/{slug}/versions` -- a new version; versions never change |
| Serve a version (or roll back) | `POST /api/agents/{slug}/versions/{n}/publish` |
| Allow users and teams | `PUT /api/agents/{slug}/grants` `{grants: [{principal_type: "user" \| "group", principal_id}]}` |
| Maintainers | `PUT /api/agents/{slug}/members` |
| Why may someone use it (or not)? | `GET /api/agents/{slug}/access/{user_id}` |
| Usage (no content) | `GET /api/agents/{slug}/usage?days=30` |
| Templates for Fadenstack's own apps | `GET /api/agents/templates` -- each one's `definition` is a body for `POST /api/agents` |
| What the apps bring, against the latest version | `GET /api/agents/{slug}/apps` |
| Take it in (a new version) | `POST /api/agents/{slug}/apps/accept` `{tools: [{name, risk}], remove: [...], ui, publish}` |
| Retire / restore | `POST /api/agents/{slug}/retire`, `POST /api/agents/{slug}/restore` |
| Delete a retired agent (`agents:delete`) | `DELETE /api/agents/{slug}` -- 409 while it is not retired |

Nobody can use an agent until it is published and granted: grants go to users
or to groups (teams), and everyone else is refused. Maintainers can use their
agent before it is published.

A version holds:

- `instructions`: put before every conversation; the SDK never sees them.
- `model_alias`: the model every request uses, whatever the client asks for.
- `host_tool_contract`: the tools the host application may offer (`name`,
  optionally `schema_sha256` -- the SHA-256 of the parameter schema as
  canonical JSON, set when a tool is accepted from an app -- `risk`,
  `approval`). Any other tool a request declares is held back and kept for
  review (see *What the apps bring*) -- unless the device may attach local MCP
  servers (`local_mcp_mode: allow`, or the user's developer mode): their tools
  are in no contract, and tool modes treat them as unclassified. Empty means
  no restriction.
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

### What the gateway did on its own

Send `X-Faden-Events: ag-ui` (any chat route) to hear about the gateway's own
work during the answer, in AG-UI's vocabulary. In a stream they arrive as
named events (`event: faden`) between the OpenAI chunks; a whole answer
carries them in a `faden` object beside the OpenAI fields. Without the header
nothing changes, so plain OpenAI clients never see them.

| Event | Says |
|---|---|
| `TOOL_CALL_START`, `TOOL_CALL_END` | A gateway tool ran: name, kind (`knowledge`, `mcp`, `skill_file`, ...), duration, whether it failed |
| `CUSTOM` `faden.citations` | The sources a knowledge search returned (document, chunk, score), not their text |
| `CUSTOM` `faden.pii` | What the PII scan found: types and counts only |
| `CUSTOM` `faden.gateway_round` | The round as the model saw it -- its tool-calling turn and the tools' answers, with PII values put back |

An agent's SDK keeps `faden.gateway_round` messages in its local session and
sends them back as history with the next request, so the model still knows
what it searched after the client answered one of its own tools.

### Tools on the device, called by the gateway

Most of an agent's tools run in the SDK's own loop. When the gateway itself
has to call a tool on the user's machine -- a server session using the
desktop's tools -- the device keeps a WebSocket open to `/v1/client/ws`:

1. **Authenticate first.** An agent token (the agent must still let the user
   in; its `did` is the device) or the user's login token, sent as
   `Authorization: Bearer ...`, as the subprotocol `faden.bearer.<token>`
   (offer `faden.v1` beside it -- browsers cannot set headers), or as `token`
   in `session_init`. Without one the socket is closed (4401) before anything
   is registered.
2. `session_init` `{client_id, tools, session_id?}`. The device registers once
   and serves all of the user's sessions; a named session must be the user's
   own, and gets this device first. A device signed in for an agent serves
   only that agent's calls.
3. The gateway sends `tool_call_request` `{call_id, tool_id, arguments}`; the
   device answers `tool_call_result` or `tool_call_error` with the same
   `call_id`. Only the device that was sent a call can answer it.
4. Calls wait 30 s. `tool_call_progress` buys another 30 s;
   `tool_call_approval_pending` `{timeout_s}` waits for a person (up to
   10 minutes). A call that runs out is failed and the device gets
   `tool_call_cancel`.
5. A device that drops and reconnects as the same device within 15 s gets
   its pending calls again; after that they fail with a clear error.

With Redis configured, a call reaches the device wherever its socket is:
each worker announces its devices and the call travels to the worker that
holds the socket, and the answer back.

### Tool modes

The conversation is on the device, so the SDK sends its session's tool mode
with each request: `X-Faden-Tool-Mode: off | read_only | ask | auto` (Ask
when missing). The gateway hides what the mode hides -- the agent's host
tools by their `risk` in the contract, a tool without one as unclassified --
and the SDK asks its user before running a host tool the mode asks about. The
profile tells the SDK the default and any ceiling:
`tool_modes: {default: "ask", ceiling: null, open_world_enforced: false}`.
See [governance.md](governance.md).

### Refusals

A prompt, an answer or a tool call can be refused (tool rules in core,
content policies with Enterprise); the outcomes -- 422 `policy_blocked`,
`finish_reason: content_filter`, `faden.tool_denied`, approvals -- are in
[governance.md](governance.md). Agent routes answer approvals at
`POST /v1/agents/{agent}/approvals/{approvalId}`.

What the server keeps: one request-log row per request (agent, user, device,
conversation id, model, tokens, latency, status), the events, and nothing of
the conversation. Traces of agent requests hold metadata only.
