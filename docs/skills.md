# Skills

A skill teaches the assistant how to do one kind of task the way your team
does it: a review checklist, a report format, the steps of an incident
write-up. It is a set of instructions that goes into a chat when it is needed.

- **MCP** is what the assistant can do (tools).
- **RAG** is what it knows (documents).
- **Skills** are how it should work (instructions).

Skills are an optional module (`faden module enable skills`, or
**Modules** in the admin console).

---

## Using a skill in a chat

- Type **`$`** and the skill's name (`$incident-review`). The input suggests
  matching skills as you type; pick one with Enter or Tab.
- Or press the **skills button** next to the input and tick the skills you
  want.

![Typing $test lists the matching skills](images/chat/04-skill-picker.png)

A picked skill shows as a chip above the input and is used for every message
in that chat until you take it off. Other skills come in on their own when
they are set to (see [When a skill is used](#when-a-skill-is-used)).

`$name` works from any client of the API too: the gateway reads it from the
message. A client can also send `"skills": ["incident-review"]` with a chat
request; the field is taken off before the model sees the request.

## Creating a skill from a chat

Type **`/skill`** in a chat (optionally followed by what the skill should do),
or choose **Create a skill from this chat** in the skills menu.

1. The chat's model reads the conversation and drafts a skill: a name, a
   description, and instructions.
2. You read the draft and change what is needed.
3. **Save skill** stores it. Nothing is saved before this.

The model only proposes. If it could save skills on its own, text in a
document or web page could talk it into creating one, and that skill would
come back into every later chat.

The model is asked to leave out names, credentials and other details of the
conversation. Check the draft for them anyway.

New skills are **private**: only you (and administrators) see them until you
share them.

## Your skills page

**Skills** in the chat's user menu (or `/skills`) lists the skills you can
see: yours, the ones shared with you, and the organization's. Each skill has:

| Tab | What it holds |
|---|---|
| Instructions | Description and instructions; saving adds a version |
| Settings | Title, tags, when it is used, triggers, tool lists, knowledge sources |
| Sharing | Who sees it (owner only) |
| Versions | Every saved version: publish one, roll back, see what changed |

Administrators see every skill under **Extensions → Skills Hub**.

## Who sees a skill

| Visibility | Who can see and use it |
|---|---|
| Private | The owner, the people and groups it is shared with, and administrators |
| Organization | Everyone in the tenant |
| Everyone | Every tenant on the installation (administrators only) |

Sharing gives a person or a group one of two levels:

| Access | Allows |
|---|---|
| Can use | Seeing the published version and using it in chats |
| Can edit | Also saving versions and publishing them |

Only the owner (or an administrator) shares a skill, changes who sees it,
archives or deletes it. People who can only use a skill see its published
version, not unpublished drafts.

The same rules decide what reaches a chat: the gateway resolves skills among
the ones the user can see, so a private skill never reaches anyone else's
chat. When a user is removed from a group, the skills shared with that group
stop reaching their chats within a minute.

## When a skill is used

| Setting | The skill comes into a chat |
|---|---|
| When picked | Only when picked (`$name` or the skills menu) |
| When it matches | When a message contains one of its trigger keywords (as whole words), or the chat has a file of one of its trigger types (its own files and its project's), and whenever it is picked |
| Always | In every chat of everyone who sees it. A private one: only its owner's chats, never the chats of people it is shared with |

A skill the whole organization sees can be **Always** only if an
administrator sets it up: it goes into everyone's chats.

At most three skills come in on their own per message, ranked by priority
and how well they match; picked skills come on top of that, up to five.

## Versions

Every save is a new version; no version is ever changed or deleted. A skill
points at two of them:

- the **latest** version, which the editor shows;
- the **published** version, which chats get.

**Save** adds a version without changing what chats get. **Save & publish**,
or **Publish** on a version, moves the pointer. Publishing an older version
rolls back, including its tool lists and triggers, which are part of each
version.

Each version has a SHA-256 of its content. The audit log records the skill,
version and hash for every request that used a skill, so what the model was
told can always be looked up. Saving the same content again adds no version.

Two editors saving at once: the second save is refused with "This skill
changed since you opened it" instead of silently replacing the first.

## The format

Skills use the [Agent Skills](https://agentskills.io/specification) format,
which Claude Code, Codex and Open WebUI read too. A skill is a folder:

```
incident-review/
├── SKILL.md        name, description (+ license, compatibility, metadata)
│                   and the instructions
├── faden.yaml    Fadenstack's settings: title, tags, activation,
│                   trigger_rules, allowed/preferred/forbidden tools,
│                   knowledge_sources
├── references/     further documents
├── assets/         templates, data files
└── scripts/        kept, never run
```

**Export** gives the zipped folder, or the `SKILL.md` alone. **Import** takes
either; a skill from anywhere else imports as it is, and an older Fadenstack
`.md` (with its settings in the frontmatter) is read too.

- `SKILL.md`'s own `allowed-tools` (for example `Bash(git:*) Read`) is kept
  for export but not applied: Fadenstack's tool lists are in `faden.yaml`.
- Nothing in Fadenstack runs a skill's scripts. They are kept so the skill can
  be exported to agents that do, and the editor marks them.
- The chat gets the `SKILL.md` instructions, and the skill's other files
  when it needs them. A model that calls tools sees the files listed with the
  skill and reads one with `read_skill_file` (only files of the skills in
  that chat, in parts of 20 000 characters). A model that does not call tools
  gets the small text files inline, up to 12 000 characters in all, and the
  rest named as not included. Scripts are listed, never inlined; binary files
  cannot be read.

## Permissions

| Permission | Allows | Given to |
|---|---|---|
| `skills:read` | Seeing and using the skills visible to you | default_user, viewer, operator, admin |
| `skills:create` | Creating and importing skills of your own | default_user, operator, admin |
| `skills:share` | Sharing your skills with people and groups | default_user, operator, admin |
| `skills:publish` | Making a skill visible to the whole organization | admin |
| `skills:manage` | Administering every skill; the Skills Hub | admin |

Superusers have all of them. Built-in roles get these on the next backend
start; give them to custom roles under **Roles**.

## Upgrading from 0.3

Migration `002_ownership_versions` runs with the skills migrator. Existing
skills keep behaving as they did:

- The organization owns them, and the whole tenant sees them.
- Skills that were `global` or `tenant` scope came into every chat, so they
  become **Always**. `workspace`, `assistant` and `user` ones came in only
  when they matched, so they become **When it matches**.
- Published (and archived) skills get their current version as the published
  one; drafts have none.
- The tool lists, knowledge sources and triggers are copied into every
  version; the latest version takes the skill's current values.

The old limit of five versions per skill (older ones were deleted) is gone,
and the limit of 25 published skills now counts only the ones the whole
organization sees; people's private skills do not count against it.

## Limits

| Setting (`FADEN_SKILLS_…`) | Default | |
|---|---|---|
| `MAX_PUBLISHED_SKILLS_PER_TENANT` | 25 | Published skills the whole organization sees |
| `MAX_SKILLS_PER_USER` | 200 | Skills one person owns |
| `MAX_EXPLICIT_SKILLS` | 5 | Picked skills per message |
| `MAX_BODY_CHARS` | 100 000 | Instructions of one version |
| `MAX_FILES_PER_SKILL` | 64 | Files in a skill's folder |
| `MAX_FILE_BYTES` | 512 KiB | One file |
| `MAX_BUNDLE_BYTES` | 4 MiB | All files of a version |
