# Der Browser-Agent {#the-browser-agent}

Der Browser-Agent ist Fadenstack in der Seitenleiste von Chrome. Er arbeitet mit der Seite, die Sie geöffnet haben: Er beantwortet Fragen dazu, fasst sie zusammen und übersetzt sie, zeigt Ihnen, wo etwas steht, kopiert Tabellen heraus, vergleicht Tabs, liest PDFs, sieht sich Screenshots an und schreibt in die E-Mail oder das Ticket, an dem Sie gerade arbeiten. Er antwortet mit dem Modell Ihrer Organisation, über Ihren Fadenstack-Server, und im Tool-Modus **Nachfragen**, mit dem jeder Chat beginnt, fragt er, bevor er etwas ändert.

![Die Seitenleiste des Browser-Agenten: vorgeschlagene Fragen, die Leiste mit der geöffneten Seite und das Nachrichtenfeld mit seinen Schaltflächen darunter](../images/use/browser-agent-panel.png)

## Bevor Sie beginnen {#before-you-start}

- **Die Erweiterung** ist in Chrome installiert. Ihre IT-Abteilung installiert sie für Sie oder sagt Ihnen, wo Sie sie bekommen.
- **Zugriff**: Ihr Administrator hat Ihnen den Browser-Agenten freigegeben. Siehe [Agenten und Add-ins](../administer/agents.md).
- **Die Adresse des Servers und der Name des Agenten**, sofern Ihre IT-Abteilung sie nicht für Sie eingestellt hat.

## Seitenleiste öffnen und anmelden {#open-the-panel-and-sign-in}

1. Wählen Sie die Fadenstack-Schaltfläche in der Symbolleiste von Chrome, oder drücken Sie Alt+Umschalt+F.
2. Geben Sie beim ersten Mal in **Mit Fadenstack verbinden** den **Server** und den **Agent** ein.
3. Wählen Sie **Verbinden**, und erlauben Sie den Zugriff, wenn Chrome danach fragt.
4. Geben Sie unter **E-Mail** und **Passwort** die Zugangsdaten Ihres Fadenstack-Kontos ein.
5. Wählen Sie **Anmelden**.

Wenn Ihre IT-Abteilung Server und Agent für Sie eingestellt hat, zeigt das Formular beide an; Sie wählen nur **Verbinden** und melden sich an.

??? info "Server und Agent"
    - **Server**: Ihr Fadenstack-Server, z. B. `https://ai.example.internal`.
    - **Agent**: seine Adresse, wie die Konsole sie unter **Agenten** zeigt; `browser-assistant`, sofern man Ihnen nichts anderes gesagt hat.

## Sprache und Einstellungen {#language-and-settings}

Die Seitenleiste zeigt sich in der Sprache Ihres Browsers. Um eine andere zu wählen, wählen Sie oben in der Seitenleiste das Zahnrad (**Einstellungen**) und dann unter **Sprache** eine Sprache: Die ganze Seitenleiste wechselt sofort, auch die Unterhaltung, die Begrüßung des Agenten und die Einträge im Kontextmenü. **Automatisch** kehrt zur Sprache des Browsers zurück. Die Sprache wird für Ihr Konto gespeichert, sodass die Konsole und die Office-Add-ins ihr folgen, auf diesem und auf Ihren anderen Computern. Hat Ihre Organisation eine Sprache festgelegt, zeigen die Einstellungen sie mit **Von Ihrer Organisation festgelegt** an, und Sie können sie nicht ändern.

Unter den Sprachen zeigen die **Einstellungen**, welchen Agenten Sie auf welchem Server nutzen, mit **Auf diesem Gerät abmelden** und **Server oder Agent ändern**.

