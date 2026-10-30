# Sign in a desktop app

Some apps on your computer sign in to Fadenstack with a code instead of your password: the app shows a code, and
you approve it in your browser, where you sign in to Fadenstack as usual. Your password, and your organisation's
sign-in, stay in the browser; the app never sees them.

## Before you start

- You need a Fadenstack account, and access to the agent the app works with. Your administrator gives it (see
  [Agents and add-ins](../administer/agents.md)).
- Have the app open on your computer, and your browser on the same computer, or on your phone.

## Approve the sign-in

1. In the app, start signing in. It shows a code of eight letters, such as `BDWP-HTQX`, and opens the page
   **Sign in an app** in your browser. If it cannot open the browser, open your server's address followed by
   `/pair`, for example `https://ai.example.internal/pair`, and type the code there.
2. If the browser is not signed in to Fadenstack yet, sign in as usual: with your password, or with your
   organisation's sign-in. You come back to **Sign in an app** afterwards, with the code still filled in.
3. Check the **Code**, then select **Continue**.
4. Check what asks to be signed in (see [What to check before you approve](#what-to-check-before-you-approve)),
   then select **Approve**.

You see **Signed in. You can go back to the app.** The app finishes signing in by itself, within a few seconds.

To refuse, select **Deny**. You see **Denied. The app was not signed in.**, and the app says the sign-in was denied.

Opening the page, or the link the app opens, approves nothing: only **Approve** does.

## What to check before you approve

The page asks **Sign in** *the agent* **on** *the device*, and shows:

| You see | What to check |
|---|---|
| **Check that the app on your computer shows** and the code | The code must be the one in the app in front of you. If it is not, select **Deny**. |
| **App** | The app's name and version, as the app says. An app can say anything here: it is a hint, not a proof. |
| **Device** | The name the app gave the computer, and its system, also as the app says. |
| **Requested** | The address the request came from, and when. |
| **This request came from another network than yours.** | The app asked from a different network address than your browser's. That is normal when your browser is on your phone or on another connection; if you did not expect it, select **Deny**. |
| **Stays signed in** | How long the app stays signed in at most, and after how long without use the sign-in ends sooner. Your organisation sets both. |

!!! warning "Only approve what you started"
    Approve only if you started the sign-in yourself, just now, on your own computer. Fadenstack never asks you to
    approve a code someone sent you by e-mail, chat or phone. An app you approve works in your name, with your
    access to the agent.

## Limits

- A code works for **10 minutes**, and once. For a new one, start signing in again in the app.
- Wrong or expired codes are counted: **5 per person** and **20 per network address** in **10 minutes**. Beyond
  that, the page says **Too many tries** and when you can try again. Your account is not locked: only entering
  codes waits, and signing in to Fadenstack works as before.

## See and end the sign-in

- **In the app:** signing out in the app ends its sign-in at the server, too.
- **Everywhere at once:** changing your password ends all your sign-ins, the apps' on your devices included (see
  [Your account](account.md#change-your-password)).
- **An administrator, or the agent's maintainers,** see every device signed in to an agent under
  **Signed-in devices** on the agent's **Activity** tab, and can end one there (see
  [Agents and add-ins](../administer/agents.md)). Ask them if you lost a computer that was signed in.

## When it does not work

| You see | What to do |
|---|---|
| **No sign-in waits for this code.** | Compare the code with the app, letter by letter. A code works for 10 minutes: if it expired, start signing in again in the app for a new one. |
| **This sign-in was already approved or denied.** | Each code is used once. Start signing in again in the app. |
| **Too many tries.** | Wait the time the page names, then try again. If it happens to many people at once, they may reach the server through one address; tell your administrator. |
| **This agent is not available to you. Ask your administrator.** | You do not have access to the agent the app asks for, or it was retired. Your administrator can give you access. |
| You are sent to the sign-in page | Your sign-in in the browser ended. Sign in again: you come back to the page with the code. |
