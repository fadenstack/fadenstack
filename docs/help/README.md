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

## Preview and build

`scripts/build.py` puts the site together with the Fadenstack theme (`docs/theme/`, shared with the Developers
site) and builds it, for one of two places:

```sh
python docs/help/scripts/build.py --target console --out build/help --build   # what the console serves at /help
python docs/help/scripts/build.py --target public  --out build/help --build   # help.fadenstack.com (the docs hub builds it)
cd build/help/en && zensical serve                                             # http://localhost:8000 (build/help/de: German)
```

The **console** copy is built into the frontend image (`frontend/Dockerfile`, build context `docs`), so every
install serves the help of its own release at `/help`, offline too; the console's page help links to it
(`doc` in `frontend/app/lib/pageHelp.ts`, held to existing pages by `pageHelp.test.ts`). The **public** copy has
the site's address and links to the website and the Developers site; the docs hub (`fadenstack/docs`) builds and
publishes it from the release tag. Both use the nav in `zensical.toml` here and the settings in `site.toml`.

`cd docs/help && zensical serve` still works for a quick look at the pages in the plain theme.

## Languages

English in `pages/`, German in `pages-de/`: the same files, the same order, German navigation titles in
`nav-de.toml`. The site has English at its root and German under `de/` (`/help/de` in the console), with a
language switch in the top bar; the console links to the German pages when it is in German. A page not
translated yet is the English one with a note, so the German build never fails for want of one -- but translate
it in the same change: STYLE.md says how, and `scripts/ui_label.py` finds what the German console calls a button.
The German pages show the German screenshots (`pages/images/de/`) where they exist, otherwise the English ones.

## Screenshots

In `pages/images/<section>/` (German: `pages/images/de/<section>/`). Every picture is taken from a running install
with `scripts/shots.mjs`, which replaces every lab name, address and person by the neutral examples of STYLE.md
before the picture is taken. `HELP_SHOTS_LANG=de` takes the German ones; `HELP_SHOTS_OUT` puts a trial run
elsewhere. Look at every picture before it goes in (text recognition helps): no lab names, private addresses, test
data or errors the page does not explain.

Taken by hand and English only: the browser agent's and the Office add-ins' pictures (their own repositories) and
the maintenance page (only shown during an update). The cluster's page and the Getting started card need an
install without a failed deployment, and with first steps still open.

### Pictures still to take

They need a state the test install does not have, or a dialog the script does not open yet:

- **Use**: the sign-in page; an approval card ("… wants to run", Approve and Decline) and a **Not run** line; the
  New skill dialog (`/skill`), the `$` list and a picked skill's chip; an attached file's chip and an answer naming
  its document; My Profile's API Token section and the avatar menu; the browser agent's first run, its "cannot read"
  bar and a marked passage; Word's **Agent** group, the add-in's sign-in pane, **More → Sign out** and **Undo agent
  change**; **Check content** with a result (`use/checking-content.md`) and the public check page.
- **Administer**: the Help menu opened; Settings → Modules; the dashboard in Customize mode; Add Provider's Pricing
  step and a remote provider's Edit dialog; a deployment's Offer in chat and reply-wait dialogs and its Logs
  section without errors; the marketplace's Search tab, a model's details and the Delete dialog on On this server;
  an agent's Apps tab with a held-back tool, and its Activity tab at Signed-in devices; an MCP server's page and
  the German Edit dialog; the skill editor and a skill's Settings tab; the PII dashboard; one request opened under
  Cost Observability → Requests; the **Provenance** card with **Marking engine** and **Public check page**, and its
  **Disable Provenance?** dialog (`administer/content-marking.md`).
- **Operate**: a machine waiting for approval, Skip the approval step opened, the "Agent version available" chip;
  Create a cluster step 3; a cluster starting, one needing attention and one failed with **Try again**; a selected
  machine's Drain and Remove from cluster, a drain in progress; switching the runtime; the cards Where the machines
  get models, Models running here without a deployment, Health checks, and both encryption cards before
  encryption; Add machines with a refused machine; deleting a machine; the takeover card and dialog; routing a found
  vLLM and a routed one; Root Mode, Save and replace, Networks and Stacks.
