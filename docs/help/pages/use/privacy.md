# Your data and privacy

Fadenstack runs on your organisation's own server. This page says what is kept there, who can see it, how personal data is protected when your organisation has set that up, and what leaves your organisation when a model runs at an outside provider.

## What is kept, and where

On your organisation's server:

- your account;
- your chats: the messages, the replies and their titles;
- your projects, with their settings;
- the files you attach, and the text read from them;
- your skills;
- usage records: when you used which model, how much text went in and out, and which tools ran. They do not contain what was said.

On a standard installation, the messages, the replies, chat titles, project instructions and the text read from your files are stored encrypted; your administrator can confirm that yours is set up this way. Project names, file names and the attached files themselves are stored as they are. A model's thinking is not saved at all.

On your own computer:

| | Its conversations | Your sign-in |
|---|---|---|
| **The browser agent** | In your browser, not on the server | In your browser too, not encrypted |
| **The Office add-ins** | On your computer, encrypted | Protected by Windows |

Fadenstack keeps your chats until they are deleted. See [Deleting](#deleting).

## Who can see what

| Who | What they see |
|---|---|
| You | Your chats, projects, files and skills |
| Colleagues | None of your chats or files. Skills you share with them. The organisation's knowledge base is shared by everyone |
| Administrators, in the console | That a chat exists (who, when, in which project), not its title or messages. Project names. The names, sizes and types of your files, not their content. Your usage. Which tools ran. How often personal data was found, not the data itself. Every skill, including private ones |

The console does not show administrators your conversations. The people who run the server do hold the key the chats are encrypted with, so use the chat as you would your organisation's e-mail: private from colleagues, but within your organisation's IT.

## Personal data protection

Your organisation can have Fadenstack find personal data, such as names, e-mail addresses, phone numbers or account numbers, in what you send, and mask it before it reaches a model. Your administrator decides this in a policy: whether it is on at all, for which models, and what is looked for. It is not always on. See [Policies and personal data](../administer/policies.md).

When it is on, personal data is masked in one of two ways:

| Mode | What happens to personal data |
|---|---|
| **`redact`** | It is replaced by a label such as `<PERSON>` before the model sees it, and it does not come back. An answer that mentions it shows the label. |
| **`tokenize_reversible`** | It is replaced by a placeholder such as `[PERSON_1]` on the way to the model, and the real value is put back in the answer you see. |

??? info "If the check cannot run"
    If the check cannot run, the policy decides: your message is not sent, it is sent anyway, or it goes to one of your organisation's own models instead.

!!! note "Detection is not always right"
    Personal data is found automatically, and how well that works depends on the language and on how the text is written: tables, lists, code and short fragments are harder than plain sentences. It finds most personal data in English text, not all of it; in German or other languages it often misses names and places. It can also mask words that are not personal data. Do not paste anything you are not allowed to share.

### See and tighten it for one chat

![The Privacy tab saying Using tenant defaults, with Cloud egress, Local egress, Mode and Fail Action, and the kinds of personal data looked for, each with a lock, above Add entity](../images/use/chat-privacy-tab.png)

1. Open the tools panel with the wrench beside the message box.
2. Select **Privacy**.
3. Read the top line: **Using tenant defaults** (your organisation's policy) or **Session override active** (this chat has its own settings).
4. To protect more in this chat, change a setting:

| Setting | What it does |
|---|---|
| **Cloud egress** | Switch it on to protect what goes to models at outside providers |
| **Local egress** | Switch it on to protect what goes to your organisation's own models |
| **Fail Action** | Choose what happens when the check cannot run |
| **Add entity**, under **Detection Settings** | Adds a kind of personal data to look for |
| **Mode** | Switches between `redact` and `tokenize_reversible`, if your organisation allows it |
| **Clear Session Override** | Goes back to your organisation's settings |

You can only make the protection stricter than your organisation's policy, never looser. Settings the policy enforces show a lock.

??? info "In the browser agent and the Office add-ins"
    In the browser agent and the Office add-ins, a note **Personal data protected** says how many values were kept from the model, and of which kinds.

## Models at outside providers

Your administrator can offer models that run at an outside provider, such as OpenAI or Azure, next to your organisation's own. When you pick one of those, everything the model needs to answer goes to that provider: your message, the earlier messages of the chat, the text of your attached files, project and skill instructions, tool results and knowledge passages. Personal data is masked first if the policy says so. The provider's own terms then apply.

The model list does not always show where a model runs. If it matters for what you write, ask your administrator, or pick a model you know runs inside your organisation. See [Remote providers](../administer/providers.md).

## Tools and the knowledge base

- A tool your administrator connected receives what the model passes to it, often a search term or text from your conversation. Your administrator decides, per service, how personal data in those calls is handled. See [Tools in the chat](tools.md).
- Everything in the organisation's knowledge base can come up in anyone's chat. Your attached files do not go into it. See [Knowledge](knowledge.md).

## Deleting

| You delete | What happens |
|---|---|
| **A chat** | It disappears from your list at once. It is not erased from the server: its messages and files stay there until an administrator deletes the chat. |
| **A project** | It is deleted with all its chats. |
| A chat in **Chat history**, in the browser agent | It is removed from your browser. |

If something must be erased for good, for example personal data you pasted by mistake, ask your administrator.

## When it does not work

| You see | What to do |
|---|---|
| **A PII policy is set for this workspace, but the PII module is not running.** | Your organisation's policy needs the personal-data check, and the module that does it is switched off. Nothing was sent. Tell your administrator. |
| **PII service unavailable and fail_action=block.** | The personal-data check could not run, and your organisation's policy says not to send messages without it. Try again later; if it keeps happening, tell your administrator. |
| An answer shows `<PERSON>` or `[PERSON_1]` where a name should be | The name was masked before it reached the model. With `redact` this is expected. With `tokenize_reversible` the name should come back; if it does not, tell your administrator. |
| **Your admin hasn't allowed changing the PII mode for this chat.** | Your organisation fixes the mode. The other settings in **Privacy** may still be open to you. |
| **… is not applied. Choose it again to apply it.** | The mode you chose earlier did not take effect. Choose it again in **Mode**. |
