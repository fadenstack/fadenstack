# Mail

Fadenstack verschickt Mail für Passwort-Zurücksetzungen, für die Codes, mit denen eine Anmeldung mit einem Konto
verknüpft wird, und für Warnungen an Administratoren. Jede Mail übergibt es dem Mailserver Ihrer Organisation,
auf diesem oder einem anderen Rechner, über eine verschlüsselte Verbindung: angemeldet mit dem Passwort eines
Kontos, als App in Microsoft 365 oder als Rechner, den Ihr Relay annimmt. Mail braucht Fadenstack Enterprise.

![Einstellungen, Mail: oben der Mail-Status, darunter Mailserver, Anmeldung und vertrauenswürdige Zertifikate](../images/administer/mail.png)

## Bevor Sie beginnen {#before-you-start}

- Sie sind in der Konsole als **Superuser** angemeldet.
- Das Mail-Modul läuft: **Einstellungen → Module**, **Mailer**.
- Sie kennen den Mailserver: seinen Namen, seinen Port (587 für STARTTLS, 465 für TLS von Anfang an) und ein
  Konto, das als Absenderadresse senden darf. Für Microsoft 365 brauchen Sie statt eines Kontos eine App (unten).
- Dieser Server erreicht den Mailserver auf diesem Port. Bitten Sie Ihr Netzwerkteam, ihn durch Firewalls
  zu lassen.
- Ein neues Passwort oder ein neuer geheimer Clientschlüssel braucht den **Root-Modus**: Schalten Sie ihn in
  der oberen Leiste ein, bevor Sie speichern.

## Mail einrichten {#set-up-mail}

1. Öffnen Sie **Einstellungen** und wählen Sie den Reiter **Mail**.
2. Tragen Sie unter **Mailserver** den **Server** ein und wählen Sie die **Verschlüsselung**. Der **Port** folgt
   der Verschlüsselung, es sei denn, Sie tragen einen eigenen ein.
