# Der Browser-Agent {#the-browser-agent}

Der Browser-Agent ist Fadenstack in der Seitenleiste von Chrome. Er arbeitet mit der Seite, die Sie geöffnet haben: Er beantwortet Fragen dazu, zeigt Ihnen, wo etwas steht, kopiert Tabellen heraus, vergleicht Tabs, liest PDFs und schreibt in die E-Mail oder das Ticket, an dem Sie gerade arbeiten. Er antwortet mit dem Modell Ihrer Organisation, über Ihren Fadenstack-Server, und er fragt, bevor er etwas ändert.

## Bevor Sie beginnen {#before-you-start}

- **Die Erweiterung** ist in Chrome installiert. Ihre IT-Abteilung installiert sie für Sie oder sagt Ihnen, wo Sie sie bekommen.
- **Zugriff**: Ihr Administrator hat Ihnen den Browser-Agenten freigegeben. Siehe [Agenten und Add-ins](../administer/agents.md).
- **Die Adresse des Servers und der Name des Agenten**, sofern Ihre IT-Abteilung sie nicht für Sie eingestellt hat.

## Seitenleiste öffnen und anmelden {#open-the-panel-and-sign-in}

1. Wählen Sie die Fadenstack-Schaltfläche in der Symbolleiste von Chrome, oder drücken Sie Alt+Umschalt+F.
2. Beim ersten Mal fragt **Connect to Fadenstack** nach dem **Server** (z. B. `https://ai.example.internal`) und dem **Agent** (seine Adresse, wie die Konsole sie unter **Agenten** zeigt; `browser-assistant`, sofern man Ihnen nichts anderes gesagt hat). Wählen Sie **Connect**, und erlauben Sie den Zugriff, wenn Chrome danach fragt.
3. Geben Sie unter **E-mail** und **Password** die Zugangsdaten Ihres Fadenstack-Kontos ein und wählen Sie **Sign in**.

Wenn Ihre IT-Abteilung Server und Agent für Sie eingestellt hat, melden Sie sich nur noch an.

## Fragen zu einer Seite stellen {#ask-about-a-page}

Geben Sie Ihre Frage in der Seitenleiste ein, z. B. „Fasse diese Seite zusammen“ oder „Was sagt dieser Vertrag über Kündigungsfristen?“. Die Leiste über dem Nachrichtenfeld nennt die Seite, mit der der Agent arbeitet.

![Die Seitenleiste des Browser-Agenten: vorgeschlagene Fragen, die Leiste mit der geöffneten Seite und das Nachrichtenfeld mit seinen vier Schaltflächen darunter](../images/use/browser-agent-panel.png)

Der Agent liest die Seite selbst, wenn Sie danach fragen: ihren Text, ihre Überschriften, Listen und Tabellen, also den sichtbaren Inhalt, ohne Menüs, Werbung oder versteckten Text. Eine lange Seite liest er in Teilen.

Die Schaltflächen unter dem Nachrichtenfeld fügen Ihrer nächsten Nachricht etwas hinzu:

| Schaltfläche | Fügt hinzu |
|---|---|
| **Add this page** | Die Seite, auf der Sie sind |
| **Add the selection** | Was Sie auf der Seite markiert haben |
| **Add tabs** | Tabs dieses Fensters, die Sie auswählen, um sie zu vergleichen (**All readable tabs** nimmt alle) |
| **Add a PDF from this computer** | Eine PDF-Datei; Sie können sie auch auf die Seitenleiste ziehen |

![Add tabs geöffnet: All readable tabs und die zwei Tabs des Fensters zum Ankreuzen, mit Cancel und Add](../images/use/browser-agent-add-tabs.png)

Sie können auch mit der rechten Maustaste auf eine Markierung klicken und **Ask Fadenstack about "…"** wählen, oder mit der rechten Maustaste auf eine Seite klicken und **Ask Fadenstack about this page** wählen.

**New chat** oben in der Seitenleiste beginnt eine neue Unterhaltung; **Chat history** listet Ihre früheren Unterhaltungen in diesem Browser auf.

## Was er sonst noch kann {#what-else-it-can-do}

- **Zeigen, wo etwas steht.** Fragen Sie, wo etwas auf der Seite steht: Der Agent markiert die Stelle und scrollt dorthin. In einem PDF öffnet er das PDF auf der richtigen Seite.
- **Eine Tabelle kopieren.** Bitten Sie ihn, eine Tabelle oder einen Text zu kopieren: Der Inhalt landet in Ihrer Zwischenablage, als Text und als Tabelle. So lässt er sich in Excel als Zellen und in Word oder Outlook als Tabelle einfügen. Jede Tabelle in einer Antwort hat außerdem eine Schaltfläche zum Kopieren.
- **In eine E-Mail, ein Ticket oder ein Formular schreiben.** Der Agent füllt Formularfelder aus und schreibt formatierten Text in den Editor auf der Seite, etwa in eine E-Mail in Outlook im Web oder Gmail, in ein Ticket oder eine Notiz. Sagen Sie, wohin: an die Stelle Ihres Cursors, unter die Anrede, ans Ende oder anstelle des gesamten Inhalts.
- **PDFs lesen**, die in Chrome geöffnet sind, und PDFs, die Sie hinzufügen. Lange PDFs liest er Seite für Seite, so weit nötig.

## Wann er nachfragt {#what-it-asks-first}

