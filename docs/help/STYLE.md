# Writing Help pages

Help is for the people who use, administer and run Fadenstack. It is public (`help.fadenstack.com`) and, later,
part of every install (`/help`). These rules keep it useful to them and keep Fadenstack's internals private.

## Who reads which section

| Section | Reader | They want to |
|---|---|---|
| **Use** (`pages/use/`) | anyone in the organisation, not technical | get work done in the chat, the browser and Office, and know what happens to their data |
| **Administer** (`pages/administer/`) | the IT administrator who sets Fadenstack up for the organisation, in the console | add people, offer models, connect tools, set rules, see usage |
| **Operate** (`pages/operate/`) | the person who runs the server and the GPU machines, on a command line | install, upgrade, secure, connect machines, keep it running, fix it |

Write for that reader. A Use page never sends someone to a terminal; an Operate page may assume a shell, SSH
and Docker, but not knowledge of Ray or vLLM internals.

## What a page may say, and what it may not

May: what a part does for the reader and the result they get; how to install, configure, use, upgrade, back
up and troubleshoot it; the settings, buttons, commands, ports, files and error messages they meet; limits and
guarantees; what leaves the network, what is encrypted, who can see what.

Must not:
- how Fadenstack decides: routing, ranking, selection, thresholds, scoring, retries, prompts sent to models;
- tuning and evidence: benchmark numbers, measured timings, load tests, comparisons with other products;
- design history: what was tried, why it was built this way, bugs found and fixed;
- the lab: host names, private addresses, machine names, people's names, dates of test runs;
- maintainer processes: releasing, signing, CI, registries;
- how Enterprise licences are checked or what the Enterprise layer hooks into;
- plans and editions not announced.

Describe the **contract** (what to do, what you get, the limits), never the **mechanism**. If a page only makes
sense with the mechanism, it does not belong here.

`python docs/help/scripts/check.py` fails on the common slips (private addresses, lab names, internal wording, timings,
e-mail addresses, secrets) and on links to pages that do not exist.

## How a page reads

- **Start with what the reader gets**, in one or two sentences, before any step.
- **Steps are numbered and start with a verb.** One action per step. Say where it is: "In the console, open
  **Clusters** and select **Add machines**."
- **Name things exactly as the console shows them**, in bold: pages, buttons, fields, tabs. The English labels
  are in `backend/i18n/en/common.json` and `tour.json`. Commands, paths, settings and values go in `code`.
- **Short sentences, plain words.** "Copy", not "replica"; "machine", not "node", except where the console
  itself says otherwise. Explain a necessary technical term the first time it appears.
- **Say what can go wrong** where it can, and what the reader sees then: the message, and what to do.
- **No marketing.** No "seamless", "powerful", "simply". Do not promise what the product does not do.
- **Headings are sentence case.** One `#` title per page; `##` for sections.
- **Admonitions** for what must not be missed: `!!! warning` for data loss or downtime, `!!! note` for limits.
  Use them sparingly.
- **Planned features** carry `<span class="fs-planned">Planned</span>` and say only what they will do.
- **Links** are relative to the page, to `.md` files: `[Clusters](../operate/clusters.md)`. Link a term to its
  page the first time it matters, not every time.
- **Examples** use `https://ai.example.internal` for the server, `gpu-01`, `gpu-02` for machines,
  `192.0.2.x` for addresses, `alex@example.com` for people.
- **Screenshots** show a screen the reader is about to use, or what they should see when it worked: one to
  three on a page, next to the step they belong to, never instead of the text. They live in
  `pages/images/<section>/` and are taken with `scripts/shots.mjs`, which swaps every name, address and person
  for the neutral examples above before the picture is taken. Look at every picture before you add it: no
  real machine, cluster or person names, no private addresses, no error that the page does not explain.
  Alt text says what the picture shows: `![The cluster page with two machines](../images/operate/cluster.png)`.

## Questions for the front page

The front page answers questions as readers ask them (`assets/javascripts/fs-ask.js` in the theme, no model and no
server). It knows each page by the questions in `questions.toml` and `questions-de.toml`: when you add a page or a
section that answers something new, add the question there, in the reader's words, with the section's anchor.
`scripts/build.py` fails on a page or an anchor that does not exist. A question that finds the wrong page is fixed
there, by adding the question the reader asked.

## Page template

```markdown
# Title in sentence case

One or two sentences: what this page lets the reader do, and what they get.

## Before you start

What they need: role, access, what must exist already. Leave out when there is nothing.

## Do the thing

1. Open **Page** and select **Button**.
2. ...

What they see when it worked.

## When it does not work

**The message they see.** What it means, and what to do.
```

## German pages (Deutsch)

The German Help is `pages-de/`: the same files as `pages/`, the same order, the same links. Its navigation
titles are in `nav-de.toml`. Everything above applies; in addition:

- **Address the reader as "Sie"**, as the German console does. Plain, short sentences; a verb at the start of
  a step ("Öffnen Sie **Cluster** und wählen Sie **Maschinen hinzufügen**.").
- **Name things exactly as the German console shows them**, in bold. Look a label up instead of translating
  it: `python docs/help/scripts/ui_label.py "Create User" --lang de` prints the German text of every key
  that holds that English text. Where the console has no German text for it, keep the English label.
- **Untranslated:** commands, code, file and folder names, paths, setting keys (`users.login_lifetime_s`),
  environment variables, role and permission names (`default_user`, `rag.search:write`), product names
  (Fadenstack, Ray, vLLM, NVIDIA), HTTP status codes, and messages quoted as the system shows them.
- **Pictures** are the English screenshots until German ones are taken: the paths stay as in `pages/`
  (`../images/...`), only the alt text is German.
- **Typography:** „…" for quotes, "z. B.", "d. h.", a space before units ("12 GB"), a decimal comma in prose
  ("1,5 GB"), " – " for a dash. Headings in sentence case, as in German anyway.
- **Words**, one German word for one thing, everywhere:

| English | Deutsch |
|---|---|
| chat, console | Chat, Konsole |
| superuser, administrator | Superuser, Administrator |
| user / account, role, group, permission | Benutzer / Konto, Rolle, Gruppe, Berechtigung |
| sign in, sign-in | anmelden, Anmeldung |
| API token | API-Token |
| knowledge base, collection, document | Wissensdatenbank, Sammlung, Dokument |
| model, deployment | Modell, Bereitstellung (the menu says **Deployments**: name it so) |
| cluster, machine, GPU machine | Cluster, Maschine, GPU-Maschine |
| runtime, provider, remote provider | Runtime, Provider, externer Provider |
| agent, add-in, skill, tool, MCP server | Agent, Add-in, Skill, Tool, MCP-Server |
| policy, personal data | Richtlinie, personenbezogene Daten |
| usage, costs | Nutzung, Kosten |
| install, upgrade (noun / verb), backup, restore | Installation, Upgrade / aktualisieren, Sicherung, Wiederherstellung |
| maintenance page, troubleshooting | Wartungsseite, Fehlerbehebung |
| request, reply | Anfrage, Antwort |
| copy (of a model) | Kopie |
