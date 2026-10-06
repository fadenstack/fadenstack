# Help

The pages of `help.fadenstack.com`: how to use Fadenstack, set it up for an organisation, and run it. They live
here, next to the code they describe, so a change and its help go in the same commit, and the help of a release
is `docs/help/` at its tag.

| Section | Folder | For |
|---|---|---|
| **Use** | `pages/use/` | everyone in the organisation: the chat, skills, tools, knowledge, the browser agent, the Office add-ins, their account and data |
| **Administer** | `pages/administer/` | IT administrators in the console: people and roles, models and deployments, providers, MCP servers, skills, agents, policies, usage |
| **Operate** | `pages/operate/` | whoever runs the server and the GPU machines: requirements, installing, upgrading, backups, HTTPS, network, machines, clusters, failures |

## Write

Read [STYLE.md](STYLE.md) first: who reads each section, what a public page may and may not say, how a page
reads. A new page goes into the nav in `zensical.toml`, or it does not appear. Check before you commit:

```sh
python docs/help/scripts/check.py      # the docs hub's scrub, and the links between pages
```

It needs the docs hub (`fadenstack/docs`) checked out next to this repo, or `FADEN_DOCS_HUB` pointing at it.

## Preview

```sh
cd docs/help
zensical serve                          # plain theme: http://localhost:8000
```

The branded site, with the Fadenstack theme and the links to the website and the Developers site, is built by
the docs hub: `python scripts/assemble.py help` there takes `docs/help/pages/` and this nav.

## Screenshots

In `pages/images/<section>/`. Taken from a running install with `scripts/shots.mjs`, which replaces every lab
name, address and person by the neutral examples of STYLE.md before the picture is taken. An older screenshot
may be used only when it shows none of them (checked by eye; text recognition helps on many pictures).

### Pictures still to take

With `scripts/shots.mjs` (add the screen to its list), or on a demo install where a screen needs a state:

- **Use**: the tools panel with **Tool mode** and a realistic service, an approval card, a reply being written,
  a failed reply with **Retry**, an attached file, a knowledge answer naming its source; the Office ribbon's
  **Agent** button, its sign-in and **Context** menu, a changed Excel sheet; an Outlook draft with neutral people;
  the chat list and the profile as a regular user, without test chats.
- **Administer**: the Create User, Edit Roles and Developer mode dialogs, Groups; a skill's Versions and Sharing;
  an agent's Access, Apps, Activity and Versions tabs without test agents; Add Provider and Set price; a model
  card, **On this server**, the token dialog and the hosting dialog's steps; a deployment's logs; Add rule, Tool
  classes and Decisions; the request view and Pricing of the cost dashboard, without load figures.
- **Operate**: **Add a machine** with the install line and a machine waiting for approval; the Create a cluster
  steps; **Add machines** with a machine to pick and one refused; a selected machine with **Drain** and
  **Remove from cluster**; the Runtime, Model traffic and Health checks cards; a cluster starting, failed with
  **Try again**; routing a found vLLM; the container screens (images, create, a container, edit).
