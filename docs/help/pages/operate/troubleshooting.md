# Troubleshooting

Problems an operator meets, with their cause and what to do. For what Fadenstack repairs by itself and where the
logs are, see [Failures and logs](failures.md).

## The server

**The console does not answer.**
Cause: a service is down, or the proxy cannot start on its port.
What to do: run `faden status`. For a service that is not running or unhealthy, read its log
(`faden logs faden-backend`, `faden logs nginx`). `faden doctor` shows whether something else holds port 80 or
443; change the ports with `faden ports --http 8080 --https 8443` if needed.

**Every page says "Fadenstack is being updated" or "Fadenstack is under maintenance".**
Cause: maintenance is on, from an upgrade or switched on by hand.
What to do: `faden maintenance status` says why and since when. `faden maintenance off` lifts it.

**Services restart over and over: Postgres, the log store, the trace store.**
Cause: usually a full disk.
What to do: check with `df -h`. Remove images no container uses (`docker image prune`), move old backups off the
server, then `faden up`. The dashboard warns earlier: **Disk space low**.

**The administrator's password is lost.**
What to do: `faden admin reset-password --email alex@example.com` sets a new one and shows it. Leave out
`--password` to have one generated.

**The browser warns about the certificate.**
Cause: the server uses a certificate it made, and this computer does not trust its authority yet.
What to do: import `~/fadenstack/tls/ca.crt` as a trusted certificate authority (see
[HTTPS and trust](https.md#trusting-a-certificate-made-on-the-server)).

**The browser console shows `Cannot read properties of undefined (reading 'keys')`.**
Cause: Grafana, in the dashboard panel on the console's front page, needs a secure page. Over plain HTTP it logs
this error; the dashboard still works.
What to do: serve the console over HTTPS.

**`faden upgrade` stops before it changes anything.**
What to do: read its message; [Upgrading](upgrading.md#when-it-does-not-work) lists them.

## Adding a machine

**The installer asks for a `sudo` password you do not have.**
What to do: run the line without `sudo`. The agent installs in your home directory and runs as a user service.
Your user still needs to be able to use Docker or Podman.

**The machine showed a code, but nothing appears in the console.**
Cause: the request expired, or the **AI Infrastructure** group in the navigation is collapsed (it then shows a dot
instead of the count).
What to do: open **Machines**. If nothing is waiting there, run the line again for a new code.

**The agent stops when you log out.**
Cause: the agent runs as a user service, and *linger* is not on for your user.
What to do: an administrator of the machine runs `sudo loginctl enable-linger <user>` once.

**The machine joined but shows no GPU.**
Cause: the service cannot find the NVIDIA driver, although your shell can.
What to do: check that `nvidia-smi` is on a standard path, and that the service runs the agent you installed:
`sudo systemctl show faden-agent -p ExecStart` (or `systemctl --user show faden-agent -p ExecStart`).

**The installer stops with `the download does not match the expected digest`.**
Cause: the server's copy of the agent in `~/fadenstack/agent-binaries/` is from another release.
What to do: replace it with the current release's builds.

**A machine you stopped or removed still shows healthy.**
Cause: an agent is still running somewhere on it, for example one started by hand with `faden-agent run`, which is
not a service. A machine is marked offline as soon as its agent disconnects.
What to do: on the machine, run `pgrep -af faden-agent`. If anything comes back, `sudo faden-agent stop` (or
`faden-agent stop`) ends it.

**A machine's logs do not show up under Logs.**
Cause: its agent is too old to send its logs through the server.
What to do: run the install line on it again to update it.

**After `faden tls off`, the machines stay offline.**
Cause: an agent that moved to HTTPS never moves back by itself.
What to do: on each machine, `faden-agent configure --set BACKEND_URL=http://ai.example.internal`, then restart the
agent (`sudo systemctl restart faden-agent`, or `systemctl --user restart faden-agent`).

## Clusters

**The cluster fails to start with `Address already in use`.**
Cause: something on a machine holds one of the cluster's ports, often another Redis or another Ray.
What to do: free the port (see [Network and ports](network.md#ports-a-gpu-machine-must-have-free)), then select
**Try again** on the cluster's page.

**The cluster says it is waiting for the cause to change.**
Cause: a failure that comes out the same on every attempt, most often a runtime image on the server that is not
the build the cluster needs. The cluster stops retrying on a loop and checks again about once an hour.
What to do: `faden runtime-images --check` shows what the server holds; `faden runtime-images` fetches what is
missing. Then select **Try again**.

**The runtime image copy to a machine was interrupted.**
What to do: nothing. It resumes where it stopped, from the server or from a machine of the cluster that has it,
as long as the machine has room on disk for a copy of the image; otherwise it starts again.

**The cluster says it is restarting itself.**
Cause: Ray stopped on the head, or a machine dropped out. Fadenstack repairs it.
What to do: nothing, unless it reads **Failed** after three attempts. See [Failures and logs](failures.md).

**A cluster cannot be deleted because its machines are switched off.**
What to do: select **Delete** anyway. With no machine reachable there is nothing to stop; the page says so and
deletes the cluster.

**A machine cannot be added to a cluster.**
What to do: **Add machines** shows the reason under each machine; [Clusters](clusters.md#add-machines-to-a-running-cluster)
says what each means.

**A drain does not finish.**
Cause: a copy's replacement did not start on the other machine. After 30 minutes the drain says so.
What to do: open the deployment and read its errors, or select **Stop drain**.

## Models

**A deployment sits on "Copying the model" or "Downloading … to the Fadenstack server".**
Cause: the server is still downloading the model, or the download failed.
What to do: watch or retry the download under **Model marketplace → On this server**. A gated model needs a Hugging
Face token from an account that accepted its licence (see [Models](../administer/models.md)).

**A deployment reaches "Starting" and then fails.**
Cause: the engine could not start the model; the message carries its own reason. Most often the model does not fit
in the GPU's memory, or needs a number format the card does not support.
What to do: change the engine settings or the share of each accelerator, as described in
[Deployments](../administer/deployments.md).

**A deployment serves fewer copies than asked for.**
Cause: "cannot start: each copy needs …" means the cluster has no room for more; anything else is the engine's
reason for the copy it could not start.
What to do: scale down, or [add a machine to the cluster](clusters.md#add-machines-to-a-running-cluster).

**Replies fail with "The model did not start its reply within … s".**
Cause: the model is busy; its queue is full, and a new request waited too long.
What to do: give the model more room: more copies, or more GPU memory per copy (see
[Deployments](../administer/deployments.md)).

**A vLLM already running on a machine cannot be routed.**
What to do: [Existing vLLM servers](existing-vllm.md#what-cannot-be-routed) lists the reasons.
