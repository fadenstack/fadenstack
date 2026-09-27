# Connections and TLS

Every connection between the parts of a Fadenstack install -- browsers, the
server's containers, the GPU machines, Ray, vLLM, the internet -- and whether it
is encrypted today. Drawn for the release VM and the DGX Spark pair: one server
host, one Ray head, one Ray worker. Read from the code on branch `ray` at
`d2432f3` and checked live, 27 September 2026.

How TLS works where it is used -- the install CA, the machines following the
server to HTTPS, renewal, signed commands -- is in
[HTTPS and trust](https-and-trust.md).

- [The map](#the-map)
- [What is encrypted now, and what should move next](#what-is-encrypted-now-and-what-should-move-next)
- [Encrypted today](#encrypted-today)
- [Plain, never leaves the host](#plain-never-leaves-the-host)

## The map

| Line | Meaning |
|---|---|
| Green, solid | TLS, always: calls out to the internet, and the gateway to the models on the machines (mutual TLS). |
| Green, dashed | TLS once HTTPS is on: into nginx. A new install starts on plain HTTP. |
| Red, thick | Plain across the network, **convert first**: prompts to a vLLM found on a machine, not started by it. |
| Orange | Plain across the network, **convert next**: logs; Ray cluster traffic (token-authenticated). |
| Amber, dashed | Plain across the network, low value: metrics, or content already checked by digest. |
| Purple, dotted | Cannot be TLS: keep the network isolated. GPU-to-GPU tensor traffic over RDMA. |
| Grey, thin | Plain, never leaves the host: the Docker network, a local socket or the loopback address. |

A green outline marks a port that faces the network, each TLS: the server's
nginx and each machine's model-TLS proxy. A red outline marks a listener with
no authentication of its own. The machines open no port for control: their
agents dial out to nginx. How model traffic is protected is in
[HTTPS and trust: Model traffic](https-and-trust.md#model-traffic-the-gateway-and-the-machines).

```mermaid
flowchart LR
  subgraph CLIENTS["Users and clients"]
    direction TB
    BR["Browser<br/>console, chat"]
    API["API clients<br/>SDKs, curl"]
  end

  subgraph SERVER["Server · one host · Docker network llm_port_shared"]
    NG["nginx :80 / :443<br/>the only public port"]
    subgraph APPS["Application"]
      FE["frontend :3000"]
      BE["backend :8000<br/>/api"]
      BW["backend-worker"]
      GW["gateway :8000<br/>/v1 OpenAI API"]
      PII["PII + PII worker"]
      MCP["MCP service"]
      SK["skills"]
      RAG["RAG, docling<br/>optional modules"]
    end
    subgraph DATA["Data stores · 127.0.0.1 only"]
      PG[("Postgres")]
      RD[("Redis")]
      RMQ[("RabbitMQ")]
      S3[("MinIO")]
      CH[("ClickHouse")]
    end
    subgraph OBS["Observability · 127.0.0.1 only"]
      PROM["Prometheus :9099"]
      LOKI["Loki :3100"]
      GRAF["Grafana"]
      ALLOY["Alloy"]
      LF["Langfuse web + worker"]
    end
  end

  subgraph MA["GPU machine A · Ray head · DGX .71"]
    AGA["node agent<br/>dials out, no inbound port"]
    DKA["Docker engine"]
    MTLS["model-TLS proxy · nginx<br/>published port, e.g. :8010<br/>client certificate required"]
    subgraph RAYA["Ray head container"]
      HEAD["Ray head<br/>GCS :6390 · token auth<br/>dashboard :8265 on 127.0.0.1"]
      SERVE["Ray Serve proxy<br/>127.0.0.1:28010"]
      VLA["vLLM engine<br/>TP rank 0"]
    end
    SVA["standalone vLLM (ours)<br/>127.0.0.1:28000"]
    FVA["found vLLM (not ours)<br/>0.0.0.0 · no auth"]
  end

  subgraph MB["GPU machine B · Ray worker · DGX .49"]
    AGB["node agent<br/>dials out, no inbound port"]
    DKB["Docker engine"]
    subgraph RAYB["Ray worker container"]
      RWB["raylet, object manager"]
      VLB["vLLM engine<br/>TP rank 1"]
    end
  end

  subgraph INET["Internet"]
    HF["Hugging Face Hub"]
    PROV["Remote model providers"]
    XMCP["Remote MCP servers"]
    REG["Container registries"]
  end

  BR -->|"HTTPS · plain HTTP answers 307"| NG
  API -->|"HTTPS /v1"| NG
  AGA -->|"WSS command stream, signed<br/>HTTPS models, images, trust,<br/>machine certificate"| NG
  AGB -->|"WSS + HTTPS, same"| NG
  NG --> FE
  NG -->|"/api"| BE
  NG -->|"/v1"| GW
  NG -->|"/grafana"| GRAF
  BE --> DATA
  BW --> DATA
  GW --> DATA
  GW --> PII
  GW --> MCP
  GW --> SK
  GW --> RAG
  GW -->|"traces"| LF
  LF --> DATA
  BE -->|"queries"| PROM
  BE -->|"queries"| LOKI
  GRAF --> PROM
  GRAF --> LOKI
  ALLOY -->|"server logs"| LOKI
  BE -->|"HTTPS model downloads"| HF
  GW -->|"HTTPS"| PROV
  MCP -->|"HTTPS"| XMCP
  DKA -->|"HTTPS image pulls"| REG
  VLA -->|"HTTPS weights<br/>unless air-gapped"| HF
  GW ==>|"HTTPS · mutual TLS<br/>gateway certificate · every prompt"| MTLS
  MTLS --> SERVE
  MTLS --> SVA
  GW ==>|"HTTP · prompts · found vLLM only<br/>CONVERT · HIGH (restart it under LLM.Port)"| FVA
  AGA -->|"HTTP :3100 container logs · Loki has no auth<br/>CONVERT · MEDIUM"| LOKI
  AGB -->|"HTTP :3100, same"| LOKI
  RWB <-->|"Ray gRPC :6390 + raylet and object ports<br/>control, objects, Serve calls with prompts<br/>token-authenticated, not encrypted · MEDIUM"| HEAD
  PROM -->|"HTTP /metrics · LOW"| HEAD
  PROM -->|"HTTP /metrics · LOW"| RWB
  AGB -->|"image seed over the fabric<br/>one-off token, digest-pinned · LOW"| AGA
  VLA <-->|"NCCL over RoCE / RDMA · tensors<br/>no TLS possible · ISOLATE"| VLB
  AGA -->|"docker.sock"| DKA
  DKA -->|"runs"| RAYA
  DKA -->|"runs"| SVA
  DKA -->|"runs"| MTLS
  SERVE --> VLA
  AGB -->|"docker.sock"| DKB
  DKB -->|"runs"| RAYB
  RWB --> VLB

  classDef public stroke:#2da44e,stroke-width:2.5px
  classDef exposed stroke:#d1242f,stroke-width:2.5px
  class NG public
  class FVA exposed
  class MTLS public
  linkStyle 0,1,2,3 stroke:#2da44e,stroke-width:2.5px,stroke-dasharray:7 5
  linkStyle 4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,28,29,38,39,40,41,42,43,44,45 stroke:#8c959f,stroke-width:1.2px
  linkStyle 22,23,24,25,26,27 stroke:#2da44e,stroke-width:2.5px
  linkStyle 30 stroke:#d1242f,stroke-width:3.5px
  linkStyle 31,32,33 stroke:#e16f24,stroke-width:2.8px
  linkStyle 34,35,36 stroke:#b08800,stroke-width:2px,stroke-dasharray:4 4
  linkStyle 37 stroke:#8250df,stroke-width:2.5px,stroke-dasharray:2 4
```

## What is encrypted now, and what should move next

| Priority | Connection | What crosses it | Today | Recommended |
|---|---|---|---|---|
| Done | Gateway → models on the machines: Ray Serve and the vLLM LLM.Port starts | Every prompt and every completion | Mutual TLS since agent 0.1.16: each machine's nginx accepts only the gateway's certificate from the machine CA; the model listens on 127.0.0.1. Clusters started before are moved with one button. | Nothing. Measured on the DGX pair: chat 1.85 s as before; plain HTTP, no certificate, a foreign CA and another machine's key all refused; a CA replacement interrupts about 35 s. |
| High | Gateway → a vLLM found running on a machine (not started by LLM.Port) | Every prompt and every completion | Plain HTTP to whatever that container listens on; its own server, with no authentication by default. | Start the model under LLM.Port instead (it is then behind the proxy), or firewall its port to the server. |
| High | Browsers, API clients and agents → nginx | Sign-ins, API keys, prompts, machine credentials | TLS only after HTTPS is switched on; a new install runs plain HTTP. | Make `--tls generate` the default of `llmport deploy`. Everything else is already built. |
| Medium | Agents → Loki `:3100` | Container logs; vLLM can log request text | Plain HTTP, and Loki has no authentication. Only happens when `LOKI_BIND` publishes Loki. | Push through nginx on a path authenticated by the machine credential. It inherits HTTPS, and Loki stays on 127.0.0.1. |
| Medium | Ray between machines: GCS, raylet, object manager, worker ports | Cluster control, objects, and Serve's calls into replicas on the other machine, prompts included | Plain gRPC, authenticated with the cluster's token (Ray token authentication). | Keep it on the isolated fabric (today a direct RoCE link). Then `RAY_USE_TLS=1` with per-machine certificates from the machine CA, which the agents already renew. Measure the cost on object transfers first. |
| Low | Prometheus → Ray metrics on each machine | Numbers | Plain HTTP pull over the LAN. | Later: scrape through the agent, or TLS with the machine certificate. |
| Low | Runtime image copy between machines (image seed) | A public runtime image | Plain HTTP on the fabric; one-off token; the copy is checked against the pinned digest. | Nothing needed: integrity is already covered and the content is not secret. |
| Isolate | NCCL / RDMA between GPUs, and the fabric check | Tensors, test traffic | Plain over RoCE. | TLS is not possible at this bandwidth. Keep the fabric physically separate: a direct cable or its own VLAN. |
| Fine | Containers on the server; containers on a machine; the proxy to its model | Everything | Plain on the Docker bridge, a local socket or the loopback address; never leaves the host. | Fine while they share the host. A Postgres, Redis or MinIO moved to another host needs TLS (`sslmode=verify-full`, `rediss://`, HTTPS). |

## Encrypted today

- The gateway to the models on the machines: mutual TLS through each machine's nginx (agents 0.1.16+)
- Browser and API clients to nginx, once HTTPS is on (plain HTTP answers 307)
- Agents to the server: command stream (WSS), enrollment, credential rotation, model and image downloads, trust refresh, machine certificates
- Every command to a machine is HMAC-signed, TLS or not (agents 0.1.15+)
- Backend to Hugging Face; gateway to remote providers; MCP to remote MCP servers; Docker to registries

## Plain, never leaves the host

- nginx to frontend, backend, gateway, Grafana
- Services to Postgres, Redis, RabbitMQ, MinIO, ClickHouse
- Prometheus, Loki, Grafana, Alloy, Langfuse on the server
- Agent to Docker; the model-TLS proxy to Serve or vLLM on 127.0.0.1; Serve to its local replica
- Ray dashboard `:8265`, bound to 127.0.0.1
