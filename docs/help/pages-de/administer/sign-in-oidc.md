# OpenID Connect {#openid-connect}

Verbinden Sie einen Identitätsanbieter, der OpenID Connect spricht, etwa Entra ID, Google Workspace, Okta oder
Keycloak. Die Benutzer wählen auf der Anmeldeseite **Anmelden mit** und den Namen des Anbieters, melden sich
dort an und kommen angemeldet zurück. Was für alle Anbieter gilt, steht unter
[Anmeldeanbieter](sign-in.md).

## Bevor Sie beginnen {#before-you-start}

- Sie dürfen beim Anbieter eine Anwendung (einen „Client“) registrieren.
- Sie kennen den **Aussteller** (Issuer) des Anbieters: die Adresse, unter der er sich nennt, z. B.
  `https://login.microsoftonline.com/<Mandanten-ID>/v2.0` bei Entra ID oder `https://accounts.google.com`.

## Fadenstack beim Anbieter registrieren {#register-fadenstack-at-the-provider}

1. Öffnen Sie in der Konsole **Zugriffskontrolle → Anmeldeanbieter**, wählen Sie **Anbieter hinzufügen** und
   unter **Art** **OpenID Connect**.
2. Geben Sie den **Namen** ein und prüfen Sie den **Schlüssel** und die **Serveradresse**. Der Dialog zeigt
   nun die **Weiterleitungs-URI**, z. B. `https://ai.example.internal/api/auth/sso/example-corp/callback`.
   Wählen Sie das Kopiersymbol.
3. Registrieren Sie beim Anbieter eine Webanwendung mit dieser Adresse als Weiterleitungs-URI (auch Callback-
   oder Antwort-URL genannt). Sie muss den Autorisierungscode-Ablauf (authorization code flow) erlauben.
4. Legen Sie dort ein Client-Secret an und notieren Sie die Client-ID der Anwendung.

## Die Felder des Anbieters ausfüllen {#fill-in-the-providers-fields}

| Feld | Was hineingehört |
|---|---|
| **Aussteller (Issuer)** | Der Aussteller genau so, wie der Anbieter sich nennt, ohne Schrägstrich am Ende, außer der Anbieter hat einen. Das Modul liest die Einstellungen des Anbieters von dort. |
| **Client-ID** | Die ID der Anwendung beim Anbieter. |
| **Client-Secret** | Das angelegte Secret. Es wird nie wieder angezeigt; lassen Sie das Feld leer, wenn Sie etwas anderes ändern. |
| **Scopes** | `openid email profile`, sofern der Anbieter keine anderen braucht, durch Leerzeichen getrennt. |
| **Claim, der die Person benennt** | `sub`, außer der Anbieter hat eine andere ID, die in all seinen Anwendungen gleich bleibt. Entra ID mit [Provisionierung](sign-in-provisioning.md): `oid`, die ID, die die Provisionierung sendet. Legen Sie ihn fest, bevor sich die Benutzer zum ersten Mal anmelden: Eine spätere Änderung lässt alle ihr Konto neu verknüpfen. |
| **UserInfo-Endpunkt fragen** | **Wenn dem ID-Token E-Mail oder Gruppen fehlen**, sofern der Anbieter nichts anderes sagt. |

Legen Sie dann fest, wer sich anmelden darf (siehe
[Anmeldeanbieter](sign-in.md#who-may-sign-in-and-what-they-get)), und wählen Sie **Anbieter hinzufügen**.

!!! note
    Ein Anbieter, der nicht sagt, dass eine E-Mail-Adresse geprüft ist (der Claim `email_verified`), lässt
    sich trotzdem nutzen: Tragen Sie die Domains Ihrer Organisation unter **Erlaubte E-Mail-Domains** ein.
    Adressen darin gelten als geprüft.

??? info "Entra ID"
    1. Öffnen Sie im Microsoft Entra Admin Center **App-Registrierungen** und wählen Sie **Neue Registrierung**.
       Wählen Sie unter **Umleitungs-URI** **Web** und fügen Sie die **Weiterleitungs-URI** aus der Konsole ein.
    2. Fügen Sie unter **Zertifikate & Geheimnisse** ein Client-Secret hinzu.
    3. Fügen Sie unter **API-Berechtigungen** die delegierten Microsoft-Graph-Berechtigungen `openid`, `email`
       und `profile` hinzu und wählen Sie **Administratorzustimmung erteilen**: Sonst wird jede Person bei ihrer
       ersten Anmeldung um Zustimmung gebeten.
    4. Fügen Sie unter **Tokenkonfiguration** dem ID-Token den optionalen Claim `email` hinzu, und einen
       Gruppen-Claim, wenn Sie Gruppenregeln nutzen wollen.
    5. In der Konsole: **Aussteller** `https://login.microsoftonline.com/<Mandanten-ID>/v2.0`, die **Client-ID**
       der Anwendung und das Secret. Mit Provisionierung **Claim, der die Person benennt** `oid` (Entra ID sendet
       ihn mit dem Scope `profile`).

    Entra ID sagt nicht, ob eine Adresse geprüft ist: Tragen Sie Ihre Domains unter **Erlaubte E-Mail-Domains**
    ein. Gruppen sendet es mit ihrer Objekt-ID; Gruppenregeln nennen daher die ID, nicht den Namen der Gruppe.

## Wenn es nicht funktioniert {#when-it-does-not-work}

| Sie sehen | Was zu tun ist |
|---|---|
| **„Der Anmeldedienst ist gerade nicht erreichbar.“** | Das Anmeldemodul konnte die Einstellungen des Anbieters nicht vom **Aussteller** lesen. Prüfen Sie, ob der Server diese Adresse erreicht, und übernehmen Sie den `issuer` genau aus `/.well-known/openid-configuration` des Anbieters. |
| Der Anbieter meldet, dass die Weiterleitungs-URI nicht passt | Tragen Sie die **Weiterleitungs-URI** genau so ein, wie der Dialog sie zeigt, mit `https://` und dem Schlüssel. |
| **„Die Anmeldung hat nicht geklappt.“** direkt nach **Anmelden mit** | Die Anmeldeseite wurde unter einer anderen Adresse geöffnet als der in der **Weiterleitungs-URI**, z. B. über die IP-Adresse. Öffnen Sie sie unter der Adresse, die die Benutzer verwenden. Hat sich diese Adresse geändert, ändern Sie die **Serveradresse** und tragen Sie die neue **Weiterleitungs-URI** beim Anbieter ein. |
| **„Der Anbieter hat Ihre E-Mail-Adresse nicht bestätigt …“** | Tragen Sie Ihre Domains unter **Erlaubte E-Mail-Domains** ein. |
| Benutzer kommen in keine Gruppe | Der Anbieter sendet keinen Claim `groups`. Fügen Sie beim Anbieter einen hinzu, und prüfen Sie, ob die Regeln die Gruppen so nennen, wie der Anbieter sie sendet. |
