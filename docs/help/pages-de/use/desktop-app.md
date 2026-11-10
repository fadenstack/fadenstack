# Die Desktop-App {#the-desktop-app}

Die Fadenstack-Desktop-App bringt den Assistenten auf Ihren Computer. Diese Seite zeigt, wie Sie sie bekommen, was
beim ersten Start passiert, wie sie sich aktualisiert, was sie auf Ihrem Computer speichert und wie Sie sie
entfernen.

## Die App besorgen {#get-the-app}

Laden Sie die App vom Fadenstack-Server Ihrer Organisation herunter: Öffnen Sie **Apps** im Menü des Chats. Dort
steht sie, wenn Ihr Administrator die App anbietet. Andernfalls laden Sie sie von der Seite des Desktop-Agenten
auf fadenstack.com herunter (`https://fadenstack.com/agents/desktop/`).

Zu jedem Download steht seine SHA-256-Prüfsumme. Vergleichen Sie sie mit der Datei, die Sie heruntergeladen haben;
unter Windows gibt diese PowerShell-Zeile sie aus:

```
Get-FileHash <file> -Algorithm SHA256
```

| System | Download | Hinweise |
|---|---|---|
| **Windows** 11 (Windows 10 22H2 nach Möglichkeit), x64 | Das Setup, eine Datei mit der Endung `-Setup.exe` | Installiert die App nur für Sie, ohne Administratorrechte, und legt **Fadenstack** im Startmenü an. Es aktualisiert sich selbst. |
| | Das portable Zip | Braucht keine Installation: Entpacken Sie es irgendwo und starten Sie `Faden.Desktop.exe`. Es aktualisiert sich nicht selbst. |
| **Linux** | Ein `.deb`-Paket: Ubuntu 24.04 und 26.04, Debian 13 | Wird über das Software-Werkzeug des Systems oder die Verwaltungswerkzeuge Ihrer Organisation installiert und aktualisiert. |
| | Ein `.rpm`-Paket: Red Hat Enterprise Linux 9 und 10, SUSE Linux Enterprise Desktop 15 SP7 | Ebenso. Die App selbst meldet nur, wenn eine neue Version erschienen ist. |
| **macOS** <span class="fs-planned">Geplant</span> | Noch nicht verfügbar | |

??? info "Mehr zu Linux-Desktops"
    Auf Desktops, die nur Wayland nutzen (Ubuntu 26.04, Red Hat Enterprise Linux 10), ist begrenzt, was die App von
    anderen Fenstern sehen kann.

!!! note "Vorabversionen sind noch nicht codesigniert"
    Die aktuellen Versionen sind Vorabversionen und nicht codesigniert. Windows zeigt eventuell **Der Computer wurde
    durch Windows geschützt**: Wählen Sie **Weitere Informationen** und dann **Trotzdem ausführen**. Manche
    Organisationen sperren nicht signierte Programme: Wenden Sie sich dann an Ihre IT-Abteilung.

## Erster Start {#first-start}

Hat Ihre IT-Abteilung den Server für Sie eingerichtet, kann die App sich gleich anmelden. Andernfalls fragt sie nach
der Adresse Ihres Servers, z. B. `https://ai.example.internal`. Danach melden Sie sich mit einem Code an, den Sie
im Browser genehmigen (siehe [Eine Desktop-App anmelden](sign-in-an-app.md)).

Ohne Server zeigt **Die Demo ohne Server ausprobieren** die App mit einer Beispielunterhaltung.

## Aktualisierungen {#updates}

Die App sucht kurz nach dem Start nach einer neuen Version und danach einmal am Tag. Alles zu Aktualisierungen
finden Sie unter **Einstellungen → Info**.

