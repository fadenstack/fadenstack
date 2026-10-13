# Office-Add-ins {#office-add-ins}

Die Office-Add-ins bringen einen Fadenstack-Agenten in einen Aufgabenbereich neben Ihrem Dokument in Word, Excel, PowerPoint und Outlook. Der Agent sieht, was Sie markieren, liest das Dokument, wenn er es braucht, und ändert es nur, wenn Sie es erlauben. Jede Antwort kommt vom Modell Ihrer Organisation, über Ihren Fadenstack-Server.

![Ein Word-Dokument mit zwei Kommentaren des Fadenstack-Agenten und daneben der Aufgabenbereich des Agenten, der sagt, was er hinzugefügt hat](../images/use/office-word-comments.png)

## Bevor Sie beginnen {#before-you-start}

- **Windows und Office für den Desktop.** Die Add-ins funktionieren in den Desktopversionen von Word, Excel, PowerPoint und Outlook unter Windows. In Office im Web und auf dem Mac funktionieren sie nicht. Getestet sind sie nur mit den Desktop-Apps von Microsoft 365.
- **Klassisches Outlook.** In Outlook funktioniert das Add-in nur im klassischen Outlook, nicht im neuen Outlook für Windows, das keine Add-ins dieser Art ausführt. Wenn Sie das neue Outlook nutzen, wechseln Sie zum klassischen Outlook zurück, um den Agenten zu verwenden.
- **Von der IT installiert.** Ihre IT-Abteilung installiert die Add-ins und stellt in der Regel die Adresse des Servers für Sie ein.
- **Zugriff.** Ihr Administrator hat Ihnen den Agenten für jede Anwendung freigegeben, die Sie nutzen. Siehe [Agenten und Add-ins](../administer/agents.md).

## Agent öffnen und anmelden {#open-the-agent-and-sign-in}

1. Wählen Sie auf der Registerkarte **Start** (in einer Nachricht, die in einem eigenen Fenster geöffnet ist: auf der Registerkarte **Nachricht**) in der Gruppe **Agent** die Schaltfläche **Agent**. Der Aufgabenbereich öffnet sich neben dem Dokument.
2. Geben Sie beim ersten Mal in **Sign in to your agent** Ihre **E-mail** und Ihr **Password** (Ihr Fadenstack-Konto) ein, und den **Server**, falls die IT ihn nicht eingestellt hat.
3. Wählen Sie **Sign in**.

Eine Anmeldung gilt für Word, Excel, PowerPoint und Outlook auf diesem Computer.

??? info "Mehr zum Aufgabenbereich"
    Jedes Dokumentfenster hat seinen eigenen Aufgabenbereich und seinen eigenen Chat; wenn Sie den Aufgabenbereich ausblenden, bleibt sein Chat erhalten.

## Sprache und Einstellungen {#language-and-settings}

Der Bereich zeigt sich in der Sprache von Office. Um eine andere zu wählen, wählen Sie oben im Bereich das Zahnrad (**Einstellungen**) und dann unter **Sprache** eine Sprache: Der ganze Bereich wechselt sofort, auch die Unterhaltung. **Automatisch** kehrt zur Sprache von Office zurück. Die Auswahl gilt für Word, Excel, PowerPoint und Outlook auf diesem Computer und wird für Ihr Konto gespeichert, sodass die Konsole und der Browser-Agent ihr folgen. Hat Ihre Organisation eine Sprache festgelegt, zeigen die Einstellungen sie mit **Von Ihrer Organisation festgelegt** an, und Sie können sie nicht ändern. Die eigenen Texte des Bereichs, etwa die Anmeldung, sind vorerst auf Englisch.

## Dem Agenten Kontext geben {#give-the-agent-context}

![Der Aufgabenbereich des Agenten in Excel: vorgeschlagene Anfragen, die markierten Zellen als Chip im Nachrichtenfeld und darunter die Schaltfläche Context](../images/use/office-excel-pane.png)

- **Ihre Markierung.** Was Sie markieren, wird als Chip (**Current selection**) im Nachrichtenfeld angeboten. Der Chip folgt Ihrer Markierung, und Sie können ihn ausschalten. Sein Inhalt wird beim Senden gelesen.
- **Kontext.** Die Schaltfläche **Context** listet die Teile des Dokuments auf (Blätter, Tabellen, Abschnitte, Folien, Nachrichten), die Sie Ihrer Nachricht hinzufügen können.
- **Ask about selection** im Menüband oder **Ask the agent about this** und **Add to agent chat** im Kontextmenü (Rechtsklick) fügen die Markierung dem Chat hinzu. In Outlook bietet eine Nachricht, die in einem eigenen Fenster geöffnet ist, **Ask about this message**.
- Der Agent kann das Dokument auch selbst lesen, wenn Ihre Frage es erfordert. Ein langes Dokument liest er in Teilen.

## Was er in jeder Anwendung kann {#what-it-can-do-in-each-application}

