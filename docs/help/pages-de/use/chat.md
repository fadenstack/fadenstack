# Chat {#chat}

Sprechen Sie mit den Modellen Ihrer Organisation: einen Chat beginnen, ein Modell wählen und Ihre Chats wiederfinden.

![Der Chat: links Ihre Chats und Projekte, in der Mitte Vorschläge, unten das Nachrichtenfeld](../images/use/chat.png)

## Einen Chat beginnen {#start-a-chat}

![Das Nachrichtenfeld: links Tools, Skills und die Büroklammer, rechts die Auswahl „Modell“ und die Senden-Schaltfläche](../images/use/chat-composer.png)

1. Wählen Sie in der Seitenleiste **Neuer Chat**.
2. Wählen Sie in der Auswahl **Modell** ein Modell.
3. Geben Sie Ihre Nachricht ein und drücken Sie die Eingabetaste. Umschalt+Eingabetaste beginnt eine neue Zeile.

Die Antwort erscheint, während sie geschrieben wird. Um eine Datei hinzuzufügen, wählen Sie vor dem Senden die Büroklammer.

??? info "Mehr zum Beginnen eines Chats"
    Ein Modell ist bereits ausgewählt: das erste der Liste oder das, das Sie zuletzt gewählt haben. Ein neuer Chat wird nach dem Anfang Ihrer ersten Nachricht benannt. Das Modell liest eine angehängte Datei zusammen mit Ihrer Frage und kann sie für den Rest des Chats nutzen. Welche Dateien Sie hinzufügen können und welche Grenzen gelten, steht unter [Wissen](knowledge.md).

## Ein Modell wählen {#choose-a-model}

![Die geöffnete Auswahl „Modell“ mit den Modellen zur Wahl](../images/use/chat-model.png)

Sie können mitten in einem Chat das Modell wechseln: Die nächste Antwort kommt von dem jetzt gewählten Modell, und es sieht das bisherige Gespräch.

??? info "Wo läuft ein Modell?"
    Manche Modelle laufen auf den eigenen Maschinen Ihrer Organisation, andere bei einem externen Provider. Der Name in der Liste verrät nicht immer, was davon zutrifft; fragen Sie Ihren Administrator, wenn es für das, was Sie schreiben, eine Rolle spielt. Siehe [Ihre Daten und Datenschutz](privacy.md).

## Während eine Antwort geschrieben wird {#while-a-reply-is-written}

| Um | So geht's |
|---|---|
| die Antwort zu stoppen | Wählen Sie das Quadrat, das die Senden-Schaltfläche ersetzt. Was geschrieben ist, bleibt stehen und wird als **Angehalten** markiert. |
| frühere Nachrichten zu lesen | Scrollen Sie nach oben; der Chat folgt der Antwort dann nicht mehr. **Zur neuesten Nachricht** (die runde Schaltfläche mit dem Pfeil) bringt Sie wieder nach unten. |
| zu sehen, wie das Modell nachgedacht hat | Manche Modelle denken zuerst nach: **Denkt nach…** zeigt die letzten Zeilen und wird dann zu **N s nachgedacht** zusammengeklappt, was Sie auswählen können. Es wird nicht mit dem Chat gespeichert. |

## Den Chat verlassen, während eine Antwort geschrieben wird {#leave-while-a-reply-is-written}

Das geht: Die Antwort wird weiter geschrieben, wenn Sie einen anderen Chat öffnen, zu einer anderen Seite wechseln, die Seite neu laden oder den Tab schließen. Kehren Sie zurück, zeigt der Chat die Antwort von Anfang an. Ein Chat, dessen Antwort noch geschrieben wird, hat in der Seitenleiste ein kleines Ladesymbol.

??? info "Wie viele Antworten gleichzeitig"
    - Eine Antwort pro Chat zur selben Zeit: Senden Sie die nächste Nachricht, wenn die Antwort fertig ist.
    - Antworten in bis zu drei Chats gleichzeitig, sofern Ihr Administrator keine andere Zahl festgelegt hat. Eine vierte wird abgelehnt, bis eine davon fertig ist; wählen Sie dann **Erneut versuchen**.

## Wenn eine Antwort fehlschlägt {#when-a-reply-fails}

Eine fehlgeschlagene Antwort bleibt mit dem Grund im Chat stehen. Wählen Sie **Erneut versuchen** unter der letzten Antwort des Chats, um mit dem jetzt gewählten Modell erneut zu fragen; die neue Antwort tritt an ihre Stelle.

??? info "Was das Modell danach sieht"
    Eine fehlgeschlagene Antwort wird mit Ihren späteren Nachrichten nicht an das Modell gesendet. Eine gestoppte Antwort schon. Wenn Sie die letzte Antwort gestoppt haben, steht **Erneut versuchen** ebenfalls daneben.

## Details einer Antwort {#details-of-a-reply}

