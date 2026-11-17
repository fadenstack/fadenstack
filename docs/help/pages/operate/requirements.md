# System requirements

What the server and the GPU machines need before you install anything. The server is sized by the services it
runs and the people using them; a GPU machine is sized by the models it runs.

## The server

| | Minimum | Recommended |
|---|---|---|
| Operating system | Linux, x86_64 | |
| CPU cores | 4 | 8 |
| Memory, core services | 8 GB | 12 GB |
| Memory, with the multilingual PII, MCP and skills modules | 12 GB | 16 GB |
| Disk, with the runtime images for both kinds of machine (the default) | 150 GB | 250 GB, plus the size of the models you intend to host |
| Disk, with the runtime images for x86_64 machines only | 70 GB | 120 GB, plus the size of the models you intend to host |
| GPU | none | none |

`faden doctor` fails a server with less than 7 GB of memory, which is what a server with 8 GB reports once the
kernel has its share, and one with less than 20 GB of free disk. Those are its floor for the services alone; the
disk figures above add the runtime images that `faden deploy` pulls (see [Disk](#disk)).

| The server also needs | |
|---|---|
| **Docker Engine 24 or later with Compose v2** | The user who runs `faden` must be able to use Docker (be in the `docker` group). |
| **Python 3.12 or later** | With `pipx` or `uv`, to install the `faden` command-line tool. |
| **Ports 80 and 443 free** | On the server. Other ports can be chosen; see [Network and ports](network.md). |
| **Internet access while installing and upgrading** | To pull the service images and the runtime images. See [What needs the internet](#what-needs-the-internet). |

Add memory for anything else you run on the server. The server needs no GPU: the models run on the machines you
add. A server that has an NVIDIA GPU can also be added as a machine.

`faden doctor` checks a host before you install: memory, free disk, Docker, Compose, and whether the ports the
install publishes are free.

### What grows with what

| When you add | What grows on the server |
|---|---|
| People chatting at the same time | CPU of the gateway and the console API first, then database connections |
| The PII module | Additional memory for its selected model and more CPU per chat turn; allow at least 4 GB for the multilingual PII container, plus the other services |
| MCP tools, skills | A few hundred MB of memory |
| Models | Disk: every model is downloaded to the server and copied to the machines from there |
| GPU machines | The runtime images for each kind of machine on disk; nothing measurable in CPU or memory |
| Tracing | Disk for the trace store |

To size the services again after changing the hardware, run `faden tune` and then `faden up`.

PII can use a compatible local NVIDIA GPU, or run on CPU without one. CPU handling is slower for concurrent
requests and long documents. `faden doctor` reports compatibility and CPU availability. See
[Local PII on CPU or GPU](installing.md#local-pii-on-cpu-or-gpu) for selection and fallback behavior.

??? info "More about sizing the services"
    `faden deploy` and `faden upgrade` size the services for the host they run on: how many worker processes each
    service gets, and how many database connections Postgres accepts.

### Disk

| What | Size | Needed when |
|---|---|---|
| Service images | about 17 GB | always |
| Runtime images, x86_64 machines | about 29 GB (two images of about 14.5 GB) | you add x86_64 GPU machines |
| Runtime images, NVIDIA DGX Spark machines | about 68 GB (two images of 35 and 33 GB) | you add DGX Spark machines |
| Model store | the models you host | always |
| Database | grows with chat history | always |
| Trace store | grows with traces | tracing is on |

The server keeps the runtime images for each kind of machine, because the machines download them from the
server, not from the internet. A release pins two per kind of machine: the current one and the one before it, for
clusters not switched yet. `faden deploy` pulls them for both kinds unless you say otherwise, about 97 GB on top
of the service images. If you only have x86_64 machines, install with `faden deploy --runtime-images x86_64` to
skip the DGX Spark ones; `faden runtime-images --check` shows what the server holds.

!!! warning "Ubuntu Server's default disk layout"
    On a large disk, Ubuntu Server's installer gives the root file system a logical volume of about 100 GB and
    leaves the rest of the disk unused. That fills up while the runtime images are pulled. Grow it before you
    install, for example with `sudo lvextend -r -l +100%FREE /dev/ubuntu-vg/ubuntu-lv`, or install with
    `--runtime-images` for the machines you have.

Backups land on the same disk by default; leave room for them, or send them elsewhere (see [Backups](backups.md)).

## GPU machines

| | |
|---|---|
| Operating system | Linux: x86_64, or aarch64 on NVIDIA DGX Spark only |
| GPU | NVIDIA only, with a working driver: `nvidia-smi` must print your card |
| Containers | Docker or Podman, running, and usable by the user the agent runs as, with access to the GPU (for Docker, the NVIDIA Container Toolkit) |
| Service manager | systemd: the agent runs as a system service, or as a user service when installed without `sudo` |
| Disk | about 15 GB for the runtime image, plus room for the models it runs; a runtime switch needs room for a second image while it runs |
| Network | Reach the server's HTTPS port. The server must reach the machine's model port. Machines in one cluster must reach each other. See [Network and ports](network.md) |

Two kinds of machine are supported, each with its own runtime image:

- **x86_64 machines with NVIDIA GPUs**, from the Turing generation to Blackwell. Their runtime image is not
  certified yet: the cluster's **Runtime** card says **Not certified**.
- **NVIDIA DGX Spark** (GB10, aarch64).

Other machines are not supported.

??? info "What happens with other machines"
    One without an NVIDIA GPU, or with another processor architecture, joins but cannot be put in a cluster: the
    cluster wizard says that no certified runtime image runs on it. Another aarch64 machine with an NVIDIA GPU is
    offered the DGX Spark image, which is not made for it.

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
