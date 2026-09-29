# Governance

The gateway can refuse a prompt, an answer or a tool call. Whatever refuses
it, the client gets the same outcome, so a chat page, an SDK or a plain
OpenAI client handles one contract.

| Check point | When | Core | Enterprise |
|---|---|---|---|
| Input | Before anything is stored or sent to a model | -- (agent access and rate limits run before it) | Content policies on the prompt |
| Output | On the answer, before it is forwarded | -- | Content policies on the answer |
| Tool | Before a tool runs (gateway tools), or before it is offered (the client's tools) | **Tool rules** | Content policies on tool arguments |

Core ships the pipeline, the outcomes, the decision log and tool rules.
Content policies are an Enterprise extension: with none installed there is
no input or output check, and a streamed answer is forwarded exactly as
before -- no buffering.

---

## What the client gets

**A refused prompt** -- HTTP 422, the OpenAI error envelope, and a `faden`
object beside it. OpenAI clients raise their usual error
(`UnprocessableEntityError` in Python).

```json
{
  "error": {"type": "governance_error", "code": "policy_blocked",
            "message": "Project names stay out of the assistant.", "param": null},
  "faden": {"governance": {
    "outcome": "prompt_blocked", "stage": "input", "category": "confidential",
    "message": "Project names stay out of the assistant.",
    "decision_id": "…", "request_id": "…", "session_id": "…", "message_id": "…"}}
}
```

The web chat receives it as its stream's error event, with the same `faden`
object.

**A refused answer** -- the answer stops, and ends with
`finish_reason: "content_filter"`. Clients that sent `X-Faden-Events` get a
`faden.governance` event first. A whole answer is a 200 with empty content,
`finish_reason: "content_filter"` and the `faden.governance` object.

The answer is checked in windows before it is forwarded. The last window is
always held back until the next check, so a phrase up to a window long is
checked whole before any of it leaves.

**A refused tool call** -- the tool does not run. The model is told
("Tool 'x' is not allowed here: <message>") and carries on. Clients that
asked for events get `faden.tool_denied`. A tool the *client* offered and a
rule refuses is not offered to the model at all, and the client hears so
before the answer.

**A tool call that needs approval** -- in a stream to a client that sent
`X-Faden-Events`, the gateway sends `faden.approval_required` and waits for
the user (5 minutes, `governance_approval_timeout_s`):

```
POST /v1/approvals/{approvalId}                 {"approved": true}
POST /v1/agents/{agent}/approvals/{approvalId}  (with an agent token)
```

Only the user whose request waits can answer, on any gateway worker. A client
that cannot ask its user (no events, or a whole answer) gets the call refused,
saying it needs approval. A tool on the user's device is sent with
`approval_required`: the device asks the user itself (see the device channel
in [agents.md](agents.md)).

**A rate limit** -- 429 with `type: rate_limit_error`, `code:
rate_limit_rpm` / `rate_limit_tpm`, and `Retry-After`.

---

## What is stored

Never the refused text.

- **Decision log** (`llm_gateway_policy_decision`): stage, outcome
  (`block`, `approval_required`, `approved`, `rejected`, `expired`), policy,
  category, what the user was told, the tool, and every check's objection.
  A refused prompt or answer is recorded as an HMAC-SHA256 under the tenant's
  own key: the same text refused twice shows the same value; nobody can read
  it or test guesses against it without the key.
- **The chat's turns** (`chat_message.status`):

  | Status | The user sees | The model is sent next time |
  |---|---|---|
  | `ok` | the message | yes |
  | `blocked_input` | the notice (the message has no text) | no |
  | `blocked_output` | the question, and the notice for the answer | the question only |
  | `error` | the question, with an error notice | no |

  Consecutive user messages reach the model as one: strict chat templates
  refuse roles that do not alternate. The rolling summary skips the same
  turns. `GET /v1/sessions/{id}/messages` returns `status` and, for a blocked
  message, the `notice`.

An agent's conversation stays on the device: the SDK applies the same rule.

---

## Tool rules (core)

Admins manage them in the backend (they live in the gateway's database):

```
GET    /api/admin/governance/tool-rules?tenant_id=default
POST   /api/admin/governance/tool-rules   {name, tool_pattern, action, message, priority, enabled}
PUT    /api/admin/governance/tool-rules/{id}
DELETE /api/admin/governance/tool-rules/{id}
GET    /api/admin/governance/decisions?stage=tool&outcome=block&since=…
```

- `tool_pattern` is a glob over tool names: `knowledge_*`, `mcp_github__*`,
  `read_page`, `*`.
- `action`: `deny`, `approval_required`, or `allow`. The lowest `priority`
  that matches decides, so an `allow` at a lower number is an exception to a
  broader rule.
- `message` is what the user is told; `name` is for admins.
- The gateway reads a tenant's rules at most 30 s old.

The decision log shows no policy names; the auditor's view with them, and
exports, are Enterprise.

---

## For Enterprise: adding a check

A check is an object with `name`, `stages`, `output_window_chars` and
`async check(ctx) -> GovernanceDecision | None`
(`gateway/faden_gateway/services/gateway/governance.py`). The Enterprise
gateway plugin hands them over in `register(registry)`
(`registry.governance_checks`) or from a `governance_checks()` method. The
most restrictive objection wins (block over approval); a check that fails is
skipped unless it sets `fail_closed`.
