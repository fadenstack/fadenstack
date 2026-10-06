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

In `pages/images/<section>/`. Taken from a running install with every lab name, address and person replaced
by the neutral examples of STYLE.md before the picture is taken; never a raw capture of a real install.
