# Chat {#chat}

Im Chat sprechen Sie mit den Modellen Ihrer Organisation. Diese Seite zeigt, wie Sie einen Chat beginnen, ein Modell wählen, eine Antwort stoppen und neu anfordern und Ihre Chats wiederfinden.

## Einen Chat beginnen {#start-a-chat}

1. Wählen Sie in der Seitenleiste **Neuer Chat**.
2. Wählen Sie in der Auswahl **Modell** neben dem Nachrichtenfeld ein Modell. Ein Modell ist bereits ausgewählt: das erste der Liste oder das, das Sie zuletzt gewählt haben.
3. Geben Sie Ihre Nachricht ein und drücken Sie die Eingabetaste. Umschalt+Eingabetaste beginnt eine neue Zeile.

Die Antwort erscheint, während sie geschrieben wird. Ein neuer Chat wird nach dem Anfang Ihrer ersten Nachricht benannt.

Um eine Datei hinzuzufügen, wählen Sie vor dem Senden die Büroklammer. Das Modell liest die Datei zusammen mit Ihrer Frage und kann sie für den Rest des Chats nutzen. Welche Dateien Sie hinzufügen können und welche Grenzen gelten, steht unter [Wissen](knowledge.md).

## Ein Modell wählen {#choose-a-model}

Die Auswahl **Modell** listet die Modelle auf, die Ihr Administrator anbietet. Sie können mitten in einem Chat das Modell wechseln: Die nächste Antwort kommt von dem jetzt gewählten Modell, und es sieht das bisherige Gespräch.

![Die geöffnete Auswahl „Modell“ mit drei Modellen zur Wahl](../images/use/chat-model.png)

Manche Modelle laufen auf den eigenen Maschinen Ihrer Organisation, andere bei einem externen Provider. Der Name in der Liste verrät nicht immer, was davon zutrifft; fragen Sie Ihren Administrator, wenn es für das, was Sie schreiben, eine Rolle spielt. Siehe [Ihre Daten und Datenschutz](privacy.md).

## Während eine Antwort geschrieben wird {#while-a-reply-is-written}

- **Antwort stoppen.** Die Senden-Schaltfläche wird zu einem Quadrat, solange die Antwort geschrieben wird. Wählen Sie sie, um zu stoppen. Was bereits geschrieben ist, bleibt stehen und wird als **Angehalten** markiert.
- **Frühere Nachrichten lesen.** Der Chat folgt der Antwort nach unten. Wenn Sie nach oben scrollen, hört er auf zu folgen, sodass Sie in Ruhe lesen können. Die runde Schaltfläche mit dem Pfeil (**Zur neuesten Nachricht**) bringt Sie wieder nach unten.
- **Dem Modell beim Nachdenken zusehen.** Manche Modelle denken nach, bevor sie antworten. Sie sehen **Denkt nach…** mit den letzten Zeilen ihrer Gedanken; sobald die Antwort beginnt, wird das zu **N s nachgedacht** zusammengeklappt, was Sie zum Lesen auswählen können. Das Nachdenken wird nicht gespeichert: Wenn Sie den Chat später wieder öffnen, sehen Sie die Antwort ohne es.

## Den Chat verlassen, während eine Antwort geschrieben wird {#leave-while-a-reply-is-written}

Eine Antwort wird weiter geschrieben, wenn Sie einen anderen Chat öffnen, zu einer anderen Seite wechseln, die Seite neu laden oder den Tab schließen. Kehren Sie zum Chat zurück, zeigt er die Antwort von Anfang an. Ein Chat, dessen Antwort noch geschrieben wird, hat in der Seitenleiste ein kleines Ladesymbol.

- Eine Antwort pro Chat zur selben Zeit: Senden Sie die nächste Nachricht, wenn die Antwort fertig ist.
- Antworten in bis zu drei Chats gleichzeitig, sofern Ihr Administrator keine andere Zahl festgelegt hat. Eine vierte wird abgelehnt, bis eine davon fertig ist; wählen Sie dann **Erneut versuchen**.

## Wenn eine Antwort fehlschlägt {#when-a-reply-fails}

Eine fehlgeschlagene Antwort bleibt mit dem Grund im Chat stehen. Wählen Sie **Erneut versuchen** unter der letzten Antwort des Chats, um mit dem jetzt gewählten Modell erneut zu fragen; die neue Antwort tritt an ihre Stelle. Wenn Sie die letzte Antwort gestoppt haben, steht **Erneut versuchen** ebenfalls daneben.

Eine fehlgeschlagene Antwort wird mit Ihren späteren Nachrichten nicht an das Modell gesendet. Eine gestoppte Antwort schon.

## Details einer Antwort {#details-of-a-reply}

Unter jeder Antwort stehen:

