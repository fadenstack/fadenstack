# Office-Add-ins {#office-add-ins}

Die Office-Add-ins bringen einen Fadenstack-Agenten in einen Aufgabenbereich neben Ihrem Dokument in Word, Excel, PowerPoint und Outlook. Der Agent sieht, was Sie markieren, liest das Dokument, wenn er es braucht, und ändert es nur, wenn Sie es erlauben. Jede Antwort kommt vom Modell Ihrer Organisation, über Ihren Fadenstack-Server.

## Bevor Sie beginnen {#before-you-start}

- **Windows und Office für den Desktop.** Die Add-ins funktionieren in den Desktopversionen von Word, Excel, PowerPoint und Outlook unter Windows. In Office im Web und auf dem Mac funktionieren sie nicht.
- **Klassisches Outlook.** In Outlook funktioniert das Add-in nur im klassischen Outlook, nicht im neuen Outlook für Windows, das keine Add-ins dieser Art ausführt. Wenn Sie das neue Outlook nutzen, wechseln Sie zum klassischen Outlook zurück, um den Agenten zu verwenden.
- **Von der IT installiert.** Ihre IT-Abteilung installiert die Add-ins und stellt in der Regel die Adresse des Servers für Sie ein.
- **Zugriff.** Ihr Administrator hat Ihnen den Agenten für jede Anwendung freigegeben, die Sie nutzen. Siehe [Agenten und Add-ins](../administer/agents.md).

## Agent öffnen und anmelden {#open-the-agent-and-sign-in}

1. Wählen Sie auf der Registerkarte **Start** (in einer Nachricht, die in einem eigenen Fenster geöffnet ist: auf der Registerkarte **Nachricht**) in der Gruppe **Agent** die Schaltfläche **Agent**. Der Aufgabenbereich öffnet sich neben dem Dokument.
2. Beim ersten Mal fragt **Sign in to your agent** nach **E-mail** und **Password** (Ihr Fadenstack-Konto) und nach dem **Server**, falls die IT ihn nicht eingestellt hat. Wählen Sie **Sign in**.

Eine Anmeldung gilt für Word, Excel, PowerPoint und Outlook auf diesem Computer. Jedes Dokumentfenster hat seinen eigenen Aufgabenbereich und seinen eigenen Chat; wenn Sie den Aufgabenbereich ausblenden, bleibt sein Chat erhalten.

## Dem Agenten Kontext geben {#give-the-agent-context}

- **Ihre Markierung.** Was Sie markieren, wird als Chip (**Current selection**) im Nachrichtenfeld angeboten. Der Chip folgt Ihrer Markierung, und Sie können ihn ausschalten. Sein Inhalt wird beim Senden gelesen.
- **Kontext.** Die Schaltfläche **Context** listet die Teile des Dokuments auf (Blätter, Tabellen, Abschnitte, Folien, Nachrichten), die Sie Ihrer Nachricht hinzufügen können.
- **Ask about selection** im Menüband oder **Ask the agent about this** und **Add to agent chat** im Kontextmenü (Rechtsklick) fügen die Markierung dem Chat hinzu. In Outlook bietet eine Nachricht, die in einem eigenen Fenster geöffnet ist, **Ask about this message**.
- Der Agent kann das Dokument auch selbst lesen, wenn Ihre Frage es erfordert. Ein langes Dokument liest er in Teilen.

![Der Aufgabenbereich des Agenten in Excel: vorgeschlagene Anfragen, die markierten Zellen als Chip im Nachrichtenfeld und darunter die Schaltfläche Context](../images/use/office-excel-pane.png)

## Was er in jeder Anwendung kann {#what-it-can-do-in-each-application}