- **Eine mit dem Setup installierte Kopie** lädt die neue Version im Hintergrund herunter. Dann sagt ein kleiner
  Hinweis neben dem Assistenten „Version … ist bereit", mit **Jetzt neu starten** und **Später**. Er fragt nie,
  während der Assistent antwortet. **Später** hebt die Aktualisierung für den nächsten Start auf; oder öffnen Sie
  **Einstellungen → Info → Updates** und wählen Sie **Neu starten und aktualisieren**. Ihre Chats und
  Einstellungen bleiben.
- **Das portable Zip und die Linux-Pakete** sagen „Version … ist erschienen", mit **Download-Seite**.
- **Ein Sicherheitsupdate** wird als solches benannt („Sicherheitsupdate …").

**Updates** zeigt, wie es steht, und hat **Jetzt prüfen**. **Update-Kanal** bietet zwei Möglichkeiten:
**Vorschau** bekommt neue Versionen zuerst; **Stabil** bekommt sie, wenn sie erprobt sind.

Ihre IT-Abteilung kann die Prüfung ausschalten oder den Kanal festlegen. Dann sagt die Zeile „Ihre IT hat
Updates auf diesem Computer ausgeschaltet", oder der Kanal zeigt **Von Ihrer IT festgelegt**.

Die App installiert nur Versionen, die Fadenstack veröffentlicht hat, unverändert.

| Sie sehen | Was Sie tun |
|---|---|
| Das Update hat die Signaturprüfung nicht bestanden. Es wurde nichts installiert. | Es wurde nichts installiert, Ihre Kopie ist unverändert. Sagen Sie es Ihrer IT-Abteilung. |
| Die Liste der Versionen wurde nicht rechtzeitig erneuert | Neuere Versionen fehlen möglicherweise. Prüfen Sie Ihre Verbindung: Die App versucht es später erneut. |

## Ihre Daten auf diesem Computer {#your-data}

Die Chats, Einstellungen, Anmeldungen und Protokolle, die Fadenstack-Apps für Sie speichern, liegen in Ihren eigenen
Benutzerordnern. Andere Personen, die den Computer nutzen, teilen sie nicht.

| System | Ordner |
|---|---|
| Windows | `%LOCALAPPDATA%\Fadenstack` und `%APPDATA%\Fadenstack` |
| Linux | `~/.local/share/Fadenstack`, `~/.config/Fadenstack` und `~/.local/state/Fadenstack` |

Auch die Office-Add-ins speichern ihre dort. Das Deinstallieren der App lässt sie bestehen, sodass eine neue
Installation Ihre Einstellungen wiederfindet.

So entfernen Sie sie:

1. Ist Word, Excel, PowerPoint oder Outlook geöffnet, schließen Sie es. Die App bittet Sie darum.
2. Öffnen Sie **Einstellungen → Info → Ihre Daten auf diesem Computer** und wählen Sie **Meine Daten entfernen …**.
3. Prüfen Sie die Ordner, die die App auflistet, und wählen Sie **Entfernen und beenden**.

Die App meldet Sie ab, beendet sich und entfernt dann alles, was Fadenstack-Apps für Sie auf diesem Computer
speichern, auch die Chats und Anmeldungen der Office-Assistenten. Konnte etwas nicht entfernt werden, sagt die App
es beim nächsten Start.

!!! warning "Das lässt sich nicht rückgängig machen"
    Ihre Chats, Einstellungen und Anmeldungen auf diesem Computer sind dann endgültig weg.

## Die App entfernen {#remove-the-app}

- **Windows, mit dem Setup installiert:** Öffnen Sie **Einstellungen → Apps → Installierte Apps**, suchen Sie
  **Fadenstack** und wählen Sie **Deinstallieren**.
- **Windows, portables Zip:** Löschen Sie seinen Ordner.
- **Linux:** Entfernen Sie **fadenstack-desktop** mit dem Software-Werkzeug des Systems, oder fragen Sie Ihre
  IT-Abteilung.

Ihre Daten bleiben auf dem Computer (siehe [Ihre Daten auf diesem Computer](#your-data)).
