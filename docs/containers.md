# Containers

The **Containers** pages manage Docker on the LLM.Port server: the containers
LLM.Port itself runs, and any you add next to it, such as an MCP server or a
small service your models call. They do not manage the GPU machines; their
runtimes are deployments (see [Deploying models](model-marketplace.md)).

Everything below was taken on the test install (Ubuntu VM, `ray` at `a9d1714`,
2026-09-26; the create form and editing at `1892550`; the state tiles on the
development install at `4544c24`).

---

## The list

**Containers → Containers** lists every container on the server, the ones
LLM.Port runs included. Four tiles above the list count them:

![The state tiles above the list](images/dashboard/05-containers-tiles.png)

| Tile | Counts |
|---|---|
| Running | Running containers, of all listed |
| Not running | Exited, created or paused |
| Unhealthy | Running, but failing their health check |
| Restarting | Restarting, often in a crash loop |

Each tile is also a filter: select it to list only those containers, select
it again to list all. The **Class** filter still applies, and the counts
follow it.

![The containers on the server](images/containers/01-containers-list.png)

| Column | What it shows |
|---|---|
| Name | The container's name and short id |
| Image | What it runs |
| State | Running, exited or paused, and for how long |
| Class | What the console may do with it (below); change it from the arrow |
| Endpoint | The host port it is published on, if any (`0.0.0.0:8088`) |
| Scope | The team or unit that owns it |
| Actions | Start, stop, pause, restart, delete, details: only those its class allows. The column is at the right edge; on a narrow screen scroll the table sideways |

### Classes

A container's class decides what the console lets anyone do with it:

| Class | For | View, logs | Start, restart | Stop | Pause, exec, delete |
|---|---|---|---|---|---|
| `SYSTEM_CORE` | Postgres, Redis, nginx, RabbitMQ, the observability stack | yes | yes | no | no |
| `SYSTEM_AUX` | LLM.Port's own services (backend, gateway, modules) | yes | yes | yes | no |
| `MCP` | MCP servers | yes | yes | yes | no |
| `TENANT_APP` | Containers you run | yes | yes | yes | yes |
| `UNTRUSTED` | Anything not classified | yes | no | no | no |

Every class can be **edited** (below), unless its policy is locked.

A container created elsewhere is classified by its name the first time the
list sees it: `llm-port-*` services and the infrastructure above are system
containers, a name starting `mcp-` is `MCP`, anything else is `UNTRUSTED`.

**Root mode** lifts the class limits for the admin who activates it, for
system maintenance. A **locked** policy blocks stop, pause, exec, edit and
delete even then. (`restricted` is accepted but currently behaves like
`free`.)

---

## Getting an image

A container starts from an image on the server; creating one does not pull
it. Pull it first under **Containers → Images**: image name and tag, then
**Pull Image**. The progress shows while it downloads.

![Pulling node:22-alpine](images/containers/02-pull-image.png)

When it is done it is in the list, and the create form suggests it:

![The pulled image](images/containers/03-image-pulled.png)

---

## Creating a container

**Containers → Create** (or **Create** on the list). The example serves a
greeting on port 8088 from a one-line Node server:

![Create Container: image, name, command, class, runtime options](images/containers/04-create-form.png)

- **Image**: the image and tag; suggestions come from the images on the
  server.
- **Container Name**: how it is addressed. On LLM.Port's network another
  container reaches it by this name (`http://docs-demo:8080`).
- **Command Override**: the command to run instead of the image's default,
  written as in a shell; quoted parts stay one argument.
- **Class**: `TENANT_APP`, what the form starts on, for a container of your
  own; `MCP` for an MCP server. `UNTRUSTED` cannot be started, stopped or
  deleted from the console.
- **Policy** and **Scope**: see above; `platform` is the default scope.

![Ports, environment, volumes and the network](images/containers/05-create-form-bottom.png)

- **Port Bindings**: publish a container port on the server
  (`8088 → 8080/tcp`). Leave it empty when only LLM.Port's services need to
  reach it: they do so by name over the network. A host port must be free;
  on the shared install 3001 is Grafana's.
- **Environment Variables**: key and value, passed to the process
  (`GREETING`). They are visible to anyone who can see the container; do not
  put secrets you would not show them here.
- **Volume Mounts**: a host path and where it appears in the container.
- **Start immediately after creation**: otherwise it is created stopped.
- **Networks**: **`llm_port_shared_default`** puts it on LLM.Port's own
  network, where the gateway, backend and MCP service can reach it by name.
  Without one it joins Docker's default bridge.

**Create & Start** opens the new container:

![The container, running](images/containers/06-container-overview.png)

---

## Working with it

On the list it shows its endpoint and the actions its class allows:

![docs-demo on the list, published on 8088](images/containers/07-container-row.png)

**Logs** shows the last 300 lines; **Refresh** reads them again:

![The container's log](images/containers/08-container-logs.png)

**Exec** opens a shell session in the container and shows its id, for a
websocket client; there is no terminal on the page yet. It needs a class that
allows exec (`TENANT_APP`) or root mode.

![An exec session](images/containers/09-container-exec.png)

**Stop** before **Delete**: a running container is not deleted ("The
container is running: stop it before you delete it."). A
process that ignores the stop signal is killed after ten seconds, which shows
as `Exited (137)`; Node and many others run as the container's first process
do that unless the image adds an init.

![Stopped, ready to delete](images/containers/10-container-stopped.png)

**Delete** asks for confirmation in a browser dialog, then removes the
container. Its image stays under Images.

---

## Editing a container

**Edit** on the container's page opens the create form with what the
container was created with: its image, command, environment, ports, volumes,
network and class. What its image supplies (the image's own environment
variables, its default command) is not shown; a new image brings its own.

![Editing docs-demo: its greeting changed](images/containers/12-edit-form.png)

Docker cannot change these settings in a container that exists, so **Save and
replace** replaces it, after a confirmation:

![Replace docs-demo?](images/containers/13-edit-confirm.png)

- a new container is made with the settings, and joins the same networks
  under the same names (other containers keep reaching it by name);
- what the form does not show is kept: GPU devices, restart policy, labels,
  health check, the host address a port was bound to;
- the old one is stopped and removed once the new one runs. The container gets
  a **new ID**, and files it wrote outside its volumes are lost;
- if the new one cannot be made or started (an image that is not on the
  server, a host port in use), the old one is put back as it was, under its
  name, running if it ran, and the page says why.

![The replaced container: same name, a new ID](images/containers/14-edited.png)

Class, policy and scope move to the new container. A container with a
**locked** policy cannot be edited.

LLM.Port's own containers can be edited too, for a test or a quick fix: the
next `llmport deploy` or `llmport upgrade` makes them again from the compose
file and `.env`, without the edits, and the form says so. The backend's own
container cannot be edited from the console, since the backend would stop
itself half way: change its settings in `.env` and run `llmport upgrade`.

---

## Networks

**Containers → Networks** lists Docker's networks. `llm_port_shared_default`
is LLM.Port's; its count is the containers on it. The others are Docker's own.

![Docker networks](images/containers/11-networks.png)

---

## Used by

- [MCP servers](mcp-servers.md): an MCP server can run as a container here,
  and be registered by its name on the network.
