# Operate

This section is for whoever runs Fadenstack: the server, the GPU machines and the clusters they form. It covers
installing, upgrading, securing and backing up the server, connecting machines, and keeping it all running.

## What you run

| Part | What it is | How you manage it |
|---|---|---|
| **The server** | One Linux machine that runs Fadenstack as a set of Docker containers. It needs no GPU. | Install and upgrade it with the `faden` command-line tool. |
| **GPU machines** | Linux machines with NVIDIA GPUs that run the models. | Add a machine by running one command on it and approving it in the console. |
| **Clusters** | A cluster groups one or more machines so they can serve models together. | Create and manage clusters in the console; the models themselves are deployed by an administrator (see [Deployments](../administer/deployments.md)). |

A cluster runs on its machines, not on the server. Its models keep running while the server restarts or is
upgraded; only requests through the gateway wait until the gateway is back.

??? info "More about the parts"
    - **The server's containers:** the console, the API, the OpenAI-compatible gateway (`/v1`), the database, and
      the logs and metrics stores.
    - **The node agent:** each GPU machine runs a small program, the node agent (`faden-agent`), that connects out
      to the server and carries out what the console asks of it.
    - **The runtime image:** the machines run the models inside a runtime image: a container with Ray, which
      spreads the work over the machines, and vLLM, the engine that runs a model.

## The order of work

1. Check the [System requirements](requirements.md) for the server and the machines.
2. [Install the server](installing.md) and sign in.
3. Decide on the certificate: [HTTPS and trust](https.md). A new install serves HTTPS from the start.
4. Open the [ports](network.md) between users, the server and the machines.
5. [Add the GPU machines](machines.md).
6. [Create a cluster](clusters.md) from them.
7. Hand over to the administrator, who deploys models and adds people (see [Administer](../administer/index.md)).
8. Keep [backups](backups.md), and [upgrade](upgrading.md) when a release comes out. If the install still runs on published keys, or `.env` may have leaked, [rotate the keys](rotating-keys.md).

## When something goes wrong

- [Failures and logs](failures.md): what recovers by itself, what you see while it does, and where the logs are.
- [Troubleshooting](troubleshooting.md): common problems, their cause, and what to do.

## Also here

- [Existing vLLM servers](existing-vllm.md): put a vLLM you already run behind the gateway.
- [Taking over clusters](taking-over.md): a rebuilt server takes over the clusters its machines still run.
- [Containers](containers.md): the Docker containers on the server, as the console shows them.
- [Third-party components](third-party.md): what the server and the runtime images are built from.
