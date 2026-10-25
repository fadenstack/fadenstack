# Verzeichnisse (LDAP, Active Directory) {#directories-ldap-active-directory}

Die Benutzer melden sich mit dem Namen und dem Passwort an, die sie in Ihrem Verzeichnis haben: Active
Directory, OpenLDAP oder ein ähnliches LDAP-Verzeichnis. Sie wählen auf der Anmeldeseite das Verzeichnis und
geben ihren **Kontonamen** und ihr Passwort ein; das Passwort prüft das Verzeichnis, gespeichert wird es hier
nie. Was für alle Anbieter gilt, steht unter [Anmeldeanbieter](sign-in.md).

## Bevor Sie beginnen {#before-you-start}

- Ein Dienstkonto im Verzeichnis, das die Benutzer und Gruppen unterhalb des durchsuchten Teils lesen darf.
  Weitere Rechte braucht es nicht.
- Das Verzeichnis ist über TLS erreichbar: `ldaps://` (meist Port 636), oder `ldap://` (Port 389) mit StartTLS.
- Stammt das Zertifikat des Verzeichnisses von Ihrer eigenen Zertifizierungsstelle: deren Zertifikat, in PEM.

## Das Verzeichnis hinzufügen {#add-the-directory}

1. Öffnen Sie **Zugriffskontrolle → Anmeldeanbieter**, wählen Sie **Anbieter hinzufügen** und unter **Art**
   **Verzeichnis (LDAP, Active Directory)**.
2. Füllen Sie die Felder unten aus, legen Sie fest, wer sich anmelden darf (siehe
   [Anmeldeanbieter](sign-in.md#who-may-sign-in-and-what-they-get)), und wählen Sie **Anbieter hinzufügen**.

| Feld | Was hineingehört |
|---|---|
| **Adresse des Verzeichnisses** | Z. B. `ldaps://dc1.example.com:636`. |
| **StartTLS** | Setzen Sie das Häkchen bei einer `ldap://`-Adresse. |
| **Verzeichnistyp** | **Active Directory**: Die Benutzer melden sich mit ihrem Kontonamen (`alex`) oder ihrem Benutzerprinzipalnamen (`alex@example.com`) an; verschachtelte Gruppen zählen mit. **OpenLDAP und ähnliche**: Die Benutzer melden sich mit ihrer `uid` an. |
| **Dienstkonto** | Sein DN, z. B. `CN=svc-fadenstack,OU=Service,DC=example,DC=com`; bei Active Directory auch sein Benutzerprinzipalname. |
| **Passwort des Dienstkontos** | Wird nie wieder angezeigt; lassen Sie das Feld leer, wenn Sie etwas anderes ändern. |
| **Wo Personen gesucht werden** | Der Teil des Verzeichnisses mit den Benutzern, die sich anmelden dürfen, z. B. `OU=Staff,DC=example,DC=com`, mit allem darunter. |
| **Benutzerfilter (optional)** | Nur, wenn der Filter des Verzeichnistyps nicht passt. `{username}` steht für das, was die Person eingegeben hat. |
| **CA-Zertifikate (optional)** | Das Zertifikat Ihrer Zertifizierungsstelle, in PEM. |
| **Seine E-Mail-Adressen sind geprüft** | Setzen Sie das Häkchen, wenn das Verzeichnis das Ihrer Organisation ist. Sonst tragen Sie Ihre Domains unter **Erlaubte E-Mail-Domains** ein. |
| **Stündlich prüfen** | Ein: Wer im Verzeichnis deaktiviert oder entfernt ist, wird hier innerhalb einer Stunde gesperrt, und die Mitgliedschaften folgen den Gruppen des Verzeichnisses. |

!!! warning
    Ohne TLS gehen Passwörter lesbar durchs Netz. An eine einfache `ldap://`-Adresse ohne StartTLS sendet das
    Anmeldemodul kein Passwort: Die Benutzer sehen „Der Anmeldedienst ist gerade nicht erreichbar“.

??? info "Gruppen aus einem Verzeichnis"
    Gruppenregeln nennen eine Verzeichnisgruppe mit ihrem DN, z. B. `CN=AI Users,OU=Groups,DC=example,DC=com`.
    Bei **Active Directory** zählt auch, wer in einer Gruppe innerhalb dieser Gruppe ist.

## Wenn es nicht funktioniert {#when-it-does-not-work}

| Sie sehen | Was zu tun ist |
|---|---|
| **„Kontoname oder Passwort ist falsch.“** | Dieselbe Meldung, ob das Konto fehlt oder das Passwort falsch ist, damit sich so keine Namen herausfinden lassen. Prüfen Sie den Namen, den die Person eingibt, und ob ihr Eintrag unterhalb von **Wo Personen gesucht werden** liegt. |
| **„Der Anmeldedienst ist gerade nicht erreichbar.“** | Die Adresse ist ein einfaches `ldap://` ohne **StartTLS**, oder das Anmeldemodul erreicht das Verzeichnis nicht, oder das Passwort des Dienstkontos ist falsch. Prüfen Sie Adresse, **StartTLS**, CA-Zertifikate und Dienstkonto. |
| Jemand, der im Verzeichnis deaktiviert ist, meldet sich trotzdem an | Der Eintrag ist nicht mehr deaktiviert, oder **Stündlich prüfen** ist aus. Ein deaktiviertes Konto kann sich so oder so nicht anmelden. |
