# Let the assistant work alongside you

In the Fadenstack desktop app, you can let the assistant work in places you name, such as a document, a folder or an
app, for a while, without asking you before each step. You keep working: it never uses your keyboard, your mouse or the
window you are in. You choose the places and the time, you see while it works, and one click stops it.

## Before you start

- The desktop app, signed in (see [Sign in a desktop app](sign-in-an-app.md)).
- Your administrator allows it for the assistant. Without that, the app does not show **Work alongside me…**.
- **Work alongside me** turned on in **Settings → Working style**. It is off until you turn it on, and it is yours
  alone: anyone else who uses this computer has their own setting. While it is off, **Work alongside me…** opens
  that setting.
- Something to work in: a document open in Word, Excel or PowerPoint with its assistant, a folder you added in
  **Settings → Files**, or a window you share.

## Start it

1. Select **Work alongside me…**: the people icon in the chat's header, or the avatar's menu (right-click the
   avatar).
2. The card **Work alongside you?** shows in the chat. Under **I'll work in:** it lists the places. Select **Remove**
   beside a place to leave it out.
3. Under **For how long**, pick a time. 30 minutes is preselected.
4. Read what the card says it may do without asking and what it still asks about, then select **Allow**.
   **Not now** allows nothing, and a new chat stays empty.

The card then says **Working alongside you**. Nothing runs without asking before your organisation's server has
confirmed it.

??? info "What it may do without asking, and what still asks"
    In the places you allowed, it may read, write and change things without asking, as the card says. It still asks
    before it deletes, sends, pays or publishes anything, before it acts anywhere else, and for every tool your
    administrator wants asked first. Your organisation's server checks every step: a step outside what you allowed
    asks as it always does.

    If a path in a place you allowed leads somewhere else through a link (a symbolic link or, on Windows, a
    junction), the app asks before that step and says where the path really leads.

## While it works

| Where | What you see |
|---|---|
| The avatar | The minutes left, under the avatar |
| The title bar | **Working alongside you · 23 min left**, with **Stop** |
| The avatar's and the tray's menus | **Stop working alongside** |

- **Stop** ends it at once, in every place, and stops the reply that is being written.
- **Two minutes before the end** it asks once whether to keep working alongside you for another 30 minutes: while
  the chat is open, in the chat with **Another 30 min**; otherwise in a notice beside the avatar, **Keep working
  alongside you for another 30 min?**, with **Keep going** and **Not now**. **Another 30 min** and **Keep going** add
  the time, with the same places and the same rules; **Not now** lets it end.
- **A new place:** when you share another window, or open another document with its assistant, the card **Keep working
  alongside you?** offers only that place, under **Also in:**.
- **In Word**, on Windows, the comment margin opens when it starts, so comments it adds later do not move the page
  under your cursor. When it ends, Word's view is as it was. It never turns Track Changes on or off.

??? info "How long it can run"
    Each assistant has a longest time, which your administrator sets, counted from when you selected **Allow**. Once
    it cannot be extended any further, the app says **This cannot run any longer**. To go on, select **Work alongside
    me…** again and answer the card.

## When you step away

- **Locking the screen** pauses it, and unlocking resumes it. Locked for 30 minutes, it ends; the notice waits until
  you are back.
- **Quitting the app** pauses it too. Start the app again within 30 minutes and it goes on; later, it ends.
- Its time runs on while it is paused.

## When it ends

However it ends, one notice says how it went: **Worked alongside you for 25 min**, **Where:** the places, **Ran without
asking:** how many steps ran without asking you, **Still asked you:** how many asked anyway, and why it ended.

You can also end it in the chat's **Settings**, under **Approvals**, with **Revoke**. Your administrator can end it
too; the notice then says that the server ended it. Turning **Work alongside me** off in **Settings → Working
style** ends it as well, in every place.

## When it does not work

| You see | What to do |
|---|---|
| **There is nothing to work in yet** | Open a document in Word, Excel or PowerPoint, add a folder in **Settings → Files**, or share a window, then try again. |
| **The server did not allow working alongside** | Your organisation's server refused it; the notice says why. Ask your administrator whether it should be allowed. |
| **The server could not be reached, so nothing was allowed** | Check your connection, then try again. |
| **Not allowed: the server did not confirm it** | Nothing was allowed. Try again later. |
| **This cannot run any longer** | It reached the longest time for this assistant. Select **Work alongside me…** again. |
| **Stop did not reach the server** | Select **Stop** again. Without a connection, it still ends at its time. |
| **Turning it off did not reach the server** | It runs on until its time. The app tries again until the server answers; you can also select **Stop**. |