| Anwendung | Er liest | Er kann ändern (fragt vorher in **Ask**) | Er fragt jedes Mal, bevor er | Eine Änderung zurücknehmen |
|---|---|---|---|---|
| **Word** | Das Dokument, seine Abschnitte, Tabellen und Kommentare, Ihre Markierung | Text einfügen oder ersetzen, Kommentare hinzufügen, Formatvorlagen anwenden, Tabellen einfügen | Text löscht | Rückgängig in Word selbst. Standardmäßig ist jede Änderung außerdem eine nachverfolgte Änderung, die Sie annehmen oder ablehnen. Kommentare stammen von „Fadenstack agent“ |
| **Excel** | Die Arbeitsmappe, Blätter, Tabellen, Diagramme, benannte Bereiche, jeden Bereich, mit Formeln | Zellen beschreiben, Blätter hinzufügen, Zellen formatieren, Tabellen und Diagramme erstellen | Zellen leert, ein Blatt löscht | **Undo agent change** im Menüband, eine Änderung nach der anderen. Formatierungsänderungen sind nicht abgedeckt |
| **PowerPoint** | Die Präsentation, Folien, Formen und ihren Text, Tabellen, Alternativtext von Bildern, Sprechernotizen | Folien hinzufügen, Folientext und Sprechernotizen festlegen, Textfelder hinzufügen, Folien verschieben | Eine Folie löscht | Rückgängig in PowerPoint selbst, eine Änderung nach der anderen |
| **Outlook** | Die markierten Nachrichten oder die geöffnete, ihre Unterhaltung, den angezeigten Ordner, die kommenden Tage Ihres Kalenders, den Entwurf, den Sie gerade schreiben | Eine Antwort oder eine neue E-Mail entwerfen, Text in einen Entwurf einfügen, einen Termin erstellen, Kategorien zuweisen, eine Nachricht kennzeichnen oder verschieben | Eine Nachricht löscht (nach „Gelöschte Elemente“) | **Undo agent change** im Menüband: Ein Entwurf oder Termin wird gelöscht, ein Verschieben oder Löschen wird rückgängig gemacht, Kategorien und Kennzeichnungen werden wiederhergestellt |

![Ein Word-Dokument mit zwei Kommentaren des Fadenstack-Agenten und daneben der Aufgabenbereich des Agenten, der sagt, was er hinzugefügt hat](../images/use/office-word-comments.png)

!!! note "Der Agent sendet nie"
    In Outlook schreibt der Agent Entwürfe und öffnet sie für Sie; senden müssen Sie selbst. Ein Termin, den er erstellt, wartet in seinem Fenster darauf, dass Sie ihn senden oder speichern.

![Der Aufgabenbereich des Agenten in Outlook, nachdem er eine Antwort entworfen hat: was im Entwurf steht und dass er nichts gesendet hat](../images/use/office-outlook-draft-reply.png)

## Wann er nachfragt {#what-it-asks-first}

Jeder Chat beginnt im Tool-Modus **Ask**: Der Agent liest, ohne zu fragen, und fragt, bevor er etwas ändert. Wenn er fragt, zeigt eine Karte **Run …?** mit dem, was er tun will. Wählen Sie **Allow**, **Allow for this chat** oder **Deny**. Den Modus ändern Sie im Aufgabenbereich: **Off**, **Read only**, **Ask** oder **Auto**. Auch in **Auto** fragt der Agent, bevor er etwas tut, das sich nicht rückgängig machen lässt. Mehr zu den Tool-Modi: [Tools im Chat](tools.md#choose-the-tool-mode).

![Eine Karte Run add_comment? in Word mit dem Kommentar, den der Agent hinzufügen will, und Allow, Allow for this chat und Deny](../images/use/office-word-approval.png)

## Wohin Ihre Daten gehen {#where-your-data-goes}

- Was Sie fragen und die Teile des Dokuments, die der Agent liest, um zu antworten, gehen an den Server Ihrer Organisation und an das Modell, das Ihr Administrator für den Agenten ausgewählt hat.
- Ihre Unterhaltungen bleiben verschlüsselt auf Ihrem Computer. Der Server protokolliert, welche Tools liefen, nicht, was sie gelesen haben.
- Ihre Anmeldung wird auf Ihrem Computer gespeichert, geschützt durch Windows.

Mehr dazu: [Ihre Daten und Datenschutz](privacy.md).

## Abmelden {#sign-out}

Wählen Sie im Menüband **More**, dann **Sign out**. Damit melden Sie sich auf diesem Computer in allen vier Anwendungen ab.

## Wenn es nicht funktioniert {#when-it-does-not-work}

**Die Schaltfläche Agent ist ausgegraut.** Das Dokument ist in der geschützten Ansicht geöffnet. Wählen Sie **Bearbeitung aktivieren**, dann **Agent**.

**In Outlook gibt es keine Gruppe Agent.** Sie verwenden das neue Outlook für Windows. Wechseln Sie zum klassischen Outlook zurück.

**The e-mail or password is not right.** Prüfen Sie beides und melden Sie sich erneut an. Passwort vergessen? Siehe [Ihr Konto](account.md#forgot-your-password).

**You have no access to this agent.** Ihr Administrator hat ihn Ihnen noch nicht freigegeben.

**Office is busy (a cell may be in edit mode, or a dialog is open). Finish that and try again.** Verlassen Sie die Zelle oder schließen Sie das Dialogfeld, und fragen Sie erneut.

**The agent could not start**, mit einer Begründung. Wählen Sie **Try again**. Wenn es weiterhin fehlschlägt, teilen Sie Ihrer IT-Abteilung mit, was in der Begründung steht.
