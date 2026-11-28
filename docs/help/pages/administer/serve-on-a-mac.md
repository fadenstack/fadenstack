# Serve models on a Mac (experimental)

!!! note "Experimental"
    Serving on a Mac is new in this release. It works, but expect changes. Report problems.

A Mac with Apple silicon can serve a model on its own: one Mac, one model at a time, no cluster. This page covers
setting one up from the console, what the Mac's status card tells you, how much memory a model may use and what
a Mac does not do.

## Before you start

- The Mac has the Fadenstack agent installed and shows as a machine under **AI Infrastructure → Machines**
  (see [Connect machines](../operate/machines.md)). The agent must be version 0.3.0 or later.
- It has the memory for the model. A Mac gives the model's engine about four fifths of its memory (the rest stays
  with macOS), less a reserve: 3 GiB on a 36 GiB Mac, 4 GiB on 64 GiB, 6 GiB on 128 GiB.
  A Mac needs at least 24 GB of memory to host a model.

## Set the Mac up

1. Open the Mac under **AI Infrastructure → Machines**.
2. Select **Serve models on this Mac**. This creates an environment for this Mac alone and opens it. Doing it again
   opens the same environment.
3. Deploy a model as usual (see [Deployments](deployments.md)). The first deployment fetches the runtime to the Mac,
   which takes a few minutes.

Loading a model takes longer than starting a container: a small model needs a minute or two, and a large one
several minutes. The deployment shows **Loading the model** until it is ready, and a model that does not become
ready in time is reported as failed.

## The Mac status card

| Line | What it means | What to do |
|---|---|---|
| Native runtime | Installed and ready, or fetched with the first model. | Nothing. |
| Sandbox | On: the engine cannot read the agent's own files. | Keep it on. Turn it off only to test. |
| Application firewall | When on, it can stop the gateway reaching the model's port. | In System Settings, Network, Firewall, allow incoming connections for `faden-agent`. |
| FileVault | When on, the Mac waits for a login after a power cut. | Restart with `sudo fdesetup authrestart` so it comes back unattended, or leave FileVault off. |

## Memory {#memory-share}

When you pick the Mac in the hosting dialog, it shows what the GPU can use, what is kept for the system, the model's
weights, the room for conversations, and the share of memory the model is given. A figure marked *estimated* comes
from the Mac's total memory until the runtime has reported the real one.

A model that needs more than the Mac can give is refused, with the memory it needs and the memory available. A
smaller context or fewer parallel conversations can make it fit; the dialog proposes both.

## A Mac mini as a dedicated node

The Fadenstack server (console, gateway, monitoring) runs on a separate machine. The Mac joins it over the network
as a node, and the gateway reaches the Mac's model by its address. A Mac mini or a Mac Studio is the typical node:
it has no battery and no lid, and it can run without a display once it is set up.

Set the Mac up once with a display and keyboard, join it to the server, turn on Remote Login (see below), and from
then on it can run headless.

### Memory

