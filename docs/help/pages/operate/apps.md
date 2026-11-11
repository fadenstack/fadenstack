# Fadenstack apps

Fadenstack has apps of its own: the desktop agent, the Office add-ins, the Nextcloud app and the Chrome extension. The server tells administrators which versions of them are in use, when a newer one is out or an old one needs replacing, and can offer them to everyone on the **Apps** page.

## The app list

Once a day the server reads a second public, signed file beside the release list: `https://fadenstack.com/updates/v1/apps.json`, with its signature `apps.json.sig`. It is read together with the release list (see [How you hear about a new release](upgrading.md#how-you-hear-about-a-new-release)) and in the same way: the file is fetched like any other, nothing about this server or its apps is sent, and everything is compared on the server.

- **With a mirror** (**Settings**, **Updates**, **Release list address**), the app list is read from the same folder as the mirrored `index.json`.
- **The switch** **Check for new releases** (`updates.check_enabled`) covers both lists.
- **Without internet access,** see [A server without internet access](#a-server-without-internet-access).

The list is signed anew every day and expires 14 days after it was signed. A list that has expired is not taken in.
When the list the server keeps expires because no newer one arrived, the Apps section and **Needs attention** say so:
newer app releases, security fixes included, may be missing. Check the server's connection to fadenstack.com, or
your mirror. This protects against a list being held back, for example by a mirror that is no longer updated.

## Apps in use

Each app reports its name and version to the server when it starts. The server counts the versions it has seen in the last 30 days. No names of people are shown.

## The Apps section on Updates

In the console, open **Updates** and look at **Apps**. For each app it shows:

- the newest release;
- the versions in use here, with older ones marked;
- the end of the app's support;
- whether a release needs a newer Fadenstack than this server runs;
- the release notes, and download links with their SHA-256 checksums;
- the switch **Offer to people** (see [Offer an app to people](#offer-an-app-to-people));
- the [connection files](#connection-files).

## Notices

The app list adds items to **Needs attention** on the dashboard. Each can be muted like the others, and muting one release does not mute the next.

| Item | When | Shown |
|---|---|---|
| A security release of an app | An older version of the app is in use here | As a warning; as critical for a critical release |
| An app version past the end of its support | That version is in use here | Always, while the app list exists |
| The app list has not been renewed | The kept list is past its expiry | Always, as a warning |
| A new release of an app | The app is in use here | Only with **News about Fadenstack apps** |
| A new app | It was first released in the last 30 days, and nobody here uses it or is offered it | Only with **News about Fadenstack apps** |

**News about Fadenstack apps** (`updates.app_news`) is under **Settings**, **Updates**. It is on by default. The first three items do not depend on it.

### E-mail

When the server can send e-mail, the administrators get one message for each app release that fixes security problems of a version in use. Never for news.

## Offer an app to people

Switch **Offer to people** on for an app (stored in the setting `updates.apps_offered`). Everyone who is signed in then sees it on their **Apps** page: the download, the newest version, the end of support, and how to connect it to this server, with the server's address. See [Apps](../use/apps.md). Nothing else from the app list reaches people who are not administrators.

## Downloads are links

A download is a link to where the app is published: its release on GitHub under `github.com/fadenstack/`, the Chrome Web Store, or the Nextcloud app store. The server downloads nothing. Clicking a link sends the person's address (IP) to that site, as any link does. The links send no referrer, so that site does not learn this server's address.

## Connection files

Under **Apps** on **Updates** you can download files that point an app at this server. None of them holds a password or token: each person still signs in with their own account.

| App | File | What to do with it |
|---|---|---|
| Office add-ins | `policy.json` | Put it on each computer at `%PROGRAMDATA%\Fadenstack\Office\policy.json`, for example by Group Policy or Intune. It holds this server's address and the four agents (Excel, Word, PowerPoint, Outlook). |
| Nextcloud app | `connect-nextcloud.sh` | Run its `occ` commands on the Nextcloud server as the web server's user. They enable the app `fadenstack_agent` and set `server_url`, `agent` and `integration_enabled`. |
| Chrome extension | `fadenstack-chrome-policy.json` | A Chrome policy: it installs the extension from the Chrome Web Store and sets the extension's managed settings `serverUrl` and `agent`. On Linux, put the file in `/etc/opt/chrome/policies/managed/`. On Windows and macOS, set the same values by Group Policy, Intune or a configuration profile. |
| Desktop agent | none | The person enters the server's address at the first start and signs in. |

??? info "When this server uses its own certificate authority"
    For the Office add-ins, also download `/install/ca.crt` from the server and put it at `%PROGRAMDATA%\Fadenstack\Office\fadenstack-ca.crt` on each computer.

## A server without internet access

Download `apps.json` and `apps.json.sig` from `https://fadenstack.com/updates/v1/` on another computer and upload them on **Updates**, under **No internet on this server?**, as for the release list (see [A server without internet access](upgrading.md#a-server-without-internet-access)). Or copy them unchanged, with the release list, to your own web server and set **Release list address** to it.