| Anwendung | Er liest | Er kann ändern (fragt vorher in **Ask**) | Er fragt jedes Mal, bevor er |
|---|---|---|---|
| **Word** | Das Dokument, seine Abschnitte, Tabellen und Kommentare, Ihre Markierung | Text einfügen oder ersetzen, Kommentare hinzufügen, Formatvorlagen anwenden, Tabellen einfügen | Text löscht |
| **Excel** | Die Arbeitsmappe, Blätter, Tabellen, Diagramme, benannte Bereiche, jeden Bereich, mit Formeln | Zellen beschreiben, Blätter hinzufügen, Zellen formatieren, Tabellen und Diagramme erstellen | Zellen leert, ein Blatt löscht |
| **PowerPoint** | Die Präsentation, Folien, Formen und ihren Text, Tabellen, Alternativtext von Bildern, Sprechernotizen | Folien hinzufügen, Folientext und Sprechernotizen festlegen, Textfelder hinzufügen, Folien verschieben | Eine Folie löscht |
| **Outlook** | Die markierten Nachrichten oder die geöffnete, ihre Unterhaltung, den angezeigten Ordner (bei einem Kalender: seine nächsten 14 Tage), den Entwurf, den Sie gerade schreiben; außerdem kann er Ihren Posteingang und „Gesendete Elemente“ durchsuchen | Eine Antwort oder eine neue E-Mail entwerfen, Text in einen Entwurf einfügen, einen Termin erstellen, Kategorien zuweisen, eine Nachricht kennzeichnen oder verschieben | Eine Nachricht löscht (nach „Gelöschte Elemente“) |

??? info "Eine Änderung zurücknehmen"
    | Anwendung | Eine Änderung zurücknehmen |
    |---|---|
    | **Word** | Rückgängig in Word selbst. Standardmäßig ist jede Änderung außerdem eine nachverfolgte Änderung, die Sie annehmen oder ablehnen. Kommentare stammen von „Fadenstack agent“ |
    | **Excel** | **Undo agent change** im Menüband, eine Änderung nach der anderen. Formatierungsänderungen und ein gelöschtes Blatt sind nicht abgedeckt |
    | **PowerPoint** | Rückgängig in PowerPoint selbst, eine Änderung nach der anderen |
    | **Outlook** | **Undo agent change** im Menüband: Ein Entwurf oder Termin wird gelöscht, ein Verschieben oder Löschen wird rückgängig gemacht, Kategorien und Kennzeichnungen werden wiederhergestellt |

![Der Aufgabenbereich des Agenten in Outlook, nachdem er eine Antwort entworfen hat: was im Entwurf steht und dass er nichts gesendet hat](../images/use/office-outlook-draft-reply.png)

!!! note "Der Agent sendet nie"
    In Outlook schreibt der Agent Entwürfe und öffnet sie für Sie; senden müssen Sie selbst. Ein Termin, den er erstellt, wird in Ihrem Kalender gespeichert und geöffnet; eine Besprechung mit Teilnehmern wartet in ihrem Fenster darauf, dass Sie sie senden.

## Wann er nachfragt {#what-it-asks-first}

Jeder Chat beginnt im Tool-Modus **Ask**: Der Agent liest, ohne zu fragen, und fragt, bevor er etwas ändert.

![Eine Karte Run add_comment? in Word mit dem Kommentar, den der Agent hinzufügen will, und Allow, Allow for this chat und Deny](../images/use/office-word-approval.png)

Wenn er fragt, zeigt eine Karte **Run …?** mit dem, was er tun will. Wählen Sie **Allow** (dieses eine Mal), **Allow for this chat** (bis Sie den Chat schließen oder wieder öffnen), **Allow always** (für diesen Agenten auf diesem Computer) oder **Deny**. Was sich nicht rückgängig machen lässt, und alles, was Ihr Administrator zuerst erfragt haben will, bietet nur **Allow** und **Deny**: Er fragt jedes Mal.

Den Modus ändern Sie im Aufgabenbereich: **Off**, **Read only**, **Ask** oder **Auto**. Auch in **Auto** fragt der Agent, bevor er etwas löscht oder leert. Mehr zu den Tool-Modi: [Tools im Chat](tools.md#choose-the-tool-mode).

## Wohin Ihre Daten gehen {#where-your-data-goes}

- Was Sie fragen und die Teile des Dokuments, die der Agent liest, um zu antworten, gehen an den Server Ihrer Organisation und an das Modell, das Ihr Administrator für den Agenten ausgewählt hat.
- Ihre Unterhaltungen bleiben verschlüsselt auf Ihrem Computer. Der Server protokolliert, welche Tools liefen, nicht, was sie gelesen haben.
- Ihre Anmeldung wird auf Ihrem Computer gespeichert, geschützt durch Windows.

Mehr dazu: [Ihre Daten und Datenschutz](privacy.md).

## Abmelden {#sign-out}

1. Wählen Sie im Menüband **More**.
2. Wählen Sie **Sign out**.

Damit melden Sie sich auf diesem Computer in allen vier Anwendungen ab.

## Wenn es nicht funktioniert {#when-it-does-not-work}

| Sie sehen | Was Sie tun |
|---|---|
| Die Schaltfläche **Agent** ist ausgegraut | Das Dokument ist in der geschützten Ansicht geöffnet. Wählen Sie **Bearbeitung aktivieren**, dann **Agent**. |
| In Outlook gibt es keine Gruppe **Agent** | Sie verwenden das neue Outlook für Windows. Wechseln Sie zum klassischen Outlook zurück. |
| **The e-mail or password is not right.** | Prüfen Sie beides und melden Sie sich erneut an. Passwort vergessen? Siehe [Ihr Konto](account.md#forgot-your-password). |
| **You have no access to this agent.** | Ihr Administrator hat ihn Ihnen noch nicht freigegeben, oder die für diese Anwendung eingestellte Adresse des Agenten ist falsch. |
| **Office is busy (a cell may be in edit mode, or a dialog is open). Finish that and try again.** | Verlassen Sie die Zelle oder schließen Sie das Dialogfeld, und fragen Sie erneut. |
| **The agent could not start**, mit einer Begründung | Wählen Sie **Try again**. Wenn es weiterhin fehlschlägt, teilen Sie Ihrer IT-Abteilung mit, was in der Begründung steht. |
