# Anmeldeanbieter {#sign-in-providers}

Die Benutzer melden sich mit dem Konto an, das sie in Ihrer Organisation bereits haben: über einen
Identitätsanbieter, der OpenID Connect oder SAML spricht, oder mit Name und Passwort aus Ihrem Verzeichnis
(LDAP, Active Directory). Bei der ersten Anmeldung erhalten sie ein Fadenstack-Konto, mit den Rollen und
Gruppen, die Sie festlegen.

## Bevor Sie beginnen {#before-you-start}

- Sie sind als **Superuser** an der Konsole angemeldet.
- Das Modul **Sign-in** ist eingeschaltet (**Einstellungen → Module**). Dann erscheint die Seite
  **Zugriffskontrolle → Anmeldeanbieter**.
- Der Server ist über HTTPS erreichbar, unter der Adresse, die die Benutzer verwenden (z. B.
  `https://ai.example.internal`). Öffnen Sie die Konsole unter dieser Adresse, um einen Anbieter hinzuzufügen:
  Die Adressen, mit denen der Anbieter eingetragen wird, werden daraus gebildet, und Identitätsanbieter
  schicken die Benutzer dorthin zurück.

## Einen Anbieter hinzufügen {#add-a-provider}

1. Öffnen Sie **Zugriffskontrolle → Anmeldeanbieter** und wählen Sie **Anbieter hinzufügen**.
2. Wählen Sie unter **Art**, was Ihre Organisation hat:
    - **OpenID Connect**: Entra ID, Google, Okta, Keycloak und andere. Siehe [OpenID Connect](sign-in-oidc.md).
    - **SAML 2.0**: AD FS, Shibboleth und andere. Siehe [SAML](sign-in-saml.md).
    - **Verzeichnis (LDAP, Active Directory)**: Die Benutzer geben Name und Passwort ihres Verzeichnisses auf
      der Anmeldeseite ein. Siehe [Verzeichnisse](sign-in-ldap.md).
3. Geben Sie einen **Namen** ein: Die Benutzer sehen ihn auf der Anmeldeseite, z. B. „Example Corp“.
4. Prüfen Sie den **Schlüssel**, den die Konsole aus dem Namen gebildet hat. Er ist Teil der Adressen, mit
   denen der Anbieter eingetragen wird, und lässt sich später nicht ändern.
5. Füllen Sie die Felder des Anbieters aus und tragen Sie die oben angezeigten Adressen beim Anbieter ein
   (siehe die Seite zu Ihrer Art). Legen Sie dann fest, wer sich anmelden darf (unten).
6. Wählen Sie **Anbieter hinzufügen**.

Der Dialog zeigt unter **Noch nötig**, was fehlt, bevor er gesendet werden kann. Lehnt das Anmeldemodul eine
Einstellung ab, sagt es im Dialog, welche und warum.

## Wer sich anmelden darf und was die Person erhält {#who-may-sign-in-and-what-they-get}

| Feld | Was es bewirkt |
|---|---|
| **Bei der ersten Anmeldung ein Konto anlegen** | Aus: Nur wer hier schon ein Konto hat, kann sich über den Anbieter anmelden. |
| **Rollen neuer Konten** | Die Rollen, die ein neues Konto erhält. Leer: keine, bis sie jemand vergibt. Wer chatten soll, braucht `default_user` (siehe [Benutzer, Gruppen und Rollen](users.md)). Superuser wird auf diesem Weg niemand. |
| **Erlaubte E-Mail-Domains** | Nur Benutzer mit einer Adresse in diesen Domains dürfen sich anmelden, z. B. `example.com`. Eine Adresse in einer davon gilt als geprüft. Leer: alle. |
| **Personen mit einer Adresse in diesen Domains dürfen sich nur über diesen Anbieter anmelden** | Sie können sich hier nicht mehr mit Passwort anmelden; wer Ihre Organisation verlässt, verliert damit auch den Zugang hier. Superuser behalten ihr Passwort. |
| **Gruppenregeln** | Wer beim Anbieter in einer Gruppe ist, kommt bei der Anmeldung in eine Gruppe hier. Nennen Sie die Gruppe so, wie der Anbieter sie sendet: mit Namen, mit ID (Entra ID sendet IDs) oder mit DN (Verzeichnisse). |
| **Mitgliedschaften entfernen, die der Anbieter nicht mehr vergibt** | Bei jeder Anmeldung verlassen Benutzer die Gruppen hier, die ihnen eine Regel zuvor gegeben hat und jetzt nicht mehr gibt. Von Hand gesetzte Mitgliedschaften bleiben, wie sie sind. |
| **Anmeldung gilt höchstens (Stunden)** | Danach melden sich die Benutzer erneut an. 12, wenn Sie nichts ändern; ist die Anmeldedauer des Servers kürzer, gilt diese. |
| **Sperren nach Tagen ohne Anmeldung** | Konten, die sich über den Anbieter angemeldet haben und so lange nicht genutzt wurden, werden gesperrt: keine Anmeldung, und keiner ihrer Agenten war aktiv. 45, wenn Sie nichts ändern; leer: nie. Superuser werden so nie gesperrt. |

