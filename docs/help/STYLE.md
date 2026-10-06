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
- **No screenshots yet.** They will be taken from a demo installation; the lab's do not belong here.

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
