# Network and ports

Which connections an install makes, between which machines, on which ports, and which of them are encrypted. Use
it to set up firewalls and to decide which networks the machines share.

## At a glance

| From | To | Port | What crosses it | Encrypted |
|---|---|---|---|---|
| Browsers, API clients | Server | 443 (HTTPS), 80 (redirects to HTTPS) | The console, the API, the gateway (`/v1`) | Yes, once HTTPS is on |
| GPU machines | Server | 443, or 80 while HTTPS is off | The agent's connection: commands, status, downloads of the agent, runtime images and models, logs | Yes, once HTTPS is on |
| Server | Each cluster's head machine | The cluster's serving port (8010 by default) | Every prompt and answer, from the gateway to the models | Yes, always (mutual TLS) |
| Server | A machine running a vLLM you routed | That vLLM's port plus 20000 | Prompts and answers | Yes, always (mutual TLS) |
| Server | Cluster machines | Ray's metrics ports | Metrics: numbers only | No |
| Cluster machines | Each other, on the cluster's network | Ray's head port (6390 by default), and ports Ray picks itself | Cluster control, data, calls into model copies on other machines (prompts included), the runtime image copied between machines | Authenticated always; encrypted once the cluster's **Traffic between machines** is on |
| Cluster machines | Each other, on the cluster's network | GPU-to-GPU (RDMA) | A model's data while one copy spans machines | No: cannot be encrypted |
| Server | The internet | 443 | Image pulls, model downloads, remote providers and MCP servers you connect | Yes |

The machines open no port for the server to control them: their agents connect out to the server and keep that
connection open. To serve models, though, the server must reach the cluster's head machine on its serving port,
and the machines of one cluster must reach each other directly. A machine behind NAT cannot join a cluster with
machines on the other side of it.

How each of these is protected, and what to switch on: [HTTPS and trust](https.md).

## Users and the server

Only the server's front door faces the network: port 443 for HTTPS, and port 80, which only redirects to HTTPS
once it is on. The console, the API (`/api`), the OpenAI-compatible gateway (`/v1`) and Grafana (`/grafana/`) are
all served there.

To serve on other ports:

```bash
faden ports                            # show them, and the addresses the console is reached at
faden ports --http 8080 --https 8443   # change them; the proxy moves at once
```

They are `FADEN_HTTP_PORT` and `FADEN_HTTPS_PORT` in `~/fadenstack/.env`. Machines and API clients then need the
new port in the server's address.

Everything else on the server listens on its loopback address only and is not reachable from the network:
Postgres (5432), Redis (6379), RabbitMQ (5672, 15672), ClickHouse (8123, 9000), MinIO (9090, 9091), Prometheus
(9099), Loki (3100), Grafana (3001), Langfuse (3002), the gateway's own port (8001) and a few more. These ports
must still be free on the server itself; `faden doctor` lists every port the install uses and whether something
else holds it.

!!! warning
    Do not publish those ports on the network. Loki has no authentication of its own, and Grafana's own port shows
    every dashboard to anyone who reaches it. The console reaches both through the front door.

## The machines and the server

Each machine needs to reach the server's front door, at the address you pick in **Machines → Add a machine**.
The panel lists the server's addresses with the interface each belongs to; pick one on the network the machine
shares with the server. The server itself does not open connections to the agent.

The server does open connections to the machines for two things:

- **The models.** The gateway sends every request to the head machine of the cluster that serves the model, on
  the cluster's serving port. Allow the server to reach that port on every head machine.
- **Metrics.** The server's Prometheus collects metrics from the cluster's machines. If a firewall blocks it, the
  cluster page shows no metrics; serving is not affected.

## Between the machines of a cluster

A cluster runs on one network that all its machines share: you confirm it when you create the cluster, and can
change it under **Advanced** on the cluster's page. On that network the machines must reach each other freely:

- Ray's head listens on the cluster's head port (6390 for a new cluster);
- Ray picks further ports itself for its node managers, its data transfer and its worker processes;
- a machine that needs the runtime image gets it from a machine of the cluster that has it, over this network;
- GPU-to-GPU traffic (RDMA, for example RoCE between NVIDIA DGX Spark machines) runs here too.

Do not filter ports between the machines on that network. Keep the network itself to the cluster's machines: a
direct cable, a switch of their own or a VLAN. GPU-to-GPU traffic cannot be encrypted, and Ray's own traffic is
only encrypted once the cluster's **Traffic between machines** switch is on.

A machine added to a running cluster is checked first: it must reach the head on the cluster's network (see
[Clusters](clusters.md#add-machines-to-a-running-cluster)).

## Ports a GPU machine must have free

| Port | On | For |
|---|---|---|
| The cluster's head port (6390 by default) | the head machine | Ray's head |
| The cluster's serving port (8010 by default) | the head machine | the models, behind the proxy that only lets the gateway in |
| The serving port plus 20000 (28010 by default) | the head machine, loopback only | the models themselves |

Ray's dashboard listens on the head machine's loopback address only (8265). If something on a machine already
holds one of these ports, the cluster cannot start and the cluster page shows Ray's error, ending in
`Address already in use`. Free the port, then select **Try again**. A model published on a port another program
holds is refused before it starts: "Port … is already in use on this machine by another program".

## What reaches the internet

See [System requirements](requirements.md#what-needs-the-internet). In short: the server pulls images and
downloads models; the machines need nothing from the internet if the server holds the agent builds.
