# System requirements

What the LLM.Port **server** needs: the machine that runs the console, the API
gateway and their databases. The machines that run models (GPU nodes) are
sized by the models they run and are not covered here.

Every number below was measured, not estimated, unless it says otherwise:
on 2026-09-25, on `ray` at `cb3d2d4`. "VM" is the Linux test install
(Ubuntu, 8 cores, production containers, core services only). "Workstation"
is a development install on Windows with the optional modules; its CPU
figures come from a single gateway process and are the upper end (the Windows
event loop costs more per request than Linux's). Chat load was the model
answering three-sentence replies to 16 users sending one turn after another
without pause, about 8 chat turns per second. On 2026-09-26 the VM was
measured again with every module as a container (`ray` at `f1d98a3`, two
workers per service as `llmport deploy` sizes them): the same 16 users made
18 to 22 chat turns per second.

## Summary

| Configuration | Memory, minimum | Memory, recommended | CPU cores, minimum | CPU cores, recommended | Disk |
|---|---|---|---|---|---|
| Core | 6 GB | 8 GB | 4 | 8 | see [Disk](#disk) |
| Core + PII + MCP + skills | 8 GB | 12 GB | 4 | 8 | see [Disk](#disk) |

Add memory for anything else you run on the server, such as embedding or
reranking models served on its CPU (about 2 GB each for the small Qwen3 models
measured below).

## Memory

### At rest, core services (VM)

The 16 containers of a core install use **3.0 GB** together; the operating
system and Docker bring the machine to 4.1 GB in use.

| Container | Memory | What it is |
|---|---|---|
| api | 542 MB | The gateway (2 workers) |
| worker | 431 MB | Langfuse worker (traces) |
| backend | 359 MB | Console API (2 workers) |
| backend-worker | 292 MB | Background tasks |
| postgres | 267 MB | All relational data |
| clickhouse | 257 MB | Langfuse trace store |
| web | 210 MB | Langfuse web |
| grafana | 142 MB | Dashboards |
| rmq | 106 MB | RabbitMQ |
| minio | 105 MB | Langfuse blob store |
| frontend | 92 MB | Console |
| alloy | 92 MB | Log collector |
| loki | 71 MB | Log store |
| prometheus | 21 MB | Metrics |
| redis | 13 MB | Leases, rate limits, cache |
| nginx | 8 MB | Entry point on port 80 |

### Optional modules (workstation)

| Module | Memory | Note |
|---|---|---|
| PII | 0.8 GB | Loads its language model at start |
| PII worker | 0.8 to 1.2 GB | Its two processes load the model too (VM) |
| MCP | 170 MB | 65 to 110 MB on the VM |
| Skills | 160 MB | 95 to 110 MB on the VM |

On the VM, every container of an install with all modules took 4.1 to 4.5 GB
at rest and 4.4 to 5.2 GB at its peaks under chat load.

### Under load

The services themselves grow little under chat load (gateway about +30 MB,
backend +50 MB). What grows is Postgres: every open database connection is a
process of its own, **6 MB at the median (3 to 12.5 MB)**, so 250 connections
at once take about 1.5 GB. How many an install can open is in
[Database connections](#database-connections).

## CPU

CPU time per chat turn, under load (workstation):

| Component | CPU per chat turn |
|---|---|
| Gateway | 106 ms |
| PII | 42 ms |
| Backend (console API, relays the chat) | 35 ms |
| Postgres | 33 ms |
| Skills | 17 ms |
| MCP | 11 ms |
| Redis | 4 ms |
| **All, with PII, MCP and skills** | **about 250 ms** |
| **All, core only** | **about 180 ms** |

The Langfuse and monitoring containers do not rise with chat traffic in these
runs.

One gateway worker is one process on one core. On the workstation it served
up to **9 chat turns per second** (8 concurrent users sending back to back)
before its core was full; beyond that, answers wait. An install made with
`llmport deploy` gives the gateway, the backend and PII a quarter of the
server's physical cores as workers each (2 on the 8-core VM); started from the
compose file alone, they run 4.

On the VM (Linux, two workers per service, every module), CPU time per chat
turn under the same load:

| Component | Plain chat | With a skill | With an MCP tool call |
|---|---|---|---|
| Gateway | 68 ms | 70 ms | 86 ms |
| Backend | 20 ms | 23 ms | 19 ms |
| Postgres | 22 ms | 13 ms | 31 ms |
| Skills | 18 ms | 24 ms | 19 ms |
| RabbitMQ | 14 ms | 20 ms | 19 ms |
| MCP (and the MCP server) | 4 ms | 5 ms | 23 ms |
| PII (local model: not scanned) | 2 ms | 3 ms | 2 ms |
| **All** | **166 ms** | **175 ms** | **216 ms** |

16 users made 21.7 plain chat turns per second (first token after 0.3 s at the
median, 0.9 s at the 95th percentile) with 3.6 of the VM's 8 cores busy on
average: the server was not the limit at that load.

As a rule of thumb: a user in a conversation sends a turn every 30 to 60
seconds, so one chat turn per second is 30 to 60 people chatting at the same
moment, and the 8-core VM served that load for several hundred.

## Disk

| What | Size | Needed when |
|---|---|---|
| Service images | about 11 GB | Always |
| Runtime image, x86_64 | 14.4 GB | You add x86_64 GPU machines |
| Runtime image, GB10 (DGX Spark) | 11.9 GB | You add DGX Spark machines |
| Model store | The models you host | Models are downloaded to the server and copied to machines from it |
| Postgres data | 254 MB after weeks of testing | Always; grows with chat history |
| ClickHouse data | Grows with traces | Langfuse tracing enabled |
| Build cache | about 19 GB | Only for installs built from source |

Plan **60 GB** for the core, one runtime architecture and a few small models;
**100 GB or more** plus the size of the models you intend to host.

## Database connections

Every service keeps a pool of database connections in each of its worker
processes, and all of them share one Postgres. Postgres accepts
`max_connections` less 3 reserved for its superuser: 250 in the shared compose
file (`POSTGRES_MAX_CONNECTIONS` in `.env` changes it), 100 in a Postgres of
your own unless you raise it. What each
service can open at its peak, workers × (connections kept + opened on demand):

| Service | `llmport deploy`, 8 cores (the VM) | Compose file alone |
|---|---|---|
| Gateway | 2 × (5 + 5) = 20 | 4 × (5 + 5) = 40 |
| Backend | 2 × (5 + 5) = 20 | 4 × (5 + 5) = 40 |
| Backend worker | 2 × (5 + 5) = 20 | 2 × (5 + 5) = 20 |
| PII | none | none |
| MCP | 1 × (5 + 10) = 15 | 1 × (5 + 10) = 15 |
| Skills | 1 × (5 + 10) = 15 | 1 × (5 + 10) = 15 |
| **Core** | **60** | **100** |
| **Core + PII + MCP + skills** | **90** | **130** |

PII has pool settings but opens no connection: its database layer is unused.
Langfuse's web and worker containers use the same Postgres with pools of their
own, not counted here.

Each service checks at startup whether its own pools fit and logs the result:

- an **error** when the service alone can open more connections than Postgres
  accepts, with the settings to change;
- a **warning** when it can take more than half;
- the numbers otherwise.

The check sees one service at a time, so add the table up for the whole
install: every configuration above fits in the shared compose file's 250. A
Postgres of your own at its default of 100 does not fit a core install from the
compose file alone (100) or any install with the optional modules.
`llmport deploy` sizes the services with `llmport tune`, as `upgrade` does:
workers by the CPU share, pools the same for every worker (5 kept, 5 on
demand), and `POSTGRES_MAX_CONNECTIONS` in `.env` as the peaks above added up,
plus 100 for Langfuse, migrations and backups, at least 250, in steps of 50:
250 at 8 physical cores, 350 at 32, 500 at 64. The settings per service are
`<PREFIX>WORKERS_COUNT`, `<PREFIX>DB_POOL_SIZE` and `<PREFIX>DB_MAX_OVERFLOW`,
with the prefixes `LLM_PORT_API_`, `LLM_PORT_BACKEND_`, `LLM_PORT_MCP_` and
`LLM_PORT_SKILLS_`; the backend worker takes the backend's.

## What grows with what

| When you add | What grows |
|---|---|
| Users chatting at once | Gateway and backend CPU (first); Postgres connections and CPU |
| The PII module | 0.8 GB memory; 42 ms of CPU per chat turn |
| MCP tools, skills | About 170 MB each; 11 and 17 ms per chat turn |
| Gateway or backend workers | 10 to 12 Postgres connections each at their peak (see [Database connections](#database-connections)) |
| Models hosted | Disk (model store); nothing on the server's CPU or memory |
| GPU machines | Nothing measurable on the server; a runtime image per architecture on disk |
| Tracing (Langfuse) | ClickHouse disk and CPU; not yet measured under chat load |

## Not yet measured

- Where the 8-core VM saturates: 16 users kept it under half busy.
- RAG Lite: document ingestion and search.
- Langfuse tracing under chat load.
