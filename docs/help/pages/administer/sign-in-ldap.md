# Directories (LDAP, Active Directory)

Let people sign in with the name and password they have in your directory: Active Directory, OpenLDAP or a
similar LDAP directory. They pick the directory on the sign-in page and type their **Account name** and
password; the password is checked by the directory and never stored here. For what all providers share, see
[Sign-in providers](sign-in.md).

## Before you start

- A service account in the directory that may read the people and groups below the part you search. It needs
  no other rights.
- The directory is reached over TLS: `ldaps://` (usually port 636), or `ldap://` (port 389) with StartTLS.
- When the directory's certificate comes from your own certificate authority: that authority's certificate,
  in PEM.

## Add the directory

1. Open **Access Control → Sign-in providers**, select **Add provider** and pick **Directory (LDAP, Active
   Directory)** under **Kind**.
2. Fill in the fields below, decide who may sign in (see
   [Sign-in providers](sign-in.md#who-may-sign-in-and-what-they-get)), and select **Add provider**.

| Field | What goes in |
|---|---|
| **Directory address** | For example `ldaps://dc1.example.com:636`. |
| **StartTLS** | Tick it for an `ldap://` address. |
| **Directory type** | **Active Directory**: people sign in with their account name (`alex`) or their user principal name (`alex@example.com`); nested groups count. **OpenLDAP and similar**: people sign in with their `uid`. |
| **Service account** | Its DN, for example `CN=svc-fadenstack,OU=Service,DC=example,DC=com`; for Active Directory also its user principal name. |
| **Service account password** | Never shown again; leave the field empty when you change something else. |
| **Where people are searched** | The part of the directory with the people who may sign in, for example `OU=Staff,DC=example,DC=com`, and everything below it. |
| **User filter (optional)** | Only when the directory type's filter does not fit. `{username}` stands for what the person typed. |
| **Stable ID attribute (optional)** | Only for a directory with neither `objectGUID` (Active Directory) nor `entryUUID` (OpenLDAP): an attribute that never changes for a person, such as `nsUniqueId` (389 Directory Server, FreeIPA). Without one, nobody can sign in with such a directory. |
| **CA certificates (optional)** | Your certificate authority's certificate, in PEM. |
| **Its e-mail addresses are verified** | Tick it when the directory is your organisation's own. Otherwise list your domains under **Allowed e-mail domains**. |
| **Check every hour** | On: people disabled or removed in the directory are suspended here within the hour, and their memberships follow the directory's groups. |

!!! warning
    Without TLS, passwords cross the network readable. The sign-in module sends no password to a plain
    `ldap://` address without StartTLS: people see "The sign-in service cannot be reached right now".

??? info "Groups from a directory"
    Group rules name a directory group by its DN, for example `CN=AI Users,OU=Groups,DC=example,DC=com`.
    With **Active Directory**, a person in a group inside that group counts as a member too.

## When it does not work

| You see | What to do |
|---|---|
| **"The account name or password is wrong."** | The same message whether the account does not exist or the password is wrong, so it cannot be used to find out names. Check the name the person types, and that their entry is below **Where people are searched**. |
| **"The sign-in service cannot be reached right now."** | The address is a plain `ldap://` without **StartTLS**, or the sign-in module cannot reach the directory, or the service account's password is wrong. Check the address, **StartTLS**, the CA certificates and the service account. |
| Someone disabled in the directory still signs in | Their entry is no longer disabled, or **Check every hour** is off. A disabled account cannot sign in either way. |