3. Wählen Sie unter **Anmeldung** bei **Anmelden mit**:
    - **Benutzername und Passwort**: das Konto auf dem Mailserver, etwa das Postfach eines Hosting-Anbieters;
    - **Microsoft 365**: siehe [Microsoft 365](#microsoft-365) unten;
    - **Ohne Anmeldung**: ein Relay in Ihrem Netz, das Mail von diesem Server annimmt.
4. Stammt das Zertifikat des Mailservers von der eigenen Zertifizierungsstelle (CA) Ihrer Organisation, fügen
   Sie das Zertifikat der CA unter **Vertrauenswürdige Zertifikate** ein. Zertifikaten öffentlicher CAs wird
   bereits vertraut.
5. Tragen Sie unter **Absender** die **Absenderadresse** und den **Absendernamen** ein, den die Empfänger sehen.
6. Tragen Sie unter **Links** die **Konsolenadresse** ein: wo die Konsole geöffnet wird, z. B.
   `https://ai.example.internal`. Links in Mails beginnen damit; ohne sie geht keine Mail hinaus.
7. Fügen Sie unter **Warnungen** die Adressen hinzu, die außer jedem aktiven Administrator Warnungen bekommen.
8. Wählen Sie unter **Testmail** **Testmail senden**. Sie geht mit den Einstellungen hinaus, wie sie auf der
   Seite stehen, gespeichert oder nicht, an Ihre eigene Adresse, wenn Sie keine andere eintragen.
9. Wählen Sie **Speichern**.

Hat es geklappt, kommt die Testmail an, und oben auf der Seite steht **Mail geht hinaus.** Änderungen gelten ab
der nächsten Mail; nichts wird neu gestartet.

??? info "Mehr zu Verschlüsselung, Zertifikaten und gespeicherten Geheimnissen"
    - Mail geht nie unverschlüsselt hinaus. Fadenstack verwendet TLS 1.2 oder neuer und prüft das Zertifikat des
      Mailservers: seinen Namen und dass eine vertrauenswürdige CA es ausgestellt hat. Es gibt keine Einstellung,
      die das abschaltet.
    - **Vertrauenswürdige Zertifikate** nimmt ein oder mehrere Zertifikate im PEM-Format, jedes von
      `-----BEGIN CERTIFICATE-----` bis `-----END CERTIFICATE-----`. Nach dem Speichern zeigt die Seite jedes mit
      seinem Ablaufdatum.
    - Eine CA muss Zertifikate signieren dürfen (Schlüsselverwendung, wie RFC 5280 es verlangt). Unter einer CA,
      die das nicht darf, sagt die Seite es: Fügen Sie stattdessen das eigene Zertifikat des Mailservers ein oder
      lassen Sie die CA neu ausstellen.
    - Ein Relay mit selbst signiertem Zertifikat (wie Exchange Server es von Haus aus hat): Fügen Sie dieses
      Zertifikat ein.
    - Passwort und geheimer Clientschlüssel werden verschlüsselt gespeichert und nie wieder angezeigt. Sie gehen
      nur an den Server und Port, für die sie gespeichert wurden: Ändern Sie **Server** oder **Port**, geben Sie
      sie erneut ein.
    - **Begrüßungsname** ist der Name, mit dem dieser Server den Mailserver begrüßt. Lassen Sie ihn leer: Dann
      gilt die Domain der Absenderadresse. Gehärtete Mailserver lehnen einen Namen ab, der kein vollständiger
      DNS-Name ist.

## Das Postfach eines Hosting-Anbieters {#a-hosting-providers-mailbox}

Die meisten Hosting-Anbieter bieten ein Postfach mit SMTP-Zugang. Ihre Hilfeseiten nennen Server und Port,
z. B. `smtp.example.com` auf 587 mit STARTTLS oder auf 465 mit TLS von Anfang an.

1. Wählen Sie **Benutzername und Passwort**. Der **Benutzername** ist meist die Adresse des Postfachs.
2. Tragen Sie diese Adresse auch als **Absenderadresse** ein. Die meisten Anbieter lehnen andere
   Absenderadressen ab.

## Microsoft 365 {#microsoft-365}

Exchange Online schaltet die Anmeldung mit Passwort für SMTP ab Ende Dezember 2026 standardmäßig ab. Fadenstack
meldet sich stattdessen mit OAuth an, als App, die Sie in Microsoft Entra ID registrieren. Kein Passwort einer
Person wird gespeichert, und die App darf nur aus dem Postfach senden, das Sie erlauben.

Sie brauchen einen Administrator in Microsoft Entra ID und in Exchange Online sowie ein Postfach, aus dem die
Mail kommt, z. B. `noreply@example.com`.

1. Öffnen Sie im Microsoft Entra Admin Center **App registrations** und wählen Sie **New registration**. Wählen
   Sie **Accounts in this organizational directory only**. Notieren Sie auf der **Overview** der App die
   **Application (client) ID** und die **Directory (tenant) ID**.
2. Öffnen Sie **Certificates & secrets** und fügen Sie ein **New client secret** hinzu. Kopieren Sie seinen
   **Value** (nicht seine ID): Er wird nur einmal angezeigt.
3. Öffnen Sie **API permissions**, wählen Sie **Add a permission**, **APIs my organization uses**, **Office 365
   Exchange Online**, **Application permissions**, dann **SMTP.SendAsApp**. Wählen Sie **Grant admin consent**.
4. Registrieren Sie die App in Exchange Online PowerShell und geben Sie ihr das Postfach. Die Objekt-ID ist die
   auf der Seite der App unter **Enterprise applications**, nicht unter App registrations.

    ```powershell
    Connect-ExchangeOnline -Organization example.onmicrosoft.com
    New-ServicePrincipal -AppId <Anwendungs-ID> -ObjectId <Objekt-ID der Enterprise-Anwendung>
    $app = Get-ServicePrincipal -Identity <Anwendungs-ID>
    Add-MailboxPermission -Identity noreply@example.com -User $app.Identity -AccessRights FullAccess
    Set-CASMailbox -Identity noreply@example.com -SmtpClientAuthenticationDisabled $false
    ```

    Die letzte Zeile schaltet SMTP für dieses Postfach ein; in neueren Mandanten ist es aus.

5. Wählen Sie in der Konsole bei **Anmelden mit** **Microsoft 365**. Der Server wird `smtp.office365.com`,
   Port 587, STARTTLS. Tragen Sie **Postfach**, **Mandant**, **Anwendungs-ID (Client-ID)** und **Geheimer
   Clientschlüssel** ein.
6. Tragen Sie das Postfach als **Absenderadresse** ein. Soll von einer anderen Adresse gesendet werden, geben Sie
   der App dort **Send As** (`Add-RecipientPermission`).
7. Senden Sie eine Testmail und speichern Sie.

Läuft der geheime Clientschlüssel in Entra ID ab, bleibt die Mail mit **Microsoft 365 hat kein Token ausgestellt**
stehen: Fügen Sie dort einen neuen hinzu und tragen Sie ihn hier ein.

## Ein Relay in Ihrem Netz {#a-relay-in-your-network}

1. Wählen Sie **Ohne Anmeldung**. Das Relay muss Mail von der Adresse dieses Servers annehmen und weiterleiten.
2. Das Relay muss STARTTLS oder TLS anbieten. Die Empfangsconnectors von Exchange Server tun das; ist ihr
   Zertifikat selbst signiert, fügen Sie es unter **Vertrauenswürdige Zertifikate** ein.

## Die Einträge Ihrer Domain {#your-domains-records}

Der Mailserver signiert und verschickt die Mail, also betreffen die SPF-, DKIM- und DMARC-Einträge Ihrer Domain
ihn, nicht Fadenstack. Landet Mail im Spam, prüfen Sie diese Einträge für den Mailserver, den Sie hier
eingerichtet haben.

## Was die Seite zeigt {#what-the-page-shows}

**Mail-Status** oben sagt, ob Mail hinausgeht, und wenn nicht, warum: Das Mail-Modul ist aus, kein Server ist
eingetragen oder die Konsolenadresse fehlt. Er zeigt, wie viele Mails warten, wann die letzte verschickt wurde, und
den letzten Fehler: welche Mail, den Grund in Worten, die Antwort des Mailservers, wie oft es versucht wurde und ob
es weiter versucht wird. Mails werden eine Weile erneut versucht und ungesendet gelöscht, sobald sie nicht mehr
nützen (ein abgelaufener Link).

Scheitert Mail, zeigt jede Seite der Konsole einen Hinweis, bis wieder eine Mail hinausgeht.

## Wenn es nicht funktioniert {#when-it-does-not-work}

Die Testmail und der Mail-Status sagen, was schiefging, und zeigen darunter die Antwort des Mailservers.

| Sie sehen | Was zu tun ist |
|---|---|
| Dem Zertifikat des Mailservers wird nicht vertraut | Fügen Sie die CA Ihrer Organisation unter **Vertrauenswürdige Zertifikate** ein. |
| Der Mail-Dienst lehnt diese CA ab | Fügen Sie stattdessen das eigene Zertifikat des Mailservers ein oder lassen Sie die CA mit Schlüsselverwendung neu ausstellen. |
| Das Zertifikat des Mailservers gilt für einen anderen Namen | Tragen Sie den Server unter dem Namen ein, auf den sein Zertifikat ausgestellt ist. |
| Der Mailserver hat nicht rechtzeitig geantwortet | Prüfen Sie den Port: STARTTLS auf 587, TLS von Anfang an auf 465. Prüfen Sie, ob eine Firewall die Verbindung verwirft. |
| Die verschlüsselte Verbindung ist gescheitert | Verschlüsselung und Port passen nicht zusammen: STARTTLS auf 587, TLS von Anfang an auf 465. |
| Der Mailserver bietet auf diesem Port keine Verschlüsselung an | Verwenden Sie TLS von Anfang an auf 465 oder fragen Sie nach STARTTLS auf 587. Fadenstack sendet nie unverschlüsselt. |
| Keine Verbindung zum Mailserver | Prüfen Sie Server und Port und ob Firewalls diesen Server durchlassen. |
| Der Name des Servers lässt sich nicht auflösen | Prüfen Sie die Schreibweise und ob das DNS dieses Servers den Namen kennt. |
| Der Mailserver hat die Anmeldung abgelehnt | Prüfen Sie Benutzername und Passwort. Bei Microsoft 365: Berechtigung, Zustimmung und Postfachberechtigung der App (Schritte 3 und 4). |
| Microsoft 365 hat kein Token ausgestellt | Prüfen Sie Mandant, Anwendungs-ID und geheimen Clientschlüssel (seinen Wert, nicht seine ID). Der Grund von Entra steht darunter. |
| Der Mailserver hat die Absenderadresse abgelehnt | Das Konto muss als Absenderadresse senden dürfen: Verwenden Sie seine eigene Adresse oder geben Sie ihm das Recht. |
| Der Mailserver leitet für diesen Server keine Mail weiter | Melden Sie sich an oder lassen Sie das Relay die Adresse dieses Servers annehmen. |
| Der Mailserver verlangt eine Anmeldung | Wählen Sie eine Anmeldung und tragen Sie das Konto ein. |
| Der Mailserver hat die Begrüßung dieses Servers abgelehnt | Tragen Sie als **Begrüßungsname** einen vollständigen DNS-Namen ein, z. B. `ai.example.internal`. |
| Das gespeicherte Passwort oder der geheime Schlüssel gilt für einen anderen Server | Geben Sie ihn für diesen Server erneut ein und speichern Sie. |
| Der Mail-Dienst ist nicht erreichbar | Starten Sie das Mail-Modul unter **Einstellungen → Module**. |