![Eine Antwort mit ihrer Größe in Tokens und dem ⓘ, das das Modell nennt, unter der Antwort](../images/use/chat-reply-details.png)

Unter jeder Antwort: wie lange sie gedauert hat (wenn der Chat bei Ihnen offen war), ihre Größe in Tokens (zeigen Sie auf das Feld für Prompt, Antwort und Summe) und ein ⓘ, das das Modell nennt. Um eine Antwort anderswo zu verwenden, markieren Sie ihren Text und kopieren Sie ihn.

## Titel, Projekte und Suche {#titles-projects-and-search}

![Die Seitenleiste: „Neuer Chat“, „Chats durchsuchen“, ein Projekt mit Einstellungen und Papierkorb, und die Chats nach Tagen](../images/use/chat-sidebar.png)

- **Einen Chat umbenennen:** Wählen Sie den Stift neben seinem Titel in der oberen Leiste.
- **Projekte** fassen Chats zusammen. Wählen Sie **Neues Projekt** neben **Projekte**. Die **Projekteinstellungen** eines Projekts enthalten eine Beschreibung und einen **System-Prompt**: Anweisungen, die jeder Nachricht in seinen Chats vorangestellt werden.
- **Einen Chat in ein Projekt verschieben:** Ziehen Sie ihn auf das Projekt, oder verwenden Sie **In Projekt verschieben**.
- **Chats durchsuchen** findet Chats schon während der Eingabe anhand ihres Titels, nicht anhand dessen, was darin geschrieben wurde.

![„Chats durchsuchen“ mit einem eingegebenen Wort: Die Seitenleiste listet die Chats, deren Titel passt](../images/use/chat-search.png)

??? info "Mehr zur Seitenleiste"
    Die Seitenleiste listet Ihre neuesten Chats danach, wann Sie sie genutzt haben (**Heute**, **Gestern**, **Letzte 7 Tage**, **Letzte 30 Tage**, **Älter**); **Mehr anzeigen** lädt ältere. **Einklappen** (der Pfeil unten) klappt sie zu einem schmalen Streifen zusammen, das Logo öffnet sie wieder. **Kein Projekt** unter **In Projekt verschieben** nimmt einen Chat aus seinem Projekt. Ihre Chats gehören Ihnen: Aus dem Chat heraus lässt sich ein Chat nicht mit Kollegen teilen.

!!! warning "Löschen lässt sich nicht rückgängig machen"
    **Löschen** bei einem Chat entfernt ihn sofort, ohne Rückfrage. Wenn Sie ein Projekt löschen, werden alle Chats darin gelöscht, ebenfalls ohne Rückfrage. Was mit gelöschten Chats auf dem Server geschieht, steht unter [Ihre Daten und Datenschutz](privacy.md#deleting).

## Auf dem Smartphone {#on-a-phone}

![Der Chat auf dem Smartphone, mit der runden Menü-Schaltfläche in der unteren Ecke](../images/use/chat-phone.png){ width="280" }

Die Seitenleiste öffnen Sie mit der runden Menü-Schaltfläche in einer unteren Ecke oder mit dem Logo. Mit **Menü-Schaltfläche** (**Links** oder **Rechts**) in der Seitenleiste legen Sie die Schaltfläche auf die Seite Ihres Daumens. Auf dem Smartphone beginnt die Eingabetaste eine neue Zeile, die Senden-Schaltfläche sendet, und die Aktionen eines Chats (**In Projekt verschieben**, **Löschen**) liegen hinter seiner Schaltfläche ⋮.

## Wenn es nicht funktioniert {#when-it-does-not-work}

| Sie sehen | Was Sie tun |
|---|---|
| **Keine Modelle** neben dem Nachrichtenfeld | Ihnen wird noch kein Modell angeboten. Wenden Sie sich an Ihren Administrator. |
| **A reply is still being written in this chat. Wait for it, or stop it.** | Warten Sie auf die Antwort oder stoppen Sie sie, und senden Sie dann erneut. |
| **Anfragelimit erreicht** mit **Replies are being written in 3 of your chats. Wait for one to finish.** | Sie haben die Zahl der Antworten erreicht, die gleichzeitig geschrieben werden können. Wählen Sie **Erneut versuchen**, sobald eine fertig ist. |
| **Anfragelimit erreicht** mit **Erneut versuchen in N s.** | Ihre Organisation begrenzt, wie viele Anfragen in einem Zeitraum gesendet werden können. Warten Sie, und wählen Sie dann **Erneut versuchen**. |
| **The model did not start its reply within … s; it may be busy.** | Viele Personen nutzen gerade dieses Modell. Wählen Sie **Erneut versuchen**, versuchen Sie es später oder wählen Sie ein anderes Modell. |
| **Der Server wurde neu gestartet, während diese Antwort geschrieben wurde.** | Wählen Sie **Erneut versuchen**. |
