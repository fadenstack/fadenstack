# MCP servers

An MCP server gives chats tools: search a repository, read a file, add two
numbers. LLM.Port's MCP module connects to the servers you register, lists
their tools, and runs a tool when a model asks for it; the gateway offers each
chat the tools switched on for it. The module needs `llmport module enable mcp`.

Everything below was taken on the test install (Ubuntu VM, 2026-09-25/26) with
two servers: GitHub's hosted one, and `server-everything`, a test server run as
a container next to LLM.Port.

---

## Running a server as a container

A server that speaks MCP over HTTP can run on the LLM.Port server itself, from
the Containers pages ([Containers](containers.md) has the whole flow). For
`server-everything`:

- image `node:22-alpine` (pull it under Images first);
- command `npx -y @modelcontextprotocol/server-everything streamableHttp`
  (it listens on port 3001);
- network **`llm_port_shared_default`**, and no port binding: the MCP service
  reaches it by name, as `http://mcp-everything:3001/mcp`. Publishing 3001 on
  the host would clash with Grafana on the shared install.

![The MCP server's container](images/mcp/01-server-container.png)

A container named `mcp-...` is classified `MCP`; see
[Classes](containers.md#classes) for what that allows.

---

## Registering a server

**Extensions → MCP Hub → Register Server**:

![Register Server](images/mcp/02-register-server.png)

| Field | What it is |
|---|---|
| Server Name | Shown in the console |
| Transport | **Streamable HTTP** (current), **SSE** (older HTTP servers) or **Stdio** (a command the MCP service runs itself) |
| Server URL | For HTTP: the server's MCP endpoint, usually ending `/mcp` (or `/sse`) |
| Command | For stdio: the command line, e.g. `uvx mcp-server-time` |
| Tool Prefix | Tools are named `mcp.<prefix>.<tool>`; unique per server |
| PII Mode | What the server may see of personal data (below) |
| Timeout | How long connecting and each tool call may take, 5 to 600 s |

**Headers** (HTTP) and **environment variables** (stdio) carry what a server
needs to let you in, such as a token:

![Headers: Authorization for GitHub](images/mcp/03-register-headers.png)

They are stored encrypted and never shown again; the console lists their
names only. Everyone who uses the server's tools uses these credentials.

On **Register** the MCP service connects and lists the server's tools. If it
cannot, the server is saved all the same, in "error", with the reason, for
example:

- `... answered 401 Unauthorized: it needs credentials. Add them as a header,
  for example Authorization: Bearer <token>.`
- `... has no MCP endpoint (it answered 404). Streamable HTTP endpoints usually
  end in /mcp, SSE endpoints in /sse.`
- `Cannot reach ...: Connection refused`

Fix it with **Edit** and it connects again.

![Registered: active, 13 tools](images/mcp/04-servers-list.png)

---

## A server's page

![everything: status, settings, discovered tools](images/mcp/05-server-detail.png)

The page shows the server's status, transport, prefix, PII mode, timeout and
which headers are set, the reason its last connection failed if it did, and
its **Discovered Tools**: each can be switched off for everyone. **Refresh**
lists the tools again; **Test** checks the connection.

**Edit** changes the URL or command, timeout, PII mode, and headers or
variables:

![Edit: a stored header shown by name, with Replace and Remove](images/mcp/06-edit-headers.png)

A stored header is **replaced** or **removed** without the others being
entered again. Saving reconnects the server and lists its tools again.

**GitHub's hosted server** (`https://api.githubcopilot.com/mcp/`) has 48 tools;
every tool offered costs prompt tokens on every turn (all 48: about 14.7k).
Narrow it with headers: `X-MCP-Toolsets: repos` (or `issues`,
`pull_requests`, ...), `X-MCP-Tools: get_file_contents,search_repositories`,
`X-MCP-Readonly: true`.

---

## In a chat

The chat's **Tools** panel (the wrench) lists the tools it may use, server by
server, each with a switch. Only switched-on tools are offered to the model,
and a tool switched off is refused even if the model names it:

![A chat with one tool switched on](images/mcp/07-chat-tools.png)

The gateway runs the calls between rounds of the answer; the reply streams
once the tools are done:

![The model called echo](images/mcp/08-chat-echo.png)

What the chat keeps: each reply stores the tool calls and results that led to
it, and they go back to the model with the history, so later turns still call
tools (each result is kept up to 4,000 characters). The page shows the reply
only.

A small model may name a tool wrong or quote a number (`"25"`); the gateway
tells it which tools exist, and the MCP service reads a quoted number or
boolean in the type the tool declares.

---

## Personal data (PII mode)

With the PII module on, the chat's policy may replace personal data with
placeholders (`[PERSON_1]`) before a model sees it. What a tool gets depends
on its server's mode:

| Mode | The tool gets |
|---|---|
| Allow | The real values: the gateway puts them back before the call |
| Redact | The placeholders; the MCP service also redacts what it finds in the arguments |
| Block | The call is refused if the arguments contain personal data |

The modules work independently: without the PII module, or when it cannot be
reached, the MCP service lets the call through and logs a warning.