Der **Tool mode** oben in der Seitenleiste legt fest, was ohne Nachfrage ausgeführt wird:

| Was | Off | Read only | Ask (Standard) | Auto |
|---|---|---|---|---|
| Seite, Tabs, PDFs und Markierung lesen; suchen; zeigen, wo etwas steht | nein | läuft | läuft | läuft |
| Ein Feld ausfüllen, in einen Editor schreiben, in die Zwischenablage kopieren, eine Seite öffnen | nein | nein | fragt | läuft |
| Eine Schaltfläche drücken oder einem Link folgen | nein | nein | fragt | fragt |

Wenn er fragt, zeigt eine Karte **Run …?** mit dem, was er tun will, und wo. Wählen Sie **Allow**, **Allow for this chat** oder **Deny**.

![Eine Karte mit der Frage Run copy_to_clipboard?, die die zu kopierende Tabelle zeigt, mit den Schaltflächen Allow, Allow for this chat und Deny](../images/use/browser-agent-approval.png)

## Was er nie tut {#what-it-never-does}

- Er liest nie eine Website, die Sie nicht erlaubt haben. Auf einer solchen Website zeigt die Leiste **The agent cannot read …**; wählen Sie **Allow on this site** (oder **All sites**), um es zu erlauben.
- Er liest Passwörter und Zahlungsdaten nie aus und trägt sie nie ein.
- Er drückt nie eine Schaltfläche wie **Senden**, ohne Sie gesondert zu fragen.
- Er nimmt nie Anweisungen von der Seite an. Text auf einer Seite ist etwas zum Lesen, nie ein Befehl, dem er folgt.
- Er greift nie auf Dateien auf Ihrem Computer zu, die Sie nicht hinzugefügt haben.

## Berechtigungen, die er anfordert {#permissions-it-asks-for}

Bei der Installation erlaubt Chrome der Erweiterung, ihre Seitenleiste zu öffnen, ihre Einstellungen und Ihre Anmeldung zu speichern, ihre Einträge zum Kontextmenü (Rechtsklick) hinzuzufügen, Titel und Adresse der Seite zu lesen, auf der Sie sind, und Seiten zu lesen und darauf zu handeln, aber nur auf Websites, die Sie erlauben. Gesondert fragt sie nach dem Zugriff auf Ihren Fadenstack-Server (wenn Sie **Connect** wählen) und nach jeder Website, die Sie erlauben. Sie können die Erlaubnis für jede Website in den Einstellungen von Chrome für die Erweiterung wieder zurücknehmen.

## Wohin Ihre Daten gehen {#where-your-data-goes}

- Was Sie fragen und was der Agent liest, um zu antworten (die Seite, Tabs, PDF-Seiten), geht an den Server Ihrer Organisation und an das Modell, das Ihr Administrator für den Agenten ausgewählt hat.
- PDFs werden in Ihrem Browser gelesen.
- Ihre Unterhaltungen bleiben in diesem Browser. Der Server protokolliert, welche Tools liefen und wie lange sie dauerten, nicht, was sie gelesen haben.
- Ihre Anmeldung wird in diesem Browser gespeichert und ist dort nicht verschlüsselt. Melden Sie sich auf einem Computer, den auch andere nutzen, ab, wenn Sie fertig sind: Wählen Sie das Fadenstack-Logo oben in der Seitenleiste, dann **Sign out on this device**.

Mehr dazu, was gespeichert wird und wer es sieht: [Ihre Daten und Datenschutz](privacy.md).

## Was er nicht lesen kann {#what-it-cannot-read}

| Was | Was passiert |
|---|---|
| Ein Scan (Seiten, die Bilder sind) | Er weist darauf hin |
| Ein PDF mit Passwort | Er weist darauf hin: Öffnen Sie es stattdessen in einem PDF-Programm |
| Ein PDF über 50 MB | Er weist darauf hin |
| Ein PDF von Ihrem Computer, in Chrome geöffnet | Fügen Sie es stattdessen mit **Add a PDF from this computer** hinzu |
| Eine Markierung in der PDF-Anzeige von Chrome | Kopieren Sie die Stelle in Ihre Nachricht |
| Die eigenen Seiten von Chrome und der Chrome Web Store | Chrome lässt Erweiterungen sie nicht lesen |

## Wenn es nicht funktioniert {#when-it-does-not-work}

**… does not answer. Check the server's address, and that this computer trusts its certificate.** Die Adresse ist falsch, oder Ihr Computer vertraut dem Zertifikat des Servers nicht. Prüfen Sie die Adresse, oder wenden Sie sich an Ihre IT-Abteilung.

**There is no agent '…' on …** Der Name unter **Agent** ist falsch. Wählen Sie **Change server or agent** und geben Sie ihn so ein, wie Ihr Administrator ihn Ihnen genannt hat.

**You have no access to '…'. Ask your administrator to grant it to you or your team.** Sie dürfen diesen Agenten noch nicht nutzen.

**'…' has no published version yet.** oder **'…' is retired.** Der Agent ist nicht verfügbar. Wenden Sie sich an Ihren Administrator.

**Your account is deactivated.** Wenden Sie sich an Ihren Administrator.

**Fadenstack needs to reach the server. Allow it when Chrome asks.** Sie haben die Frage von Chrome abgelehnt. Wählen Sie erneut **Connect** und erlauben Sie den Zugriff.