**Info**, der letzte Eintrag unter dem Zahnrad, nennt den Agenten und seine Version, das Modell, die Erweiterung, das Fadenstack-SDK, mit dem er gebaut ist, und den Server; **Details kopieren** kopiert sie für eine Frage an Ihre IT-Abteilung. Er verlinkt auf die Hilfe Ihrer Organisation und auf fadenstack.com. Unter dem Nachrichtenfeld steht außerdem ein kleiner Link **Fadenstack** (was ein solcher Link sendet: [Ihre Daten und Datenschutz](privacy.md#links-to-fadenstackcom)).

## Fragen zu einer Seite stellen {#ask-about-a-page}

Geben Sie Ihre Frage in der Seitenleiste ein, z. B. „Fasse diese Seite zusammen“ oder „Was sagt dieser Vertrag über Kündigungsfristen?“. Die Leiste über dem Nachrichtenfeld nennt die Seite, mit der der Agent arbeitet.

??? info "Was der Agent auf einer Seite liest"
    Der Agent liest die Seite selbst, wenn Sie danach fragen: ihren Text, ihre Überschriften, Listen und Tabellen, also den sichtbaren Inhalt, ohne Menüs, Fußzeilen und Seitenleisten und ohne Text, den die Seite als verborgen markiert. Eine lange Seite liest er in Teilen. Teile der Seite, die von ihrer eigenen Website in einem Rahmen (Frame) kommen, etwa ein Editor oder ein eingebettetes Formular, liest er an ihrer Stelle mit; einen Rahmen einer anderen Website liest er, wenn Sie auch diese Website erlaubt haben.

Die Schaltflächen unter dem Nachrichtenfeld fügen Ihrer nächsten Nachricht etwas hinzu:

| Schaltfläche | Fügt hinzu |
|---|---|
| **Diese Seite hinzufügen** (die Seite) | Die Seite, auf der Sie sind |
| **Die Auswahl hinzufügen** (das Anführungszeichen) | Was Sie auf der Seite markiert haben |
| **Tabs hinzufügen** (der Tab) | Tabs dieses Fensters, die Sie auswählen, um sie zu vergleichen (**Alle lesbaren Tabs** nimmt alle) |
| **Ein PDF von diesem Computer hinzufügen** (die Büroklammer) | Eine PDF-Datei; Sie können sie auch auf die Seitenleiste ziehen. Mit einem Modell, das Bilder sieht: **Ein PDF oder Bild von diesem Computer hinzufügen** |
| **Einen Screenshot hinzufügen** (die Kamera) | Was die Seite zeigt, als Bild. Nur mit einem Modell, das Bilder sieht |
| **Schnellaktionen** (der Stern) | Siehe [Schnellaktionen](#quick-actions) |

![Add tabs geöffnet: All readable tabs und die zwei Tabs des Fensters zum Ankreuzen, mit Cancel und Add](../images/use/browser-agent-add-tabs.png)

**Neuer Chat** oben in der Seitenleiste beginnt eine neue Unterhaltung; **Chatverlauf** listet Ihre früheren Unterhaltungen in diesem Browser auf.

??? info "Mehr zu Screenshots und Bildern"
    Bilder gehen nur an den Agenten, wenn sein Modell Bilder sieht; sonst fehlt die Kamera, und ein eingefügtes Bild wird nicht hinzugefügt: Die Seitenleiste weist darauf hin. Sie können ein Bild auch in das Nachrichtenfeld einfügen oder auf die Seitenleiste ziehen.

    **Einen Screenshot hinzufügen** nimmt die Seite direkt auf, wenn Sie die Seitenleiste über die Schaltfläche in der Symbolleiste, das Tastenkürzel oder einen Eintrag im Kontextmenü auf dieser Seite geöffnet haben. Sonst fragt Chrome, was geteilt werden soll: Wählen Sie den Tab, ein anderes Fenster oder Ihren Bildschirm. Fadenstack nimmt ein Bild davon auf, und das Teilen endet sofort.

## Schnellaktionen {#quick-actions}

Schnellaktionen fragen den Agenten sofort, in Ihrer Sprache. Sie stehen im Menü **Schnellaktionen** der Seitenleiste und im Kontextmenü unter **Fadenstack**:

| Für | Schnellaktion |
|---|---|
| Die Seite | **Diese Seite zusammenfassen**, **Diese Seite ins … übersetzen**, **Zu dieser Seite fragen**, **Zu einem Screenshot dieser Seite fragen** |
| Markierten Text | **Erklären**, **Zusammenfassen**, **Ins … übersetzen**, **Zu „…“ fragen** |
| Markierten Text in einem Feld oder Editor | **Formulierung verbessern**: Die Fassung des Agenten ersetzt Ihre Markierung, nachdem Sie es erlaubt haben |

**Ins … übersetzen** bietet die Sprache der Seitenleiste und Englisch an. Um mit der Tastatur zu einer Markierung zu fragen, markieren Sie den Text und drücken Sie Alt+Umschalt+S: Die Seitenleiste öffnet sich mit der Markierung.

## Skills {#skills}

Skills sind Arbeitsweisen, die Ihre Organisation eingerichtet hat, etwa „In unserem Stil antworten“ oder „Einen Vertrag gegen unsere Checkliste prüfen“. Um einen für eine Nachricht zu nutzen, geben Sie im Nachrichtenfeld `$` ein (oder `/` am Anfang) und wählen den Skill aus der Liste: Tippen Sie ein paar Buchstaben, um sie einzugrenzen, und drücken Sie die Eingabetaste. Der Skill steht dann über dem Nachrichtenfeld und gilt nur für diese Nachricht. Siehe [Skills](skills.md).

## Websuche {#web-search}

Der Agent kann mit der Suchmaschine von Chrome im Web suchen, also mit der, die in Chrome eingestellt ist. Das ist aus, bis Sie es einschalten: Wählen Sie das Zahnrad (**Einstellungen**) und dann **Mit Chrome im Web suchen**. Hat Ihre Organisation das für Sie entschieden, zeigt der Eintrag **Von Ihrer Organisation festgelegt**.

Im Tool-Modus **Nachfragen** fragt jede Suche zuerst, und die Karte zeigt die Suchanfrage. Die Suche läuft in einem Tab im Hintergrund, und der Agent liest die Ergebnisse wie eine Seite; beim ersten Mal braucht Fadenstack deshalb Ihre Erlaubnis für die Website der Suchmaschine: Wählen Sie **… erlauben** über dem Nachrichtenfeld und fragen Sie dann erneut. Danach öffnet er ein oder zwei Ergebnisse und nennt Ihnen ihre Links.

??? info "Wohin eine Suche geht"
    Die Suchanfrage geht an die in Chrome eingestellte Suchmaschine, nicht über den Server Ihrer Organisation, so wie eine Suche, die Sie in die Adressleiste tippen. Der Agent ist angewiesen, nie personenbezogene Daten oder Text von der Seite in eine Suchanfrage zu schreiben, es sei denn, Sie haben genau darum gebeten; lesen Sie die Suchanfrage auf der Karte, bevor Sie sie erlauben.

## Was er sonst noch kann {#what-else-it-can-do}

| Bitten Sie ihn | Was er tut |
|---|---|
| **zu zeigen, wo** etwas auf der Seite steht | Er markiert die Stelle und scrollt dorthin. In einem PDF öffnet er das PDF auf der richtigen Seite. |
| **eine Tabelle** oder einen Text **zu kopieren** | Der Inhalt landet in Ihrer Zwischenablage, als Text und als Tabelle. So lässt er sich in Excel als Zellen und in Word oder Outlook als Tabelle einfügen. Jede Tabelle in einer Antwort hat außerdem eine Schaltfläche zum Kopieren. |
| **in eine E-Mail, ein Ticket oder ein Formular zu schreiben** | Er füllt Formularfelder aus und schreibt formatierten Text in den Editor auf der Seite, etwa in eine E-Mail in Outlook im Web oder Gmail, in ein Ticket oder eine Notiz. Sagen Sie, wohin: an die Stelle Ihres Cursors, unter die Anrede, ans Ende oder anstelle des gesamten Inhalts. |
| **markierten Text in einem Feld zu verbessern oder zu übersetzen** | Er setzt seine Fassung an die Stelle Ihrer Markierung. |
| **aus den Dokumenten Ihrer Organisation zu antworten** | Er durchsucht das Wissen, das Ihr Administrator dem Agenten gegeben hat, und nennt das Dokument, auf das er sich stützt. Siehe [Wissen](knowledge.md). |
| **PDFs zu lesen** | Er liest PDFs, die in Chrome geöffnet sind, und PDFs, die Sie hinzufügen. Lange PDFs liest er Seite für Seite, so weit nötig. |
| **sich ein Bild anzusehen** | Mit einem Modell, das Bilder sieht: Screenshots und Bilder, die Sie hinzufügen, etwa ein Diagramm oder eine Fehlermeldung. |

## Wann er nachfragt {#what-it-asks-first}

Der **Tool-Modus** oben in der Seitenleiste legt fest, was ohne Nachfrage ausgeführt wird:

| Was | Aus | Nur lesen | Nachfragen (Standard) | Automatisch |
|---|---|---|---|---|
| Seite, Tabs, PDFs und Markierung lesen; suchen; zeigen, wo etwas steht | nein | läuft | läuft | läuft |
| Ein Feld ausfüllen, in einen Editor schreiben, Ihre Markierung ersetzen, in die Zwischenablage kopieren, eine Seite öffnen, im Web suchen | nein | nein | fragt | läuft |
| Eine Schaltfläche drücken oder einem Link folgen | nein | nein | fragt, jedes Mal | fragt, jedes Mal |

![Eine Karte mit der Frage Run copy_to_clipboard?, die die zu kopierende Tabelle zeigt, mit den Schaltflächen Allow, Allow for this chat und Deny](../images/use/browser-agent-approval.png)

Wenn er fragt, zeigt eine Karte **… ausführen?** mit der Aktion und dem, was sie bekommt. Wählen Sie **Zulassen** (dieses eine Mal), **Für diesen Chat zulassen** (bis Sie den Chat schließen oder wieder öffnen), **Immer zulassen** (für diesen Agenten in diesem Browser) oder **Ablehnen**. Eine Schaltfläche zu drücken, und alles, was Ihr Administrator zuerst erfragt haben will, bietet nur **Zulassen** und **Ablehnen**: Er fragt jedes Mal. **Freigaben zurücksetzen** unten im Menü des Tool-Modus nimmt zurück, was Sie für den Chat und für immer erlaubt haben.

??? info "Was die Karte bei einem Feld oder einer Schaltfläche zeigt"
    Bei einem Feld oder einer Schaltfläche ist das, was die Aktion bekommt, ein interner Verweis der Seite, nicht ihre Beschriftung.

Wenn Sie die Seitenleiste schließen, während der Agent antwortet, hält die Antwort an: Der Agent arbeitet in der Seitenleiste. Wenn Sie die Seitenleiste in diesem Fenster wieder öffnen, ist der Chat wieder da, meldet **Die Antwort wurde angehalten, als das Panel geschlossen wurde**, und **Erneut versuchen** fragt noch einmal.

## Was er nie tut {#what-it-never-does}

- Er liest nie den Inhalt einer Website, die Sie nicht erlaubt haben; nur die Titel und Adressen der Tabs dieses Fensters erreichen das Modell. Auf einer solchen Website zeigt die Leiste **Der Agent kann … nicht lesen.**; wählen Sie **Auf dieser Website erlauben** (oder **Alle Websites**), um es zu erlauben. Eine Schnellaktion auf markiertem Text sendet diesen Text, auch von einer Website, die Sie nicht erlaubt haben.
- Er liest Passwortfelder und Kartenfelder, die die Website als solche kennzeichnet, nie aus und füllt sie nie aus. Ein Zahlungsfeld, das die Website nicht kennzeichnet, liest und füllt er wie jedes andere Feld.
- Er drückt nie eine Schaltfläche wie **Senden**, ohne Sie zu fragen, jedes Mal: Keine frühere Antwort gilt dafür.
- Er ist angewiesen, nie Anweisungen von der Seite oder aus einem Bild anzunehmen: Was dort steht, ist etwas zum Lesen, kein Befehl, dem er folgt. Eine Seite kann ein Modell trotzdem in die Irre führen; lesen Sie deshalb, worum eine Karte bittet, bevor Sie es erlauben.
- Er greift nie auf Dateien auf Ihrem Computer zu, die Sie nicht hinzugefügt haben.

## Berechtigungen, die er anfordert {#permissions-it-asks-for}

Bei der Installation erlaubt Chrome der Erweiterung, ihre Seitenleiste zu öffnen, ihre Einstellungen und Ihre Anmeldung zu speichern, ihre Einträge zum Kontextmenü (Rechtsklick) hinzuzufügen, Titel und Adressen Ihrer Tabs zu lesen, mit der Suchmaschine von Chrome zu suchen, wenn Sie die Websuche einschalten, und Seiten zu lesen und darauf zu handeln, aber nur auf Websites, die Sie erlauben. Wenn Sie das Tastenkürzel für die Markierung oder **Zu einem Screenshot dieser Seite fragen** nutzen, lässt Chrome sie dieses eine Mal in diesen Tab. Gesondert fragt sie nach dem Zugriff auf Ihren Fadenstack-Server (wenn Sie **Verbinden** wählen) und nach jeder Website, die Sie erlauben. Sie können die Erlaubnis für jede Website in den Einstellungen von Chrome für die Erweiterung wieder zurücknehmen.

## Wohin Ihre Daten gehen {#where-your-data-goes}

- Was Sie fragen und was der Agent liest, um zu antworten (die Seite, Tabs, PDF-Seiten, Bilder, die Sie hinzufügen), geht an den Server Ihrer Organisation und an das Modell, das Ihr Administrator für den Agenten ausgewählt hat.
- Mit eingeschalteter Websuche geht eine Suche, die Sie erlauben, an die in Chrome eingestellte Suchmaschine.
- PDFs werden in Ihrem Browser gelesen. Bilder werden in Ihrem Browser verkleinert, bevor sie gesendet werden.
- Ihre Unterhaltungen bleiben in diesem Browser. Der Server protokolliert, welche Tools liefen und wie lange sie dauerten, nicht, was sie gelesen haben.
- Ihre Anmeldung wird in diesem Browser gespeichert und ist dort nicht verschlüsselt. Melden Sie sich auf einem Computer, den auch andere nutzen, ab, wenn Sie fertig sind: Wählen Sie oben in der Seitenleiste das Zahnrad (**Einstellungen**), dann **Auf diesem Gerät abmelden**.

Mehr dazu, was gespeichert wird und wer es sieht: [Ihre Daten und Datenschutz](privacy.md).

## Was er nicht lesen kann {#what-it-cannot-read}

| Was | Was passiert |
|---|---|
| Ein Scan (Seiten, die Bilder sind) | Er weist darauf hin. Mit einem Modell, das Bilder sieht, fügen Sie stattdessen einen Screenshot hinzu |
| Ein PDF mit Passwort | Er weist darauf hin: Öffnen Sie es stattdessen in einem PDF-Programm |
| Ein PDF über 50 MB | Er weist darauf hin |
| Ein PDF von Ihrem Computer, in Chrome geöffnet | Fügen Sie es stattdessen mit **Ein PDF von diesem Computer hinzufügen** hinzu |
| Eine Markierung in der PDF-Anzeige von Chrome | Kopieren Sie die Stelle in Ihre Nachricht |
| Ein Teil der Seite von einer Website, die Sie nicht erlaubt haben (ein Rahmen) | Er weist darauf hin. Erlauben Sie diese Website in den Einstellungen von Chrome für die Erweiterung (**Websitezugriff**) |
| Die eigenen Seiten von Chrome und der Chrome Web Store | Chrome lässt Erweiterungen sie nicht lesen |

## Wenn es nicht funktioniert {#when-it-does-not-work}

| Sie sehen | Was Sie tun |
|---|---|
| **… antwortet nicht. Prüfen Sie die Adresse des Servers und ob dieser Computer seinem Zertifikat vertraut.** | Die Adresse ist falsch, oder Ihr Computer vertraut dem Zertifikat des Servers nicht. Prüfen Sie die Adresse, oder wenden Sie sich an Ihre IT-Abteilung. |
| **Auf … gibt es keinen Agenten „…“.** | Der Name unter **Agent** ist falsch. Wählen Sie **Server oder Agent ändern** und geben Sie ihn so ein, wie Ihr Administrator ihn Ihnen genannt hat. |
| **Sie haben keinen Zugriff auf „…“.** | Sie dürfen diesen Agenten noch nicht nutzen. Bitten Sie Ihre Administration, ihn Ihnen oder Ihrem Team freizugeben. |
| **„…“ hat noch keine veröffentlichte Version.** oder **„…“ ist außer Betrieb.** | Der Agent ist nicht verfügbar. Wenden Sie sich an Ihren Administrator. |
| **Ihr Konto ist deaktiviert.** | Wenden Sie sich an Ihren Administrator. |
| **Fadenstack muss den Server erreichen. Erlauben Sie es, wenn Chrome fragt.** | Sie haben die Frage von Chrome abgelehnt. Wählen Sie erneut **Verbinden** und erlauben Sie den Zugriff. |
| **Das Modell dieses Agenten sieht keine Bilder.** | Bilder können nicht an diesen Agenten gehen. Fragen Sie Ihren Administrator, wenn Sie sie brauchen. |
| **Wählen Sie zuerst Text auf der Seite aus.** | Eine Schnellaktion auf einer Markierung hat keine gefunden. Markieren Sie den Text und wählen Sie sie erneut. |
