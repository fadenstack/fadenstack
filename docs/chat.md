# The chat

The chat is where everyone in the organization talks to the models
Fadenstack serves: `http://<server>/chat`. Users land in it after signing in;
administrators reach it from the console too.

The screenshots were taken on the development install on 2026-09-26.

---

## A conversation

![A chat found by searching, with its title in the bar](images/chat/01-title-and-search.png)

**New chat** starts a conversation. Type in the box at the bottom and press
Enter to send (Shift+Enter for a new line); the square button stops a reply
while it is being written.

| Control | What it does |
|---|---|
| Wrench | The chat's **Tools**: which MCP tools the model may use. They start off; see [MCP servers → In a chat](mcp-servers.md#in-a-chat) |
| Puzzle piece | The chat's **skills**; type `$` in the box for the same list. See [Skills](skills.md) |
| Paper clip | Attach a file to the message |
| Model | The model that answers |

Under a reply, the badge counts its tokens; hover it for the prompt,
completion and total. The ⓘ beside it names the model that answered.

---

## Titles

A chat is named from its first message: its first line, cut at a word, at
most 80 characters. Chats created through the API or a script without a
title are named the same way.

The title is in the bar above the conversation. The pencil beside it renames
the chat in place: Enter saves, Escape cancels.

![Renaming a chat](images/chat/02-rename.png)

Titles are stored encrypted, as the messages are: they are the start of what
you wrote. The admin console's list of chat sessions shows an encrypted title
as "Encrypted", never its text.

---

## The sidebar

The sidebar has **New chat**, the search and two sections:

- **Projects**: a folder per project, with its chats; the folder button next
  to the heading makes a new one. A project's settings
  hold a description and a **system prompt** that goes before every message
  in its chats: the context, a persona or rules for that piece of work.
- **Chats**: the rest, grouped by when they were last used: Today, Yesterday,
  Previous 7 days, Previous 30 days, Older.

The sidebar shows the 50 most recent chats and loads more as you scroll to
the end (**Show more**). A project shows 20 at first.

**Moving a chat into a project.** Drag it onto the project, or use the
**Move to project** button that appears when you point at it. Drag it back
onto the chats, or choose **No project**, to take it out. On a touch screen,
and in search results, a chat's actions (Move to project, Delete) are behind
its ⋮ button.

**Search** matches chat titles as you type and lists the results ("Results:
2"). It searches the titles, not what was said in the chats.

**Collapsing.** The arrow at the bottom folds the sidebar into a narrow rail:
the logo opens it again, **+** starts a chat, and the magnifier opens it with
the search ready.

![The collapsed sidebar](images/chat/03-collapsed-rail.png)

---

## Skills in a chat

Type `$` and part of a skill's name: the matching skills are listed as you
type. Enter or Tab picks one, and it is used for every message of this chat
until you take it off. `/skill` drafts a new skill from the conversation.

![Picking a skill with $](images/chat/04-skill-picker.png)

[Skills](skills.md) explains what a skill is, how it is picked on its own,
and how to share one.

---

## The top bar

From the right:

- **Your avatar**: your email, **Manage profile**, **Skills** (your own, the
  ones shared with you and the organization's, when the skills module is on)
  and **Log out**.
- **Sun or moon**: light or dark theme.
- **Language**: English, German, Spanish, French or Chinese.
- **Shield** (superusers only): the admin console.

---

## On a phone

<img src="images/chat/05-phone.png" alt="A chat on a phone" width="300">

Below 600 px the sidebar opens from the round **menu button** in a bottom
corner, or from the logo in the top bar. The button sits on the side you
choose in the panel (**Menu button: Left / Right**), the same choice as in
the admin console.

On a phone:

- Replies take the whole width.
- The text has its own row, with the controls and the model under it.
- Enter starts a new line; the send button sends.
- A chat's actions are behind its ⋮ button.
- The menu button moves aside while the keyboard is up.
