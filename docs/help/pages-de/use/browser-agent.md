# Der Browser-Agent {#the-browser-agent}

Der Browser-Agent ist Fadenstack in der Seitenleiste von Chrome. Er arbeitet mit der Seite, die Sie geöffnet haben: Er beantwortet Fragen dazu, fasst sie zusammen und übersetzt sie, zeigt Ihnen, wo etwas steht, kopiert Tabellen heraus, vergleicht Tabs, liest PDFs und die Transkripte von Videos, sieht sich Screenshots an, schreibt in die E-Mail oder das Ticket, an dem Sie gerade arbeiten, und geht ein Formular über mehrere Seiten durch. Er antwortet mit dem Modell Ihrer Organisation, über Ihren Fadenstack-Server, und im Tool-Modus **Nachfragen**, mit dem jeder Chat beginnt, fragt er, bevor er etwas ändert.

![Die Seitenleiste des Browser-Agenten: vorgeschlagene Fragen, die Leiste mit der geöffneten Seite und das Nachrichtenfeld mit seinen Schaltflächen darunter](../images/use/browser-agent-panel.png)

## Bevor Sie beginnen {#before-you-start}

- **Die Erweiterung** ist in Chrome installiert. Ihre IT-Abteilung installiert sie für Sie oder sagt Ihnen, wo Sie sie bekommen.
- **Zugriff**: Ihr Administrator hat Ihnen den Browser-Agenten freigegeben. Siehe [Agenten und Add-ins](../administer/agents.md).
- **Die Adresse des Servers und der Name des Agenten**, sofern Ihre IT-Abteilung sie nicht für Sie eingestellt hat.

## Seitenleiste öffnen und anmelden {#open-the-panel-and-sign-in}

1. Wählen Sie die Fadenstack-Schaltfläche in der Symbolleiste von Chrome, oder drücken Sie Alt+Umschalt+F.
2. Geben Sie beim ersten Mal in **Mit Fadenstack verbinden** den **Server** und den **Agent** ein.
3. Wählen Sie **Verbinden**, und erlauben Sie den Zugriff, wenn Chrome danach fragt.
4. Die Seitenleiste zeigt einen Anmeldecode. Wählen Sie **Anmeldeseite öffnen**: Die Seite Ihres Servers öffnet sich in einem neuen Tab, mit dem Code schon eingetragen.
5. Melden Sie sich dort an wie in der Konsole, prüfen Sie, ob der Code übereinstimmt, und genehmigen Sie. Die Seitenleiste geht von selbst zum Chat weiter.

Ihr Passwort bleibt auf der Seite Ihres Servers; die Erweiterung sieht es nie. **Mit einem Passwort anmelden** unter dem Code meldet Sie stattdessen mit **E-Mail** und **Passwort** in der Seitenleiste an (für einen Server, der noch nicht mit einem Code anmelden kann).

Wenn Ihre IT-Abteilung Server und Agent für Sie eingestellt hat, zeigt das Formular beide an; Sie wählen nur **Verbinden** und melden sich an.

??? info "Server und Agent"
    - **Server**: Ihr Fadenstack-Server, z. B. `https://ai.example.internal`.
    - **Agent**: seine Adresse, wie die Konsole sie unter **Agenten** zeigt; `browser-assistant`, sofern man Ihnen nichts anderes gesagt hat.

## Sprache und Einstellungen {#language-and-settings}

Die Seitenleiste zeigt sich in der Sprache Ihres Browsers. Um eine andere zu wählen, wählen Sie oben in der Seitenleiste das Zahnrad (**Einstellungen**) und dann unter **Sprache** eine Sprache: Die ganze Seitenleiste wechselt sofort, auch die Unterhaltung, die Begrüßung des Agenten und die Einträge im Kontextmenü. **Automatisch** kehrt zur Sprache des Browsers zurück. Die Sprache wird für Ihr Konto gespeichert, sodass die Konsole und die Office-Add-ins ihr folgen, auf diesem und auf Ihren anderen Computern. Hat Ihre Organisation eine Sprache festgelegt, zeigen die Einstellungen sie mit **Von Ihrer Organisation festgelegt** an, und Sie können sie nicht ändern.

