# System requirements

What the server and the GPU machines need before you install anything. The server is sized by the services it
runs and the people using them; a GPU machine is sized by the models it runs.

## The server

| | Minimum | Recommended |
|---|---|---|
| Operating system | Linux, x86_64 | |
| CPU cores | 4 | 8 |
| Memory, core services | 6 GB | 8 GB |
| Memory, with the PII, MCP and skills modules | 8 GB | 12 GB |
| Disk | 60 GB | 100 GB, plus the size of the models you intend to host |
| GPU | none | none |

- **Docker Engine 24 or later with Compose v2.** The user who runs `faden` must be able to use Docker (be in the
  `docker` group).
- **Python 3.12 or later**, with `pipx` or `uv`, to install the `faden` command-line tool.
- **Ports 80 and 443 free** on the server. Other ports can be chosen; see [Network and ports](network.md).
- **Internet access while installing and upgrading**, to pull the service images and the runtime images. See
  [What needs the internet](#what-needs-the-internet).

Add memory for anything else you run on the server. The server needs no GPU: the models run on the machines you
add. A server that has an NVIDIA GPU can also be added as a machine.

`faden doctor` checks a host before you install: memory, free disk, Docker, Compose, and whether the ports the
install publishes are free.

### What grows with what

| When you add | What grows on the server |
|---|---|
| People chatting at the same time | CPU of the gateway and the console API first, then database connections |
| The PII module | About 2 GB of memory (it loads a language model), more CPU per chat turn |
| MCP tools, skills | A few hundred MB of memory |
| Models | Disk: every model is downloaded to the server and copied to the machines from there |
| GPU machines | One runtime image per kind of machine on disk; nothing measurable in CPU or memory |
| Tracing | Disk for the trace store |

`faden deploy` and `faden upgrade` size the services for the host they run on: how many worker processes each
service gets, and how many database connections Postgres accepts. To size them again after changing the
hardware, run `faden tune` and then `faden up`.

### Disk

| What | Size | Needed when |
|---|---|---|
| Service images | about 11 GB | always |
| Runtime image, x86_64 machines | about 15 GB | you add x86_64 GPU machines |
| Runtime image, NVIDIA DGX Spark machines | about 12 GB | you add DGX Spark machines |
| Model store | the models you host | always |
| Database | grows with chat history | always |
| Trace store | grows with traces | tracing is on |

The server keeps a runtime image for each kind of machine you have, because the machines download it from the
server, not from the internet. If you only have x86_64 machines, install with
`faden deploy --runtime-images x86_64` to skip the other one.

Backups land on the same disk by default; leave room for them, or send them elsewhere (see [Backups](backups.md)).

## GPU machines

| | |
|---|---|
| Operating system | Linux, x86_64 or aarch64 |
| GPU | NVIDIA only, with a working driver: `nvidia-smi` must print your card |
| Containers | Docker or Podman, running, and usable by the user the agent runs as, with access to the GPU (for Docker, the NVIDIA Container Toolkit) |
| Service manager | systemd: the agent runs as a system service, or as a user service when installed without `sudo` |
| Disk | about 15 GB for the runtime image, plus room for the models it runs; a runtime switch needs room for a second image while it runs |
| Network | Reach the server's HTTPS port. The server must reach the machine's model port. Machines in one cluster must reach each other. See [Network and ports](network.md) |

Two kinds of machine are supported, each with its own runtime image:

- **x86_64 machines with NVIDIA GPUs**, from the Turing generation to Blackwell.
- **NVIDIA DGX Spark** (GB10, aarch64).

A machine of another kind joins, but cannot be put in a cluster: the cluster wizard says that no certified
runtime image runs on it.

!!! note
    A model runs on one kind of GPU at a time. Mixed machines can share a cluster, but each copy of a model
    lands on machines of one kind. Machines that should run one model together need the same GPUs.

GPU memory decides which models fit. The model marketplace shows, for each model and cluster, whether it fits on
a share of one accelerator, on one, across several, or not at all (see [Models](../administer/models.md)).

## What needs the internet

| Who | Reaches | When |
|---|---|---|
| Server | Container registries (`ghcr.io`, Docker Hub and the others the images come from) | installing and upgrading |
| Server | Hugging Face | downloading models |
| Server | Remote model providers, remote MCP servers | if the administrator connects any |
| Server | GitHub | to check its own copy of the agent builds, if it holds one |
| GPU machines | GitHub (the project's releases) | installing or updating the agent, unless the server holds the agent builds |
| GPU machines | Hugging Face | only on a cluster set to let its machines download models themselves |

Everything else a machine needs, the runtime image and the models, comes from the server. A machine without
internet access works as long as the server holds the agent builds; see
[Machines without internet access](machines.md#machines-without-internet-access).
