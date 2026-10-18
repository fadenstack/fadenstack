# The browser agent

The browser agent is Fadenstack in Chrome's side panel. It works with the page you have open: it answers questions about it, summarises and translates it, shows you where something is, copies tables out, compares tabs, reads PDFs, looks at screenshots, and writes into the mail or ticket you are working on. It answers with your organisation's model, through your Fadenstack server, and in the **Ask** tool mode, where every chat starts, it asks before it changes anything.

![The browser agent's panel: suggested questions, the bar naming the open page, and the message box with its buttons below](../images/use/browser-agent-panel.png)

## Before you start

- **The extension** is installed in Chrome. Your IT department installs it for you, or tells you where to get it.
- **Access**: your administrator has given you the browser agent. See [Agents and add-ins](../administer/agents.md).
- **The server's address and the agent's name**, unless your IT department set them for you.

## Open the panel and sign in

1. Select the Fadenstack button in Chrome's toolbar, or press Alt+Shift+F.
2. The first time, enter the **Server** and the **Agent** in **Connect to Fadenstack**.
3. Select **Connect**, and allow the access when Chrome asks.
4. Enter your **E-mail** and **Password** (your Fadenstack account).
5. Select **Sign in**.

If your IT department set the server and agent, the form shows them and you only select **Connect** and sign in.

??? info "Server and Agent"
    - **Server**: your Fadenstack server, for example `https://ai.example.internal`.
    - **Agent**: its address, as the console shows it under **Agents**; `browser-assistant` unless you were told otherwise.

## Language and settings

The panel shows itself in your browser's language. To pick another, select the gear (**Settings**) at the top of the panel, then a language under **Language**: the whole panel changes at once, the conversation, the agent's greeting and the right-click items too. **Automatic** goes back to the browser's language. The language is kept for your account, so the console and the Office add-ins follow it, on this and your other computers. If your organisation set a language, the settings show it with **Set by your organisation**, and you cannot change it.

Below the languages, **Settings** shows which agent you use on which server, with **Sign out on this device** and **Change server or agent**.

**About**, the last entry under the gear, names the agent and its version, the model, the extension, the Fadenstack SDK it is built with and the server; **Copy details** copies them for a question to your IT department. It links to your organisation's Help and to fadenstack.com. A small **Fadenstack** link also sits under the message box (what such a link sends: [Your data and privacy](privacy.md#links-to-fadenstackcom)).

## Ask about a page

Type your question in the panel, for example "Summarise this page" or "What does this contract say about notice periods?". The bar above the message box names the page the agent works with.

??? info "What the agent reads on a page"
    The agent reads the page itself when you ask about it: its text, headings, lists and tables, the visible part, without menus, footers and side bars, and without text the page marks as hidden. A long page is read in parts. Parts of the page that come from its own site in a frame, such as an editor or an embedded form, are read in place; a frame from another site is read when you have allowed that site too.

The buttons under the message box add something to your next message:

| Button | Adds |
|---|---|
| **Add this page** (the page) | The page you are on |
| **Add the selection** (the quote mark) | What you selected on the page |
| **Add tabs** (the tab) | Tabs of this window you pick, to compare them (**All readable tabs** takes all of them) |
| **Add a PDF from this computer** (the paper clip) | A PDF file; you can also drop it on the panel. With a model that sees pictures: **Add a PDF or a picture from this computer** |
| **Add a screenshot** (the camera) | What the page shows, as a picture. Only with a model that sees pictures |
| **Quick actions** (the sparkle) | See [Quick actions](#quick-actions) |

![Add tabs opened: All readable tabs and the window's two tabs to tick, with Cancel and Add](../images/use/browser-agent-add-tabs.png)

**New chat** at the top of the panel starts a fresh conversation; **Chat history** lists your earlier ones in this browser.

??? info "More about screenshots and pictures"
    Pictures go to the agent only when its model sees pictures; otherwise the camera is not shown, and a picture you paste is not added: the panel says so. You can also paste a picture into the message box, or drop one on the panel.

    **Add a screenshot** takes the page directly when you opened the panel from the toolbar button, the shortcut or a right-click item on that page. Otherwise Chrome asks what to share: pick the tab, another window or your screen. Fadenstack takes one picture of it, and the sharing ends at once.

## Quick actions

Quick actions ask the agent at once, in your language. They are in the panel's **Quick actions** menu and in the right-click menu under **Fadenstack**:

| On | Quick action |
|---|---|
| The page | **Summarise this page**, **Translate this page into …**, **Ask about this page**, **Ask about a screenshot of this page** |
| Text you selected | **Explain**, **Summarise**, **Translate into …**, **Ask about "…"** |
| Text you selected in a field or editor | **Improve the writing**: the agent's version replaces your selection, after you allow it |

**Translate into …** offers the panel's language and English. To ask about a selection from the keyboard, select the text and press Alt+Shift+S: the panel opens with the selection added.

## Skills

Skills are ways of working your organisation set up, such as "Reply in our style" or "Check a contract against our checklist". To use one for a message, type `$` in the message box (or `/` at its start), and pick the skill from the list: type a few letters to narrow it, then press Enter. The skill shows above the message box and goes with that message only. See [Skills](skills.md).

## Web search

The agent can search the web with Chrome's own search engine, the one set in Chrome. It is off until you switch it on: select the gear (**Settings**), then **Search the web with Chrome**. If your organisation decided for you, the item shows **Set by your organisation**.

In the **Ask** tool mode, every search asks first, and the card shows the query. The search runs in a tab in the background, and the agent reads the results like a page, so the first time Fadenstack needs your permission for the search engine's site: select **Allow …** above the message box, then ask again. It then opens a result or two and gives you their links.

??? info "Where a search goes"
    The query goes to the search engine set in Chrome, not through your organisation's server, as a search you typed into the address bar would. The agent is told never to put personal data or text from the page into a query unless you asked for exactly that; read the query on the card before you allow it.

## What else it can do

| Ask it to | What it does |
|---|---|
| **Show you where** something is on the page | It marks the passage and scrolls to it. In a PDF it opens the PDF at the right page. |
| **Copy a table** or text | It puts it on your clipboard as text and as a table, so it pastes into Excel as cells and into Word or Outlook as a table. Every table in an answer also has a copy button. |
| **Write into a mail, a ticket or a form** | It fills form fields, and writes formatted text into the editor on the page, such as a mail in Outlook on the web or Gmail, a ticket, or a note. Say where: at your cursor, below the greeting, at the end, or replacing everything. |
| **Improve or translate text you selected in a field** | It puts its version in place of your selection. |
| **Answer from your organisation's documents** | It searches the knowledge your administrator gave the agent, and names the document it used. See [Knowledge](knowledge.md). |
| **Read PDFs** | It reads PDFs open in Chrome, and PDFs you add. Long PDFs are read page by page as needed. |
| **Look at a picture** | With a model that sees pictures: screenshots and pictures you add, such as a chart or an error message. |

## What it asks first

The **Tool mode** at the top of the panel decides what runs without asking:

| What | Off | Read only | Ask (default) | Auto |
|---|---|---|---|---|
| Read the page, tabs, PDFs, the selection; search; show where | no | runs | runs | runs |
| Fill a field, write into an editor, replace your selection, copy to the clipboard, open a page, search the web | no | no | asks | runs |
| Press a button or follow a link | no | no | asks, every time | asks, every time |

![A card asking Run copy_to_clipboard?, showing the table it will copy, with the Allow, Allow for this chat and Deny buttons](../images/use/browser-agent-approval.png)

When it asks, a card shows **Run …?** with the action and what it is given. Select **Allow** (this once), **Allow for this chat** (until you close or reopen the chat), **Allow always** (for this agent in this browser), or **Deny**. Pressing a button, and anything your administrator wants asked first, offers only **Allow** and **Deny**: it asks every time. **Reset approvals**, at the bottom of the tool-mode menu, takes back what you allowed for the chat and always.

??? info "What the card shows for a field or a button"
    For a field or a button, what the action is given is the page's internal reference, not its label.

If you close the panel while the agent answers, the answer stops: the agent works in the panel. When you open the panel again in that window, the chat is back, says **The answer stopped when the panel was closed**, and **Retry** asks again.

## What it never does

- It never reads the content of a site you have not allowed; only the titles and addresses of this window's tabs reach the model. On such a site the bar says **The agent cannot read …**; select **Allow on this site** (or **All sites**) to let it. A quick action on text you selected sends that text, even from a site you have not allowed.
- It never reads out or fills in password fields, nor card fields the site marks as such. A payment field the site does not mark is read and filled like any other field.
- It never presses a button, such as **Send**, without asking you, every time: no earlier answer covers it.
- It is told never to take instructions from the page or a picture: what they say is something to read, not an order to follow. A page can still mislead a model, so read what a card asks before you allow it.
- It never reaches files on your computer that you did not add.

## Permissions it asks for

At installation, Chrome lets the extension open its side panel, keep its settings and your sign-in, add its right-click items, read the titles and addresses of your tabs, search with Chrome's search engine when you switch web search on, and read and act on pages, but only on sites you allow. When you use the selection shortcut or **Ask about a screenshot of this page**, Chrome lets it into that tab that once. It asks separately to reach your Fadenstack server (when you select **Connect**) and for each site you allow. You can take any site back in Chrome's settings for the extension.

## Where your data goes

- What you ask, and what the agent reads to answer (the page, tabs, PDF pages, pictures you add), goes to your organisation's server and to the model your administrator chose for the agent.
- With web search switched on, a search you allow goes to the search engine set in Chrome.
- PDFs are read inside your browser. Pictures are made smaller in your browser before they go.
- Your conversations stay in this browser. The server records which tools ran and how long they took, not what they read.
- Your sign-in is kept in this browser and is not encrypted there. On a computer others use, sign out when you are done: select the gear (**Settings**) at the top of the panel, then **Sign out on this device**.

More on what is kept and who sees it: [Your data and privacy](privacy.md).

## What it cannot read

| What | What happens |
|---|---|
| A scan (pages that are pictures) | It says so. With a model that sees pictures, add a screenshot instead |
| A PDF with a password | It says so: open it in a PDF reader instead |
| A PDF over 50 MB | It says so |
| A PDF on your computer opened in Chrome | Add it with **Add a PDF from this computer** instead |
| A selection inside Chrome's PDF viewer | Copy the passage into your message |
| A part of the page from a site you have not allowed (a frame) | It says so. Allow that site in Chrome's settings for the extension (**Site access**) |
| Chrome's own pages and the Chrome Web Store | Chrome does not let extensions read them |

## When it does not work

| You see | What to do |
|---|---|
| **… does not answer. Check the server's address, and that this computer trusts its certificate.** | The address is wrong, or your computer does not trust the server's certificate. Check the address, or tell your IT department. |
| **There is no agent '…' on …** | The **Agent** name is wrong. Use **Change server or agent** and enter it as your administrator gave it. |
| **You have no access to '…'. Ask your administrator to grant it to you or your team.** | You are not yet allowed to use this agent. |
| **'…' has no published version yet.** or **'…' is retired.** | The agent is not available. Ask your administrator. |
| **Your account is deactivated.** | Ask your administrator. |
| **Fadenstack needs to reach the server. Allow it when Chrome asks.** | You declined Chrome's question. Select **Connect** again and allow it. |
| **This agent's model does not see pictures.** | Pictures cannot go to this agent. Ask your administrator if you need them. |
| **Select some text on the page first.** | A quick action on a selection found none. Select the text, then choose it again. |
