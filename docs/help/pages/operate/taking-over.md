# Taking over clusters

If the server is rebuilt without its database, or restored from a backup older than its clusters, the GPU machines
keep running the clusters and models the old server started. The new server can take such a cluster over as it
runs: its machines, and each model it serves. No model restarts, during the takeover or afterwards.

??? info "Why not create the cluster again?"
    Creating the cluster again instead would restart every model, and while the old copies still hold their GPUs the
    new ones often do not fit.

## Before you start

- **The machines are approved on this server.** Their agents still hold credentials for the old server. On each
  machine, run the install line from this server's **Machines → Add a machine** and approve the request.
  Reinstalling the agent does not touch the running cluster or its models.
- **The machines run an up-to-date agent.** Running the install line takes care of that.

## Take over a cluster

1. Open **Clusters** and find the card **Running on your machines, not managed here**.
2. Select **Take over…** on the cluster.
3. Enter a **Cluster name**.
4. For each model, enter the name clients ask for at the gateway (*model*: **name at the gateway**). The
   suggestion is the model's own name in lower case.
5. Select **Take over**.

It takes a few seconds. The cluster then opens with its models running, and the gateway offers each model under
the name you gave it.

Before anything is recorded, the cluster is asked to confirm that what this server would deploy for each model is
exactly what runs. If anything differs, nothing is recorded and the dialog shows the difference.

??? info "More about the card and the names"
    The card lists every cluster a machine runs that this server does not manage: its machines, and each model with
    its copies and GPUs per copy.

    The old server kept the names at the gateway in its own database, not on the machines, so they have to be
    entered again.

## What is carried over, and what is not

| What | Carried over |
|---|---|
| The cluster's machines, the network it runs on, its token | Yes |
| Each model Fadenstack deployed on it, with its settings and copies | Yes |
| **The names** shown in the console and used at the gateway | No. You enter them again when you take over |
| **History**: logs, metrics and past requests | No. They stay with the old server |
| **Applications Fadenstack did not deploy** | No. They keep running and are listed ("Also running, not deployed by Fadenstack and left as they are"), but are not managed |

## When a cluster cannot be taken over

The card says why, and **Take over…** stays disabled:

| Message | What to do |
|---|---|
| *Machine* is in the cluster but not in this fleet | Run the install line from this server on that machine and approve it |
| … already belong to a cluster this server manages | That machine is in a cluster here. Remove it from that cluster first |
| This server already has the deployment of … | The model is already managed here; there is nothing to take over |
| *Machine* runs the cluster runtime but could not say what it serves | Its agent is too old, or did not answer: run the install line on it again |

If the check in the last step finds a difference, the dialog shows the setting and both values, for example
`llm_configs.0.engine_kwargs.max_model_len: running 8192, would deploy 32768`. Nothing was recorded. The model was deployed by a version of
Fadenstack that set it up differently. Either deploy it again from this server (it restarts), or leave it running
unmanaged.

## Related

- [Backups](backups.md): restoring a server, and why a backup older than a cluster does not know it.
- [Clusters](clusters.md#models-running-without-a-deployment): a model left running after its deployment was
  deleted, on a cluster this server does manage.
