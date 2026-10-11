# Contributing to Fadenstack

Thank you for helping. This page says how to ask, report and propose changes, and what a pull request needs.

Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md).

## Questions

Read [Help](https://help.fadenstack.com) first: it covers installing, operating and using Fadenstack. If it does
not answer your question, open an issue with the **Question** template.

## Reporting a bug

1. Search the [issues](https://github.com/fadenstack/fadenstack/issues) to see whether it is known. If it is, add
   what you know there instead of opening a new one.
2. Open an issue with the **Bug report** template. Say which release you run (`faden version`), how it is
   installed, what you did, what you expected and what happened instead. Logs help: `faden logs <service>`.
3. Leave out passwords, tokens, API keys, `.env` files and personal data. Shorten logs to the part that matters.

A **security problem** is not reported in an issue: see [SECURITY.md](SECURITY.md).

## Proposing a change or a feature

Open an issue with the **Feature request** template before you write code, and describe the problem you want to
solve rather than only the solution. We say there whether it fits and how we would like it done, so your work is
not wasted. Small fixes (a typo, a broken link) can go straight to a pull request.

## Pull requests

- **Link the issue** it resolves ("Fixes #123"). A bug fix needs an issue that describes the bug; a feature needs
  an issue we agreed on.
- **One change per pull request**, as small as it can be.
- **Tests:** add or change tests that show the change works, and run the tests of every part you touched (below).
- **Help:** when you change what someone sees or does, change the page in `docs/help/pages/`. The German page in
  `docs/help/pages-de/` is welcome too; if you can't write it, say so and we will.
- **Console text** lives in `backend/i18n/<language>/` in five languages (en, de, es, fr, zh). Add a new string to
  all five; if you don't speak them all, put the English text in and say so in the pull request.
- **Commit messages** say what the change does, in a plain sentence ("Chat: a stopped reply keeps its pictures"),
  with the reason in the body when it is not obvious.
- **No confidential information**: nothing about your company, clients or systems that you don't want public.

### Contributor licence agreement

Before we can merge your first pull request, you sign the
[contributor licence agreement](CONTRIBUTOR_LICENSE_AGREEMENT.md) once. When you open the pull request, the CLA
Assistant bot comments on it with a link; you read the agreement there and sign with your GitHub account. It is
three sentences long: you let Emagin8 UG (haftungsbeschränkt), Fadenstack's steward, license your contribution on
any terms, and you give it without warranty. If your employer or a client has rights in your work, make sure you
may sign.

## Setting up for development

You need Linux, macOS or Windows with Docker, Python 3.12 or newer with [uv](https://docs.astral.sh/uv/), and Node.js 20
or newer.

```bash
git clone https://github.com/fadenstack/fadenstack.git
cd fadenstack
pipx install ./cli
faden dev doctor   # checks the tools
faden dev init     # writes the development settings
faden dev up       # runs the services from this checkout against a local stack
```

The services listen on 127.0.0.1 only, as development mode signs anyone in as the administrator. Each part's
README describes running it on its own.

## Running the tests

| Part | Commands (in its folder) |
| --- | --- |
| `gateway` | `uv sync --locked`, then `uv run ruff check .` and `uv run pytest` |
| `backend` | `uv sync --locked`, then `uv run pytest`; needs a PostgreSQL server (see `backend/README.md`) |
| `pii`, `mcp`, `skills` | `uv sync --locked`, then `uv run pytest` |
| `cli`, `agent` | `uv sync`, then `uv run pytest` |
| `frontend` | `npm ci`, then `npm run typecheck` and `npm test` |

## Licence

Fadenstack is licensed under the [Apache License 2.0](LICENSE). Your contribution is licensed under it too, and
the contributor licence agreement above adds the steward's right to license it on other terms.
