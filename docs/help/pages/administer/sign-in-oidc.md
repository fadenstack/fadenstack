# OpenID Connect

Connect an identity provider that speaks OpenID Connect, such as Entra ID, Google Workspace, Okta or Keycloak.
People select **Sign in with** and the provider's name on the sign-in page, sign in there, and come back
signed in. For what all providers share, see [Sign-in providers](sign-in.md).

## Before you start

- At the provider, you may register an application (a "client").
- You know the provider's **issuer**: the address it names itself by, for example
  `https://login.microsoftonline.com/<tenant ID>/v2.0` for Entra ID or `https://accounts.google.com`.

## Register Fadenstack at the provider

1. In the console, open **Access Control → Sign-in providers**, select **Add provider** and pick
   **OpenID Connect** under **Kind**.
2. Enter the **Name**, check the **Key** and the **Server address**. The dialog now shows the **Redirect
   URI**, for example `https://ai.example.internal/api/auth/sso/example-corp/callback`. Select the copy icon.
3. At the provider, register a web application with that address as its redirect URI (also called callback or
   reply URL). It must allow the authorization code flow.
4. Create a client secret there, and note the application's client ID.

## Fill in the provider's fields

| Field | What goes in |
|---|---|
| **Issuer** | The issuer exactly as the provider names itself, with no trailing slash unless the provider has one. The module reads the provider's settings from there. |
| **Client ID** | The application's ID at the provider. |
| **Client secret** | The secret you created. It is never shown again; leave the field empty when you change something else. |
| **Scopes** | `openid email profile` unless the provider needs others, separated by spaces. |
| **Ask the UserInfo endpoint** | **When the ID token lacks the e-mail or groups**, unless the provider says otherwise. |

Then decide who may sign in (see [Sign-in providers](sign-in.md#who-may-sign-in-and-what-they-get)) and select
**Add provider**.

!!! note
    A provider that does not say an e-mail address is verified (the `email_verified` claim) can still be
    used: put your organisation's domains under **Allowed e-mail domains**. Addresses in them count as
    verified.

??? info "Entra ID"
    1. In the Microsoft Entra admin center, open **App registrations** and select **New registration**.
       Under **Redirect URI**, pick **Web** and paste the **Redirect URI** from the console.
    2. Under **Certificates & secrets**, add a client secret.
    3. Under **Token configuration**, add the optional claim `email` to the ID token, and add a groups claim
       if you want group rules.
    4. In the console: **Issuer** `https://login.microsoftonline.com/<tenant ID>/v2.0`, the application's
       **Client ID** and the secret.

    Entra ID does not say whether an address is verified: list your domains under **Allowed e-mail domains**.
    It sends groups by their object ID, so group rules name the ID, not the group's name.

## When it does not work

| You see | What to do |
|---|---|
| **"The sign-in service cannot be reached right now."** | The sign-in module could not read the provider's settings from the **Issuer**. Check that the server reaches that address, and copy the `issuer` exactly from the provider's `/.well-known/openid-configuration`. |
| The provider says the redirect URI does not match | Register the **Redirect URI** exactly as the dialog shows it, including `https://` and the key. |
| **"The sign-in did not work."** right after **Sign in with** | The sign-in page was opened at another address than the one in the **Redirect URI**, for example by IP address. Open it at the address people use. If that address has changed, change the **Server address** and register the new **Redirect URI** at the provider. |
| **"The provider did not confirm your e-mail address …"** | Add your domains to **Allowed e-mail domains**. |
| People join no groups | The provider sends no `groups` claim. Add one at the provider, and check that the rules name groups as the provider sends them. |
