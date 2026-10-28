# Fadenstack verwalten {#administering-fadenstack}

Als Administrator richten Sie Fadenstack in der Konsole für Ihre Organisation ein: Sie wählen aus, welche
Modelle die Benutzer verwenden können und wo diese laufen, legen die Benutzer an, binden Tools an und bestimmen
die Regeln für personenbezogene Daten. Dieser Abschnitt führt durch jede dieser Aufgaben.

![Die Konsole: links das Menü, rechts das Dashboard, und in der oberen Leiste das Fragezeichen, das das Menü „Hilfe“ öffnet](../images/administer/dashboard.png)

## Bevor Sie beginnen {#before-you-start}

- Fadenstack ist installiert, und Sie können sich an der Konsole anmelden, z. B. unter
  `https://ai.example.internal/admin`. Die Installation ist unter [Installation](../operate/installing.md) beschrieben.
- Ihr Konto ist als **Superuser** eingerichtet. Die Konsole ist nur für Superuser: Alle anderen landen nach der
  Anmeldung im Chat. Siehe [Benutzer, Gruppen und Rollen](users.md).

## Sich zurechtfinden {#find-your-way-around}

Das Menü **Hilfe** (das Fragezeichen in der oberen Leiste) enthält:

| Eintrag | Was er tut |
|---|---|
| **Erste Schritte** | Eine Liste auf dem [Dashboard](dashboard.md), die Sie von einer frischen Installation bis dahin führt, dass die Benutzer damit arbeiten. |
| **Rundgang durch die Konsole** | Zeigt Ihnen in einer Minute, wo was zu finden ist. |
| **Über diese Seite** (oder ++f1++) | Erklärt die Seite, auf der Sie gerade sind, und was Sie dort tun können. |
| **Anleitungen** | Führen Sie auf den echten Seiten durch eine Aufgabe: Sie klicken selbst, die Anleitung zeigt den Weg. |
| **Über Fadenstack** | Die Edition, das Release, das dieser Server ausführt, und seine Lizenz, mit Links zu den Versionshinweisen, zur Hilfe, zu den Hinweisen zu Drittanbietern, zur Sicherheit und zur Entwicklerdokumentation, und **Details kopieren** für eine Support-Anfrage. Das Release steht auch unten im Menü links; ein Klick darauf öffnet dasselbe. |

![Die Liste „Erste Schritte“ auf dem Dashboard: fünf Schritte, vier davon abgehakt, jeder mit „Öffnen“, und „Ausblenden“ oben rechts](../images/administer/getting-started.png)

Jeder Schritt unter **Erste Schritte** hakt sich selbst ab, sobald der Server hat, was er dafür braucht; Sie
können die Schritte also in beliebiger Reihenfolge erledigen. **Zeig mir wie** startet eine Anleitung für den
Schritt; **Ausblenden** entfernt die Liste vom Dashboard. Sie bleibt unter **Hilfe** erreichbar.

??? info "Welche Anleitungen es gibt"
    Es gibt Anleitungen zum Hinzufügen einer Maschine, zum Erstellen eines Clusters, zum Bereitstellen eines
    Modells, zum Anbinden eines Cloud-Modells, zum Anlegen von Benutzern und zum Erstellen eines API-Tokens.

## Die ersten Schritte, der Reihe nach {#first-steps-in-order}

1. **Verbinden Sie Ihre GPU-Maschinen und fassen Sie sie zu einem Cluster zusammen.** Eine Maschine kommt hinzu,
   indem auf ihr ein Befehl ausgeführt und sie unter **KI-Infrastruktur → Maschinen** genehmigt wird. Erstellen
   Sie dann unter **Cluster** einen Cluster. Den Teil auf der Kommandozeile übernimmt, wer die Maschinen
   betreibt: siehe [GPU-Maschinen](../operate/machines.md) und [Cluster](../operate/clusters.md). Wenn Sie keine
   eigenen Maschinen verwenden, fahren Sie mit Schritt 3 fort.
2. **Bieten Sie ein Modell an.** Wählen Sie eines im [Modell-Marktplatz](models.md) aus, sehen Sie nach, ob es
   auf Ihren Cluster passt, und stellen Sie es bereit. Die Seite der [Bereitstellung](deployments.md) zeigt, wie
   weit sie ist. Öffnen Sie dann den Chat und probieren Sie das Modell so aus, wie Ihre Kollegen es tun werden.
3. **Binden Sie ein Cloud-Modell an**, wenn Sie eines neben Ihren eigenen möchten: siehe
   [Externe Provider](providers.md). Prüfen Sie vorher,
   [was die Organisation verlässt](providers.md#what-leaves-the-organisation).
4. **Legen Sie die Benutzer an.** Erstellen Sie ihre Konten, und vergeben Sie Rollen und Gruppen, wo jemand mehr
   als den Chat braucht. Siehe [Benutzer, Gruppen und Rollen](users.md).
5. **Fügen Sie Tools, Skills und Agenten hinzu**, wenn Sie sie brauchen: [MCP-Server](mcp-servers.md) geben
   Chats Tools, [Skills](skills.md) sind Anweisungen, die Benutzer wiederverwenden, und [Agenten](agents.md)
   bringen den Assistenten in Chrome und Microsoft Office.
6. **Legen Sie die Regeln fest.** Entscheiden Sie, welche personenbezogenen Daten maskiert werden, bevor sie ein
   Modell erreichen, und welche Tools ohne Rückfrage laufen dürfen: siehe
   [Richtlinien und personenbezogene Daten](policies.md).

Danach zeigt Ihnen das [Dashboard](dashboard.md), was Aufmerksamkeit braucht, und [Nutzung und Limits](usage.md)
zeigt, wie viel genutzt wird, nach Team, Agent und Modell. [Was mit einer Anfrage geschieht](requests.md) erklärt in einfachen
Worten, was Fadenstack mit jeder Nachricht macht, die Benutzer senden.

## Module {#modules}

Einige Teile von Fadenstack sind optionale Module: **PII / Datenschutz**, **MCP** und **Skills**. Wer den Server
betreibt, fügt sie der Installation hinzu (siehe [Installation](../operate/installing.md#optional-modules)).
Danach lassen sie sich unter **Einstellungen → Module** ein- und ausschalten; das Ausschalten stoppt ein Modul,
und Anfragen, die gerade darüber laufen, werden abgebrochen. Ihre Seiten und ihre Schritte unter
**Erste Schritte** erscheinen nur, solange das Modul eingeschaltet ist.

**Herkunftsnachweis** ist von Anfang an eingeschaltet: Es kennzeichnet erzeugte Inhalte als KI-generiert, wie es
die KI-Verordnung verlangt (Art. 50 Abs. 2). Beim Ausschalten erklärt Fadenstack, was dann nicht mehr
gekennzeichnet wird, fragt nach einer Bestätigung und hält es mit Ihrem Namen und der Uhrzeit im Audit-Protokoll
fest. Was es kennzeichnet und die öffentliche Prüfseite stehen unter
[Kennzeichnung KI-generierter Inhalte](content-marking.md).