- wie lange sie gedauert hat, bei einer Antwort, die geschrieben wurde, während der Chat bei Ihnen offen war;
- ein Feld mit ihrer Größe in Tokens (den Einheiten, in denen Modelle Text zählen). Zeigen Sie mit der Maus darauf, um Prompt, Antwort und Summe zu sehen;
- ein ⓘ: Zeigen Sie darauf, um zu sehen, welches Modell geantwortet hat.

Um eine Antwort anderswo zu verwenden, markieren Sie ihren Text und kopieren Sie ihn.

## Titel, Projekte und Suche {#titles-projects-and-search}

- **Einen Chat umbenennen:** Wählen Sie den Stift neben seinem Titel in der oberen Leiste, geben Sie den neuen Titel ein und drücken Sie die Eingabetaste (Esc bricht ab).
- **Projekte** fassen Chats zusammen. Wählen Sie **Neues Projekt** neben der Überschrift **Projekte** und geben Sie ihm einen Namen. Die **Projekteinstellungen** eines Projekts enthalten eine Beschreibung und einen **System-Prompt**: Anweisungen, die jeder Nachricht in den Chats des Projekts vorangestellt werden, z. B. der Kontext, eine Rolle oder Regeln für diese Arbeit.
- **Einen Chat in ein Projekt verschieben:** Ziehen Sie ihn auf das Projekt, oder verwenden Sie **In Projekt verschieben**. Wählen Sie **Kein Projekt**, um ihn wieder herauszunehmen.
- **Chats durchsuchen** findet Chats schon während der Eingabe anhand ihres Titels. Was in den Chats geschrieben wurde, durchsucht es nicht.
- Die Seitenleiste listet Ihre neuesten Chats danach, wann Sie sie genutzt haben (**Heute**, **Gestern**, **Letzte 7 Tage**, **Letzte 30 Tage**, **Älter**). **Mehr anzeigen** lädt ältere.
- Der Pfeil unten in der Seitenleiste (**Einklappen**) klappt sie zu einem schmalen Streifen zusammen; das Logo öffnet sie wieder.

![„Chats durchsuchen“ mit einem eingegebenen Wort: Die Seitenleiste listet die drei Chats, deren Titel passt, und neben dem Titel des geöffneten Chats steht der Stift](../images/use/chat-search.png)

Ihre Chats gehören Ihnen: Aus dem Chat heraus lässt sich ein Chat nicht mit Kollegen teilen.

!!! warning "Löschen lässt sich nicht rückgängig machen"
    **Löschen** bei einem Chat entfernt ihn sofort, ohne Rückfrage. Wenn Sie ein Projekt löschen, werden alle Chats darin gelöscht, ebenfalls ohne Rückfrage. Beides lässt sich im Chat nicht rückgängig machen. Was mit gelöschten Chats auf dem Server geschieht, steht unter [Ihre Daten und Datenschutz](privacy.md#deleting).

## Auf dem Smartphone {#on-a-phone}

Auf einem schmalen Bildschirm öffnen Sie die Seitenleiste mit der runden Menü-Schaltfläche in einer unteren Ecke oder mit dem Logo in der oberen Leiste. Mit **Menü-Schaltfläche** (**Links** oder **Rechts**) in der Seitenleiste legen Sie die Schaltfläche auf die Seite Ihres Daumens. Auf dem Smartphone beginnt die Eingabetaste eine neue Zeile, die Senden-Schaltfläche sendet, und die Aktionen eines Chats (**In Projekt verschieben**, **Löschen**) liegen hinter seiner Schaltfläche ⋮.

## Wenn es nicht funktioniert {#when-it-does-not-work}

**Keine Modelle** neben dem Nachrichtenfeld. Ihnen wird noch kein Modell angeboten. Wenden Sie sich an Ihren Administrator.

**A reply is still being written in this chat. Wait for it, or stop it.** Warten Sie auf die Antwort oder stoppen Sie sie, und senden Sie dann erneut.

**Anfragelimit erreicht** mit **Replies are being written in 3 of your chats. Wait for one to finish.** Sie haben die Zahl der Antworten erreicht, die gleichzeitig geschrieben werden können. Wählen Sie **Erneut versuchen**, sobald eine fertig ist.

**Anfragelimit erreicht** mit **Erneut versuchen in N s.** Ihre Organisation begrenzt, wie viele Anfragen in einem bestimmten Zeitraum gesendet werden können. Warten Sie die angezeigte Zeit ab und wählen Sie dann **Erneut versuchen**.

**The model did not start its reply within … s; it may be busy.** Viele Personen nutzen gerade dieses Modell. Wählen Sie **Erneut versuchen**, versuchen Sie es etwas später noch einmal oder wählen Sie ein anderes Modell.

**Der Server wurde neu gestartet, während diese Antwort geschrieben wurde.** Wählen Sie **Erneut versuchen**.
