# Serve models on a Mac

A Mac with Apple silicon can serve a model on its own: one Mac, one model at a time, no cluster. This page covers
setting one up from the console, what the Mac's status card tells you, how much memory a model may use and what
a Mac does not do.

## Before you start

- The Mac has the Fadenstack agent installed and shows as a machine under **AI Infrastructure → Machines**
  (see [Connect machines](../operate/machines.md)). The agent must be version 0.3.0 or later.
- It has the memory for the model. A Mac gives the model's engine about four fifths of its memory (the rest stays
  with macOS), less a reserve: 3 GiB on a 36 GiB Mac, 4 GiB on 64 GiB, 6 GiB on 128 GiB.

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

## Memory

When you pick the Mac in the hosting dialog, it shows what the GPU can use, what is kept for the system, the model's
weights, the room for conversations, and the share of memory the model is given. A figure marked *estimated* comes
from the Mac's total memory until the runtime has reported the real one.

A model that needs more than the Mac can give is refused, with the memory it needs and the memory available. A
smaller context or fewer parallel conversations can make it fit; the dialog proposes both.

## What a Mac does not do

- One model at a time. Delete the running deployment before hosting another.
- No scaling, no copies on other machines, no draining, and no network plan: those belong to clusters.
- GPTQ models are refused; AWQ models and MLX models work. A quantized KV cache, a limit on thinking tokens, prefix
  caching, speculative decoding and models that run code from their repository are not offered.
- Only the tool-call parser `hermes` and the reasoning parser `qwen3` are offered. Others appear once they have been tested.
- A Mac cannot be a member of a cluster while it serves on its own, and the other way round.