Die **Einstellungen** enthalten außerdem die Schalter **Mit Chrome im Web suchen** und **Tools nutzen, die Websites anbieten (experimentell)**; **Tools**, das auflistet, was der Agent nutzen kann (siehe [Tools](#tools)); **Chat exportieren**, das den geöffneten Chat als Markdown-Datei speichert; und Ihr Konto: welchen Agenten Sie auf welchem Server nutzen, **Meine Daten exportieren**, **Meine Daten auf diesem Gerät löschen**, **Auf diesem Gerät abmelden** und **Server oder Agent ändern**.

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

## Skills und Ihre eigenen Prompts {#skills-and-your-own-prompts}

Skills sind Arbeitsweisen, die Ihre Organisation eingerichtet hat, etwa „In unserem Stil antworten“ oder „Einen Vertrag gegen unsere Checkliste prüfen“. Um einen für eine Nachricht zu nutzen, geben Sie im Nachrichtenfeld `$` ein (oder `/` am Anfang) und wählen den Skill aus der Liste: Tippen Sie ein paar Buchstaben, um sie einzugrenzen, und drücken Sie die Eingabetaste. Der Skill steht dann über dem Nachrichtenfeld und gilt nur für diese Nachricht. Siehe [Skills](skills.md).

Dieselbe Liste beginnt mit **Meine Prompts**: Fragen, die Sie selbst gespeichert haben. Um eine zu speichern, wählen Sie unter einer gesendeten Nachricht **Als Prompt speichern** (das Lesezeichen). Ein gewählter Prompt setzt seinen Text in das Nachrichtenfeld, wo Sie ihn vor dem Senden ändern können. Das Papierkorb-Symbol neben einem Prompt löscht ihn. Ihre Prompts werden nur in diesem Browser gespeichert, bis zu 100; **Meine Daten exportieren** schließt sie ein, **Meine Daten auf diesem Gerät löschen** löscht sie.

## Videos {#videos}

Auf einer Seite mit einem Video kann der Agent dessen Transkript lesen: Fragen Sie „Fasse dieses Video zusammen“ oder „Was sagt es über die Garantie?“. Er liest die Untertitel des Videos, bei YouTube auch das Transkript, das YouTube anzeigt, jede Zeile mit der Zeit, zu der sie gesagt wird, und nennt die Zeiten. Fragen Sie „Wo sagt es das?“, springt er im Video an diese Stelle; Sie können auch eine Zeit nennen, etwa „Geh zu 3:42“.

??? info "Wenn ein Video kein Transkript hat"
    Ein Video ohne Untertitel und ohne Transkript kann er nicht lesen; er weist darauf hin. Automatisch erzeugte Untertitel können Namen und Zahlen falsch verstehen: Prüfen Sie Wichtiges im Video selbst.

## Aufgaben über mehrere Seiten {#tasks-across-pages}

Der Agent kann eine Aufgabe erledigen, die über mehrere Seiten einer Website geht, etwa ein Formular in Schritten oder eine Suche auf der Website: Er wechselt zu einer anderen Seite derselben Website, geht zurück, scrollt, wartet auf die nächste Seite und drückt in einem Feld die Eingabetaste, Tab, Esc oder die Pfeiltasten. Solange er auf einer Seite handelt, zeigt die Seite einen farbigen Rand und unten **Fadenstack arbeitet auf dieser Seite** mit der Schaltfläche **Stoppen**: **Stoppen** dort hält die Antwort an wie das Stoppen in der Seitenleiste.

Er bleibt auf der Website, auf der der Tab ist. Eine andere Website öffnet er in einem neuen Tab mit **Seite öffnen**, wofür er Ihre Erlaubnis für diese Website braucht, oder er erreicht sie über einen Link, wofür er Sie fragt. Eine Anfrage umfasst höchstens 25 Aktionen auf Seiten; dann sagt Ihnen der Agent, was erledigt ist, und fragt, ob er weitermachen soll. Er kauft und bezahlt nie etwas für Sie, legt keine Konten an und akzeptiert keine Bedingungen: Vor einem solchen Schritt hält er an und übergibt ihn Ihnen.

## Tabellen und Chats als Dateien {#tables-and-chats-as-files}

Jede Tabelle in einer Antwort hat neben ihrer Kopier-Schaltfläche **CSV herunterladen**: die Tabelle als Datei für Excel oder eine andere Tabellenkalkulation, gespeichert dort, wo Chrome Downloads speichert. **Chat exportieren** unter dem Zahnrad speichert den geöffneten Chat als Markdown-Datei, mit Ihren Nachrichten, den Namen dessen, was Sie hinzugefügt haben (nicht dem Inhalt), und den Antworten des Agenten.

??? info "Zellen, die mit = + - @ beginnen"
    In der CSV-Datei beginnt eine solche Zelle mit einem Apostroph, damit eine Tabellenkalkulation sie als Text zeigt, statt sie als Formel auszuführen.

## Tools {#tools}

**Tools** unter dem Zahnrad listet auf, was der Agent nutzen kann. **In dieser App**: die eigenen Tools der Erweiterung, jedes mit dem, was es darf (**Lesen**, **Ändert Daten**, **Nicht rückgängig zu machen**). **Vom Server Ihrer Organisation**: die Tools der angebundenen Dienste Ihrer Organisation, nach Dienst gruppiert, jedes mit **Für diesen Agenten verfügbar**, **Von Ihrer Administration ausgeschaltet** oder **Für diesen Agenten nicht erlaubt**.

## Tools, die Websites anbieten (experimentell) {#tools-that-sites-offer-experimental}

Manche Websites bieten Assistenten eigene Aktionen an (WebMCP), etwa „Bestellung suchen“ auf der Seite eines Shops. Chrome erprobt diesen Standard und bietet ihn nur in manchen Versionen oder auf manchen Websites an. Der Agent nutzt solche Tools nur, wenn Sie sie einschalten: Wählen Sie das Zahnrad und dann **Tools nutzen, die Websites anbieten (experimentell)**. Hat Ihre Organisation das für Sie entschieden, zeigt der Eintrag **Von Ihrer Organisation festgelegt**.

Jede Nutzung eines Tools einer Website fragt Sie zuerst, nur mit **Zulassen** und **Ablehnen**, was auch immer die Website über das Tool sagt: Die Website führt es aus, und es kann dort etwas ändern. Was eine Website über ihre Tools sagt und was sie antworten, behandelt der Agent als Text der Website, nie als Anweisung.

## Websuche {#web-search}

Der Agent kann mit der Suchmaschine von Chrome im Web suchen, also mit der, die in Chrome eingestellt ist. Das ist aus, bis Sie es einschalten: Wählen Sie das Zahnrad (**Einstellungen**) und dann **Mit Chrome im Web suchen**. Hat Ihre Organisation das für Sie entschieden, zeigt der Eintrag **Von Ihrer Organisation festgelegt**.

Im Tool-Modus **Nachfragen** fragt jede Suche zuerst, und die Karte zeigt die Suchanfrage. Die Suche läuft in einem Tab im Hintergrund, und der Agent liest die Ergebnisse wie eine Seite; beim ersten Mal braucht Fadenstack deshalb Ihre Erlaubnis für die Website der Suchmaschine: Wählen Sie **… erlauben** über dem Nachrichtenfeld und fragen Sie dann erneut. Danach öffnet er ein oder zwei Ergebnisse und nennt Ihnen ihre Links.

??? info "Wohin eine Suche geht"
    Die Suchanfrage geht an die in Chrome eingestellte Suchmaschine, nicht über den Server Ihrer Organisation, so wie eine Suche, die Sie in die Adressleiste tippen. Der Agent ist angewiesen, nie personenbezogene Daten oder Text von der Seite in eine Suchanfrage zu schreiben, es sei denn, Sie haben genau darum gebeten; lesen Sie die Suchanfrage auf der Karte, bevor Sie sie erlauben.

## Was er sonst noch kann {#what-else-it-can-do}

| Bitten Sie ihn | Was er tut |
|---|---|
| **zu zeigen, wo** etwas auf der Seite steht | Er markiert die Stelle und scrollt dorthin. In einem PDF öffnet er das PDF auf der richtigen Seite, in einem Video springt er an die Stelle. |
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
| Seite, Tabs, PDFs, das Transkript eines Videos und die Markierung lesen; suchen; zeigen, wo etwas steht; scrollen; auf eine Seite warten | nein | läuft | läuft | läuft |
| Ein Feld ausfüllen, in einen Editor schreiben, Ihre Markierung ersetzen, in die Zwischenablage kopieren, eine Seite öffnen, im Web suchen, zu einer anderen Seite der Website wechseln, zurückgehen | nein | nein | fragt | läuft |
| Eine Schaltfläche drücken oder einem Link folgen, eine Taste drücken (die Eingabetaste kann etwas absenden), ein Tool einer Website nutzen | nein | nein | fragt, jedes Mal | fragt, jedes Mal |

![Eine Karte mit der Frage Run copy_to_clipboard?, die die zu kopierende Tabelle zeigt, mit den Schaltflächen Allow, Allow for this chat und Deny](../images/use/browser-agent-approval.png)

Wenn er fragt, zeigt eine Karte **… ausführen?** mit der Aktion und dem, was sie bekommt, und nennt, worauf sie wirkt, etwa *Schaltfläche „Senden“ auf mail.example.com*, *Eingabetaste in Textfeld „Suche“ auf shop.example drücken* oder *Zu https://shop.example/order/2 wechseln*. Wählen Sie **Zulassen** (dieses eine Mal), **Für diesen Chat zulassen** (bis Sie den Chat schließen oder wieder öffnen), **Immer zulassen** (für diesen Agenten in diesem Browser) oder **Ablehnen**. Eine Schaltfläche oder Taste zu drücken, ein Tool einer Website zu nutzen und alles, was Ihr Administrator zuerst erfragt haben will, bietet nur **Zulassen** und **Ablehnen**: Er fragt jedes Mal. **Freigaben zurücksetzen** unten im Menü des Tool-Modus nimmt zurück, was Sie für den Chat und für immer erlaubt haben.

??? info "Was die Karte bei einem Feld oder einer Schaltfläche zeigt"
    Bei einem Feld oder einer Schaltfläche ist das, was die Aktion bekommt, ein interner Verweis der Seite, nicht ihre Beschriftung.

Wenn Sie die Seitenleiste schließen, während der Agent antwortet, hält die Antwort an: Der Agent arbeitet in der Seitenleiste. Wenn Sie die Seitenleiste in diesem Fenster wieder öffnen, ist der Chat wieder da, meldet **Die Antwort wurde angehalten, als das Panel geschlossen wurde**, und **Erneut versuchen** fragt noch einmal.

## Was er schreibt, ist gekennzeichnet {#what-it-writes-is-marked}

Text, den der Agent in eine Seite schreibt, etwa eine Antwort im Editor einer Mail oder eines Tickets, ist dort, wo die Seite es behält, als mit KI erstellt gekennzeichnet: Der Text trägt eine verborgene Kennzeichnung, und wo die Seite einen Titel dafür anzeigt, erscheint beim Darüberfahren **KI-generiert**. Ihre Organisation bewahrt zu jedem Text einen signierten Nachweis auf, sodass [Inhalte prüfen](checking-content.md) ihn später findet, auch wenn die Wörter aus der Seite kopiert werden. Lassen Sie die Kennzeichnungen stehen, wenn Sie weiterschreiben.

![Die Seitenleiste des Browser-Agenten, nachdem er eine Antwort in den Editor eines Tickets geschrieben hat: die Karte fill_field ausführen? mit Zugelassen beantwortet, fill_field ausgeführt und die beiden Absätze, die er eingefügt hat](../images/use/browser-agent-write-done.png)

## Was er nie tut {#what-it-never-does}

- Er liest nie den Inhalt einer Website, die Sie nicht erlaubt haben; nur die Titel und Adressen der Tabs dieses Fensters erreichen das Modell. Auf einer solchen Website zeigt die Leiste **Der Agent kann … nicht lesen.**; wählen Sie **Auf dieser Website erlauben** (oder **Alle Websites**), um es zu erlauben. Eine Schnellaktion auf markiertem Text sendet diesen Text, auch von einer Website, die Sie nicht erlaubt haben.
- Er liest Passwortfelder und Kartenfelder, die die Website als solche kennzeichnet, nie aus und füllt sie nie aus. Ein Zahlungsfeld, das die Website nicht kennzeichnet, liest und füllt er wie jedes andere Feld.
- Er drückt nie eine Schaltfläche wie **Senden** oder die Eingabetaste in einem Feld, ohne Sie zu fragen, jedes Mal: Keine frühere Antwort gilt dafür.
- Er wechselt in Ihrem Tab nie von selbst zu einer anderen Website, kauft und bezahlt nie etwas für Sie, legt keine Konten an und akzeptiert keine Bedingungen.
- Er ist angewiesen, nie Anweisungen von der Seite oder aus einem Bild anzunehmen: Was dort steht, ist etwas zum Lesen, kein Befehl, dem er folgt. Was eine Seite, ein PDF, ein Transkript oder das Tool einer Website enthält, erreicht das Modell als Text der Seite gekennzeichnet, auf eine Weise, die die Seite nicht fälschen kann, und Text, der Assistenten Anweisungen geben will, wird dem Modell angezeigt; der Agent sagt Ihnen dann, dass die Seite ihm Anweisungen geben will. Eine Seite kann ein Modell trotzdem in die Irre führen; lesen Sie deshalb, worum eine Karte bittet, bevor Sie es erlauben.
- Er greift nie auf Dateien auf Ihrem Computer zu, die Sie nicht hinzugefügt haben.

## Berechtigungen, die er anfordert {#permissions-it-asks-for}

Bei der Installation erlaubt Chrome der Erweiterung, ihre Seitenleiste zu öffnen, ihre Einstellungen und Ihre Anmeldung zu speichern, ihre Einträge zum Kontextmenü (Rechtsklick) hinzuzufügen, Titel und Adressen Ihrer Tabs zu lesen, mit der Suchmaschine von Chrome zu suchen, wenn Sie die Websuche einschalten, und Seiten zu lesen und darauf zu handeln, aber nur auf Websites, die Sie erlauben. Wenn Sie das Tastenkürzel für die Markierung oder **Zu einem Screenshot dieser Seite fragen** nutzen, lässt Chrome sie dieses eine Mal in diesen Tab. Gesondert fragt sie nach dem Zugriff auf Ihren Fadenstack-Server (wenn Sie **Verbinden** wählen) und nach jeder Website, die Sie erlauben. Sie können die Erlaubnis für jede Website in den Einstellungen von Chrome für die Erweiterung wieder zurücknehmen.

## Wohin Ihre Daten gehen {#where-your-data-goes}

- Was Sie fragen und was der Agent liest, um zu antworten (die Seite, Tabs, PDF-Seiten, Bilder, die Sie hinzufügen), geht an den Server Ihrer Organisation und an das Modell, das Ihr Administrator für den Agenten ausgewählt hat.
- Mit eingeschalteter Websuche geht eine Suche, die Sie erlauben, an die in Chrome eingestellte Suchmaschine.
- Die Untertitel eines Videos werden von der Website des Videos geholt, so wie sein Player es tut. Mit eingeschalteten Tools von Websites geht, was ein solches Tool bekommt, an diese Website.
- PDFs werden in Ihrem Browser gelesen. Bilder werden in Ihrem Browser verkleinert, bevor sie gesendet werden. CSV- und Markdown-Dateien, die Sie speichern, bleiben auf Ihrem Computer.
- Ihre Unterhaltungen und gespeicherten Prompts bleiben in diesem Browser. Der Server protokolliert, welche Tools liefen und wie lange sie dauerten, nicht, was sie gelesen haben.
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
| Ein Video ohne Untertitel und ohne Transkript | Er weist darauf hin |
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