A Mac needs at least 24 GB of unified memory to host a model. The base Mac mini has 16 GB and cannot; the console
then says it needs at least 24 GB of memory. Choose 24 GB or more when you buy. How much of that memory the model
may use is described under [Memory](#memory-share) below.

### FileVault and unattended restarts

With FileVault on, a Mac that restarts after a power cut or an automatic macOS update stops at the unlock screen.
The disk is locked, so the agent does not start and the machine stays offline until someone unlocks it. You have
three choices:

| Choice | How | Trade-off |
|---|---|---|
| Unlock over SSH | Turn on **Remote Login** (System Settings, General, Sharing). After a restart you connect over the network and type the password; the Mac then finishes starting. Needs an Apple silicon Mac on macOS 26 or later and a network connection. | Someone must hold the password and be available. The unlock is by password: keys do not work at that stage. It needs no keyboard or display. |
| `sudo fdesetup authrestart` | For restarts you start yourself. The Mac restarts and skips the unlock once. | Covers planned restarts only, not a power cut or a crash. FileVault protection is reduced for that one restart. It may not work on every Mac. |
| FileVault off | The Mac starts straight into the agent after any restart. | The disk is not encrypted with your password. Anyone who takes the Mac can read it, including the agent's credential and the downloaded models. Choose this only if theft of the Mac is not in your threat model. |

Fadenstack never turns FileVault off for you. `faden-agent status` prints one line when FileVault is on and Remote
Login is off, because then a power cut needs someone at the Mac. A small UPS shortens the exposure to power cuts.

!!! note
    An automatic macOS install may restart the Mac. If FileVault is on and you cannot unlock it, turn off
    **Install macOS updates** under System Settings, General, Software Update, or install updates at a time when
    you can unlock the Mac.

### Power settings

Check these in System Settings before the Mac goes without a display:

- **Energy, Start up when power is connected: Always.** On older macOS the option is called **Start up
  automatically after a power failure**. Without it, a desktop Mac stays off after a power cut.
- **Wake for network access: on.**
- **General, Sharing, Remote Login: on**, while you are still at the Mac.
- **Software Update, Install macOS updates:** decide it on purpose (see the note above).
- Idle sleep: nothing to do. While it runs, the agent keeps the Mac from sleeping when idle, and `faden-agent
  status` shows it. The display may still sleep.
- **High Power Mode** (Energy, Energy Mode, on the power adapter) exists only on some models, such as a Mac mini
  with a Pro chip. It lets the fans run faster for long, heavy work, and they get louder. Fadenstack does not
  change it; the decision is yours.

### The model store

Models are stored on the Mac's internal SSD, in `/var/db/fadenstack/agent/models`. Check the free space first: a
base Mac mini has a 256 GB SSD, and a large model can take tens of GB.

The agent uses another folder, set with `FADEN_NODE_AGENT_MODEL_STORE` in `/Library/Fadenstack/agent.env`, only
if it is an absolute path to an existing folder that is owned by `_fadenagent` and has mode 0700. Folders in a home
folder, on protected system paths, in the agent's own folders and symlinks are refused. A store that fails the check
is not used: the start prints a warning and the agent uses the internal SSD, and models in the old store are not
found there.

macOS can ignore ownership on an external drive, so every file looks owned by the current user. Then the store is
refused. To use an external SSD, an administrator can do this, with care, because it changes how the whole drive is
treated:

1. Format the drive as APFS if it is not. This erases it.
2. In Finder, select the drive, choose **File, Get Info**, open **Sharing & Permissions**, and make sure **Ignore
   ownership on this volume** is not ticked.
3. In Terminal, make the folder and hand it to the agent:
   `sudo mkdir /Volumes/Models/faden && sudo chown _fadenagent /Volumes/Models/faden && sudo chmod 700 /Volumes/Models/faden`
   (use your own volume name).
4. Put that path in `agent.env` as `FADEN_NODE_AGENT_MODEL_STORE`, then restart the agent with `sudo faden-agent
   start` and read what it prints.

This has not been tested on every setup. In particular, check after a restart that the drive is mounted when the
agent starts; if the folder is missing, the agent uses the internal SSD. If in doubt, use the internal SSD.

### The network

- Prefer **Ethernet**. A Mac on Wi-Fi can start before the network is up.
- The Mac tells the server its address when the agent starts, and a model's address is fixed when it is deployed.
  Give the Mac a **DHCP reservation** on your router so the address does not change, or set the address by hand with
  `FADEN_NODE_AGENT_ADVERTISE_HOST` in `agent.env`. Set it by hand too when the Mac has several networks and the
  server reaches it over a particular one.
- The macOS **firewall** can stop the server reaching the Mac. `faden-agent status` says so when the agent is not
  allowed, and prints the exact command, which an administrator runs once. A new build of the agent has a new path,
  so it may need the command again.
- **Ports:** allow the model ports **8100-8999** from the server, and **9468** for metrics. Metrics are plain HTTP
  without a login, as on Linux, so make 9468 reachable only from the server's network.

Example for a Mac that the server reaches at `192.0.2.10`:

```
FADEN_NODE_AGENT_ADVERTISE_HOST=192.0.2.10
```

### Several Macs

One model cannot yet span several Macs. A model runs on one Mac. To use more Macs, host a model on each.

## What a Mac does not do

- One model at a time, on one Mac. Delete the running deployment before hosting another.
- No scaling, no copies on other machines, no draining, and no network plan: those belong to clusters.
- GPTQ models are refused; AWQ models and MLX models work. A quantized KV cache, a limit on thinking tokens, prefix
  caching, speculative decoding and models that run code from their repository are not offered.
- Only the tool-call parser `hermes` and the reasoning parser `qwen3` are offered. Others appear once they have been tested.
- A Mac cannot be a member of a cluster while it serves on its own, and the other way round.