!!! note
    Ein gesperrtes Konto behält alles, was es hatte. **Wieder freigeben** unter **Zugriffskontrolle →
    Benutzer** gibt den Zugang zurück.

## Wie sich die Benutzer anmelden {#how-people-sign-in}

- **OpenID Connect und SAML**: Die Anmeldeseite zeigt **Anmelden mit** und den Namen des Anbieters. Die
  Benutzer werden zum Anbieter geschickt und kommen angemeldet zurück.
- **Verzeichnisse**: Die Anmeldeseite zeigt eine Auswahl zwischen **Fadenstack-Konto** und dem Namen des
  Verzeichnisses. Ist das Verzeichnis gewählt, geben die Benutzer ihren **Kontonamen** und ihr Passwort ein.
  **Passwort vergessen?** wird dann nicht angeboten: Das Passwort gehört zum Verzeichnis.

### Wer hier schon ein Konto hat {#someone-who-already-has-an-account-here}

Meldet sich jemand zum ersten Mal über einen Anbieter an und gibt es schon ein Konto mit dieser Adresse,
werden die beiden nicht von selbst verbunden: Beim Anbieter könnte jemand anderes diese Adresse haben.
Stattdessen erhält der Inhaber des Kontos eine E-Mail mit einem Link, um zu bestätigen, dass die Anmeldung
seine ist. Der Link gilt einmal, 10 Minuten lang. Danach meldet er sich über den Anbieter an.

Für die E-Mail muss der Mail-Dienst eingeschaltet sein. Ohne ihn wird die Anmeldung mit „Es gibt bereits ein
Konto mit dieser Adresse“ abgelehnt: Schalten Sie den Mail-Dienst ein, dann kann der Inhaber bestätigen.

## Einen Anbieter ändern oder entfernen {#change-or-remove-a-provider}

- **Ändern**: das Stiftsymbol in der Zeile des Anbieters. Secrets werden nie wieder angezeigt: Lassen Sie das
  Feld leer, um den gesetzten Wert zu behalten.
- **Vorübergehend abschalten**: Schalten Sie **Personen können sich damit anmelden** aus. Der Anbieter
  verschwindet von der Anmeldeseite.
- **Löschen**: das Papierkorbsymbol. Wer sich damit angemeldet hat, behält das Konto, kann sich aber nicht mehr
  damit anmelden.

## Wenn es nicht funktioniert {#when-it-does-not-work}

Was die Benutzer auf der Anmeldeseite sehen, und was zu tun ist:

| Sie sehen | Was zu tun ist |
|---|---|
| **„Hier gibt es noch kein Konto für Sie, und dieser Anbieter legt keines an.“** | Schalten Sie **Bei der ersten Anmeldung ein Konto anlegen** ein, oder legen Sie das Konto unter **Zugriffskontrolle → Benutzer** an. |
| **„Sie dürfen sich mit diesem Anbieter nicht anmelden.“** | Die Adresse liegt in keiner der **Erlaubten E-Mail-Domains**. |
| **„Der Anbieter hat Ihre E-Mail-Adresse nicht bestätigt …“** | Der Anbieter sagt nicht, dass die Adresse geprüft ist. Nehmen Sie die Domain unter **Erlaubte E-Mail-Domains** auf, oder setzen Sie bei SAML und Verzeichnissen das Häkchen bei **Seine E-Mail-Adressen sind geprüft**, wenn der Anbieter der Ihrer Organisation ist. |
| **„Der Anbieter hat Ihre E-Mail-Adresse nicht genannt …“** | Lassen Sie den Anbieter die E-Mail-Adresse senden (den Scope `email` oder das Attribut `mail`). |
| **„Die Anmeldung hat zu lange gedauert oder wurde schon verwendet.“** | Beginnen Sie auf der Anmeldeseite neu. Eine Anmeldung, die nicht innerhalb von 10 Minuten abgeschlossen ist, verfällt. |
| **„Diese Anmeldung funktioniert nur über HTTPS.“** | Öffnen Sie die Konsole unter ihrer HTTPS-Adresse. |
| **„Der Anmeldedienst ist gerade nicht erreichbar.“** | Das Anmeldemodul läuft nicht oder erreicht den Anbieter nicht. Prüfen Sie die Karte **Sign-in** unter **Einstellungen → Module**. |
| **„Die Anmeldung hat nicht geklappt.“** | Der Anbieter hat abgelehnt oder etwas geantwortet, das das Modul nicht annimmt. Prüfen Sie die Einstellungen auf beiden Seiten; das Protokoll des Anmeldemoduls sagt mehr. |
