# Chat

Talk to your organisation's models: start a chat, pick a model, and find your chats again.

![The chat: your chats and projects on the left, suggestions in the middle, the message box at the bottom](../images/use/chat.png)

## Start a chat

![The message box: Tools, Skills and the paper clip on the left, the Model picker and the send button on the right](../images/use/chat-composer.png)

1. Select **New chat** in the sidebar.
2. Pick a model in the **Model** picker.
3. Type your message and press Enter. Shift+Enter starts a new line.

The reply appears as it is written. To add a file, select the paper clip before you send. A picture you attach shows in the message you sent.

??? info "More about starting a chat"
    A model is already picked: the first in the list, or the one you picked last. A new chat is named after the start of your first message. The model reads an attached file along with your question, and can use it for the rest of the chat. What files you can add, and the limits, are on [Knowledge](knowledge.md).

## Choose a model

![The Model picker opened, listing the models to choose from](../images/use/chat-model.png)

You can switch models in the middle of a chat: the next reply comes from the model now picked, and it sees the conversation so far.

The message box has the text on its own row. Below it, tools, skills and the paper clip are on the left; the model, the thinking level and the send button are on the right.

The thinking level is **Auto**, **Off**, **Low**, **Medium** or **High**. **Auto** lets the model decide. The menu shows only the levels the chosen model tells apart; for some models only **Off** makes a difference. **Off** answers faster; higher levels think longer before answering. The choice is kept per person and per model, and applies from your next message.

??? info "Where does a model run?"
    Some models run on your organisation's own machines, others at an outside provider. The name in the list does not always say which; ask your administrator if it matters for what you write. See [Your data and privacy](privacy.md).

## While a reply is written

| To | Do this |
|---|---|
| Stop the reply | Select the square that replaces the send button. What was written stays, marked **Stopped**. |
| Read earlier messages | Scroll up; the chat stops following the reply. **Jump to the latest message** (the round arrow button) takes you back down. |
| See how the model thought | Some models think first: **Thinking…** shows the last lines, then folds into **Thought for N s**, which you can select. It is not saved with the chat. |

## Leave while a reply is written

You can: the reply keeps being written when you open another chat, go to another page, reload or close the tab. Come back and it shows the reply from its start. A chat whose reply is still being written has a small spinner in the sidebar.

??? info "How many replies at once"
    - One reply at a time in a chat: send the next message once the reply is done.
    - Replies in up to three chats at once, unless your administrator set another number. A fourth is refused until one of them finishes; then select **Retry**.

## When a reply fails

A failed reply stays in the chat with the reason. Select **Retry** under the chat's last reply to ask again with the model now picked; the new reply takes its place.

If the model says the chat is too long for it, the chat tries once more by itself, with fewer of the older messages. Only if that fails too does the reply fail.

??? info "What the model sees afterwards"
    A failed reply is not sent to the model with your later messages. A stopped reply is. If you stopped the last reply, **Retry** is beside it too.

## Details of a reply

![A reply with its size in tokens and the ⓘ that names the model, below the answer](../images/use/chat-reply-details.png)

Under each reply: how long it took (if you had the chat open), its size in tokens (point at the badge for the prompt, the reply and the total), and an ⓘ that names the model. The reply also shows how much of the model's context the chat used (**Context: 12.3k of 25.8k tokens**). To use an answer elsewhere, select its text and copy it.

In a long chat, the oldest messages stop going to the model once the chat no longer fits what the model reads at once; a line in the chat marks where. A model that uses tools can still search the chat's earlier messages when a question needs them, but it can miss a detail: for a new topic, start a new chat.

!!! note "Summaries of long chats"
    Your administrator can switch on summaries for a model. In a long chat, the model then writes a summary of the older messages, and the summary goes to the model in their place. Writing it counts in your usage, as a reply does. The summary is written by AI and can leave out or get wrong what was said; select **Show summary** on the line where older messages stop going to the model to read it. It is kept encrypted with the chat, administrators do not see it in the console, and it is deleted with the chat. Summaries are off unless your administrator switches them on.

## Titles, projects and search

![The sidebar: New chat, Search chats, a project with its settings and bin, and the chats by day](../images/use/chat-sidebar.png)

- **Rename a chat:** select the pencil beside its title in the top bar.
- **Projects** group chats. Select **New project** beside **Projects**. A project's **Project Settings** hold a description, a **System prompt** (instructions that go before every message in its chats) and **Files** (files that go with every message in its chats). A file too long to send whole is searched instead: the model looks up the parts it needs. **Project Settings** can also set the project's default model: its new chats start with it.
- **Move a chat into a project:** drag it onto the project, or use **Move to project**.
- **Search chats** finds chats by their title as you type, not by what was said in them.

![Search chats with a word typed: the sidebar lists the chats whose title matches](../images/use/chat-search.png)

??? info "More about the sidebar"
    The sidebar lists your most recent chats by when you used them (**Today**, **Yesterday**, **Previous 7 days**, **Previous 30 days**, **Older**); **Show more** loads older ones. **Collapse** (the arrow at the bottom) folds it into a narrow strip, and the logo opens it again. The narrow strip has a **New project** button too: it opens the sidebar with the name field ready. **No project** in **Move to project** takes a chat out of its project. Chats are yours: there is no way to share a chat with a colleague from the chat.

!!! warning "Deleting has no undo"
    **Delete** on a chat asks you to confirm first. Once you confirm, it is removed at once and cannot be brought back. Deleting a project asks first too, then erases all the chats in it. What happens to deleted chats on the server is on [Your data and privacy](privacy.md#deleting).

## On a phone

![The chat on a phone, with the round menu button in the bottom corner](../images/use/chat-phone.png){ width="280" }

The sidebar opens from the round menu button in a bottom corner, or from the logo. **Menu button** (**Left** or **Right**) in the sidebar puts it on the side of your thumb. On a phone, Enter starts a new line and the send button sends; a chat's actions (**Move to project**, **Delete**) are behind its ⋮ button.

## When it does not work

| You see | What to do |
|---|---|
| **No models** next to the message box | No model is offered to you yet. Tell your administrator. |
| **A reply is still being written in this chat. Wait for it, or stop it.** | Wait for the reply, or stop it, then send again. |
| **Rate limit reached** with **Replies are being written in 3 of your chats. Wait for one to finish.** | You reached the number of replies you can have at once. Select **Retry** once one is done. |
| **Rate limit reached** with **Try again in N s.** | Your organisation limits how many requests can be sent in a given time. Wait, then select **Retry**. |
| **The model did not start its reply within … s; it may be busy.** | Many people are using that model. Select **Retry**, try again later, or pick another model. |
| **The server restarted while this reply was being written.** | Select **Retry**. |
| **Image generation is turned off in this version. …** | The model answered with a picture, or the request asked for one. This version makes no pictures: ask for the answer in words, or pick another model. |
| **… only PNG, JPEG, WebP and GIF pictures are taken; this one is …** | The picture is in another format, whatever its name ends in. Save it as PNG or JPEG and add it again. |
