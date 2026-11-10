# The desktop app

The Fadenstack desktop app puts the assistant on your computer. This page shows how to get it, what happens when
you start it, how it updates, what it keeps on your computer, and how to remove it.

## Get the app {#get-the-app}

Download the app from your organisation's Fadenstack server: open **Apps** in the chat's menu. It is there when
your administrator offers the app. Otherwise, download it from the desktop agent's page on
fadenstack.com (`https://fadenstack.com/agents/desktop/`).

Each download is listed with its SHA-256 checksum. Compare it with the file you downloaded; on Windows, this
PowerShell line prints it:

```
Get-FileHash <file> -Algorithm SHA256
```

| System | Download | Notes |
|---|---|---|
| **Windows** 11 (Windows 10 22H2 as far as possible), x64 | The setup, a file ending in `-Setup.exe` | Installs the app for you only, without administrator rights, and adds **Fadenstack** to the Start menu. It updates itself. |
| | The portable zip | Needs no installation: unpack it anywhere and start `Faden.Desktop.exe`. It does not update itself. |
| **Linux** | A `.deb` package: Ubuntu 24.04 and 26.04, Debian 13 | Installed and updated through the system's software tool or your organisation's management tools. |
| | An `.rpm` package: Red Hat Enterprise Linux 9 and 10, SUSE Linux Enterprise Desktop 15 SP7 | The same. The app itself only says when a new version is out. |
| **macOS** <span class="fs-planned">Planned</span> | Not available yet | |

??? info "More about Linux desktops"
    On desktops that use Wayland only (Ubuntu 26.04, Red Hat Enterprise Linux 10), what the app can see of other
    windows is limited.

!!! note "Previews are not code-signed yet"
    The current builds are previews and are not code-signed. Windows may show **Windows protected your PC**: select
    **More info**, then **Run anyway**. Some organisations block unsigned programs: then ask your IT department.

## First start {#first-start}

If your IT department has set up the server for you, the app is ready to sign in. If not, it asks for your
server's address, for example `https://ai.example.internal`. Then you sign in with a code you approve in the
browser (see [Sign in a desktop app](sign-in-an-app.md)).

Without a server, select **Try the demo without a server** to see the app with a sample conversation.

## Updates {#updates}

The app checks for a new version a little after it starts, and then once a day. Everything about updates is under
**Settings → About**.

- **A copy installed with the setup** downloads the new version in the background. Then a small notice next to the
  assistant says "Version … is ready", with **Restart now** and **Later**. It never asks while the assistant is
  answering. **Later** keeps the update for the next start; or open **Settings → About → Updates** and select
  **Restart to update**. Your chats and settings stay.
- **The portable zip and the Linux packages** say "Version … is out", with **Download page**.
- **A security update** is named as one ("Security update …").

**Updates** shows where things stand, and has **Check now**. **Update channel** has two choices: **Preview** gets
new versions first; **Stable** gets them once they have been tried.

Your IT department can turn the checks off or fix the channel. Then the row says "Your IT turned updates off on
this computer", or the channel shows **Set by your IT**.

The app installs only versions published by Fadenstack, unchanged.

| You see | What to do |
|---|---|
| The update did not pass its signature check. Nothing was installed. | Nothing was installed and your copy is as it was. Tell your IT department. |
| The list of versions was not renewed in time | Newer versions may be missing. Check your connection: the app tries again later. |

## Your data on this computer {#your-data}

The chats, settings, sign-ins and logs that Fadenstack apps keep for you are in your own user folders. Other
people who use the computer do not share them.

| System | Folders |
|---|---|
| Windows | `%LOCALAPPDATA%\Fadenstack` and `%APPDATA%\Fadenstack` |
| Linux | `~/.local/share/Fadenstack`, `~/.config/Fadenstack` and `~/.local/state/Fadenstack` |

The Office add-ins keep theirs there too. Uninstalling the app keeps them, so a new installation finds your
settings again.

To remove them:

1. If Word, Excel, PowerPoint or Outlook is open, close it. The app asks you to.
2. Open **Settings → About → Your data on this computer** and select **Remove my data…**.
3. Check the folders the app lists, then select **Remove and quit**.

The app signs you out, quits, and then removes everything Fadenstack apps keep for you on this computer, the
Office assistants' chats and sign-ins included. If something could not be removed, the app says so when it starts
next.

!!! warning "This cannot be undone"
    Your chats, settings and sign-ins on this computer are gone for good.

## Remove the app {#remove-the-app}

- **Windows, installed with the setup:** open **Settings → Apps → Installed apps**, find **Fadenstack**, and select
  **Uninstall**.
- **Windows, portable zip:** delete its folder.
- **Linux:** remove **fadenstack-desktop** with the system's software tool, or ask your IT department.

Your data stays on the computer (see [Your data on this computer](#your-data)).
