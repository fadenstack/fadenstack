# SAML

Connect an identity provider that speaks SAML 2.0, such as AD FS, Shibboleth or Entra ID. People select
**Sign in with** and the provider's name on the sign-in page, sign in there, and come back signed in. For
what all providers share, see [Sign-in providers](sign-in.md).

## Before you start

- At the identity provider, you may add a service provider (also called relying party or enterprise
  application).
- You have the identity provider's metadata: an address it is published at, or the XML file.

## Register Fadenstack at the identity provider

1. In the console, open **Access Control → Sign-in providers**, select **Add provider** and pick **SAML 2.0**
   under **Kind**.
2. Enter the **Name**, check the **Key** and the **Server address**. The dialog now shows three values:

    | In the console | At the identity provider |
    |---|---|
    | **Metadata address** | The service provider's metadata. Most identity providers read everything from it. |
    | **Entity ID** | The identifier (entity ID) of the service provider. Empty: the metadata address. Give another one only when the identity provider knows this server by it already. |
    | **Assertion consumer service (ACS)** | The reply URL; answers come back to it by HTTP POST. |

3. At the identity provider, add the service provider with the **Metadata address**, or with the **Entity ID**
   and the **ACS** address.
4. Have it send:
    - a persistent NameID, or the `subject-id` or `pairwise-id` attribute: a value that stays the same for a
      person. A transient NameID is refused;
    - the e-mail address (`mail`, `email`, or the e-mail address claim of Microsoft's identity providers);
    - optionally the display name and the groups (`displayName`, `groups` or `memberOf`).

## Fill in the identity provider's fields

| Field | What goes in |
|---|---|
| **Identity provider's metadata** | **From its address**: the address it publishes its metadata at (`https://`). **Pasted**: the XML of its metadata. |
| **Its e-mail addresses are verified** | Tick it when the identity provider is your organisation's own: SAML does not say whether an address is verified. Otherwise list your domains under **Allowed e-mail domains**. |
| **Sign the requests sent to the identity provider** | Only when the identity provider asks for signed requests. The certificate is in the **Metadata address**. |

Then decide who may sign in (see [Sign-in providers](sign-in.md#who-may-sign-in-and-what-they-get)) and select
**Add provider**.

!!! note
    Every answer's assertion must be signed by the identity provider; unsigned ones are refused.

??? info "Entra ID"
    1. In the Microsoft Entra admin center, open **Enterprise applications**, select **New application**, then
       **Create your own application**, and pick SAML as the single sign-on method.
    2. Under **Basic SAML Configuration**, enter the **Entity ID** as the Identifier and the **ACS** address as
       the Reply URL.
    3. In the console, pick **From its address** and paste the **App Federation Metadata Url**.

    Entra ID sends groups by their object ID: group rules name the ID, not the group's name.

??? info "Okta"
    1. In the Okta Admin Console, select **Applications → Create App Integration**, pick **SAML 2.0**, and name
       the application.
    2. Under **Configure SAML**, enter the **ACS** address as the **Single sign-on URL** and the **Entity ID** as
       the **Audience URI (SP Entity ID)**.
    3. Under **Attribute Statements**, add two:
        - Name `urn:oasis:names:tc:SAML:attribute:subject-id`, name format **URI Reference**, value
          `user.getInternalProperty("id")`: Okta's ID for the person, the same one its provisioning sends. Okta's
          NameID is the e-mail address, which can change, so it is no ID.
        - Name `email`, value `user.email`.

        In newer consoles, they are on the **Sign On** tab under **Attribute Statements → Show legacy
        configuration**.
    4. In the console, pick **From its address** and paste the **Metadata URL** from Okta's **Sign On** tab.

## When it does not work

| You see | What to do |
|---|---|
| **"The sign-in service cannot be reached right now."** | The sign-in module could not read the identity provider's metadata from its address. Check that the server reaches it over HTTPS, or paste the XML instead. |
| The identity provider says the reply URL or entity is unknown | Register the **Entity ID** and the **ACS** address exactly as the dialog shows them. |
| **"The sign-in did not work."** after the identity provider | The answer was unsigned, for another service provider, too old, or named the person by nothing that stays the same. **Logs → Audit** says which: `auth.sign_in.refused` for the provider, with the reason (`no_stable_subject`: send a persistent NameID or `subject-id`, see Okta above). Check the identity provider's settings for this service provider. |
| **"The provider did not give your e-mail address …"** | Have the identity provider send the `mail` or `email` attribute. |
